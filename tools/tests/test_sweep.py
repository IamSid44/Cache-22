"""Unit tests for the design-space sweeps (tools/sweep.py) and the chart
writer (tools/svgplot.py)."""

import itertools
import json
import os
import random
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ElementTree

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import cachesim  # noqa: E402
import svgplot   # noqa: E402
import sweep     # noqa: E402


def random_trace(rng, length, lines, locality=0.6):
    """Line numbers with some reuse of recent lines, as programs have."""
    trace, recent = [], []
    for _ in range(length):
        if recent and rng.random() < locality:
            line = rng.choice(recent)
        else:
            line = rng.randrange(lines)
        trace.append(line)
        recent = (recent + [line])[-6:]
    return trace


def model_misses(trace, sets, ways, repl):
    return sweep.model_counters(((line * sweep.LINE, 0) for line in trace),
                                sets=sets, ways=ways, repl=repl)["miss"]


def fewest_misses(trace, ways):
    """The least number of misses any replacement can achieve in one set, by
    trying every choice of victim."""
    def search(index, held):
        if index == len(trace):
            return 0
        line = trace[index]
        if line in held:
            return search(index + 1, held)
        if len(held) < ways:
            return 1 + search(index + 1, held | {line})
        return 1 + min(search(index + 1, (held - {victim}) | {line}) for victim in held)
    return search(0, frozenset())


class SimulatorTests(unittest.TestCase):
    SHAPES = [(1, 1), (8, 1), (4, 2), (2, 4), (1, 8), (16, 2), (1, 32)]

    def test_fast_lru_equals_the_reference_model(self):
        rng = random.Random(1)
        for sets, ways in self.SHAPES:
            for _ in range(5):
                trace = random_trace(rng, 600, 4 * sets * ways + 3)
                self.assertEqual(sweep.lru_misses(trace, sets, ways),
                                 model_misses(trace, sets, ways, cachesim.LRU), (sets, ways))

    def test_optimal_is_never_worse_than_a_real_policy(self):
        rng = random.Random(2)
        for sets, ways in self.SHAPES:
            policies = [cachesim.LRU, cachesim.FIFO, cachesim.RANDOM]
            if ways & (ways - 1) == 0:
                policies.append(cachesim.PLRU)
            for _ in range(4):
                trace = random_trace(rng, 500, 3 * sets * ways + 2)
                best = sweep.opt_misses(trace, sets, ways)
                self.assertGreaterEqual(best, len(set(trace)))
                for repl in policies:
                    self.assertLessEqual(best, model_misses(trace, sets, ways, repl),
                                         (sets, ways, repl))

    def test_optimal_equals_exhaustive_search(self):
        rng = random.Random(3)
        for ways in (1, 2, 3):
            for _ in range(40):
                trace = [rng.randrange(ways + 3) for _ in range(11)]
                self.assertEqual(sweep.opt_misses(trace, 1, ways), fewest_misses(trace, ways),
                                 (ways, trace))

    def test_optimal_on_the_lecture_example(self):
        # Cycling over one line more than there are ways: LRU misses every time;
        # the optimal policy misses once per `ways` accesses.
        trace = list(itertools.islice(itertools.cycle(range(5)), 400))
        self.assertEqual(sweep.lru_misses(trace, 1, 4), 400)
        self.assertEqual(sweep.opt_misses(trace, 1, 4), 4 + 396 // 4)

    def test_a_larger_fully_associative_cache_never_misses_more(self):
        rng = random.Random(4)
        for _ in range(10):
            trace = random_trace(rng, 800, 70)
            for misses in (sweep.lru_misses, sweep.opt_misses):
                counts = [misses(trace, 1, ways) for ways in (1, 2, 4, 8, 16, 32, 64, 128)]
                self.assertEqual(counts, sorted(counts, reverse=True))
                self.assertEqual(counts[-1], len(set(trace)))

    def test_the_three_classes_add_up(self):
        rng = random.Random(5)
        for sets, ways in self.SHAPES:
            for _ in range(4):
                trace = random_trace(rng, 500, 3 * sets * ways + 2)
                compulsory, capacity, conflict = sweep.classify(trace, sets, ways)
                self.assertEqual(compulsory, len(set(trace)))
                self.assertGreaterEqual(capacity, 0)
                self.assertGreaterEqual(conflict, 0)
                self.assertEqual(compulsory + capacity + conflict,
                                 sweep.lru_misses(trace, sets, ways))

    def test_a_fully_associative_cache_that_holds_everything_only_misses_once_each(self):
        trace = random_trace(random.Random(6), 300, 20)
        self.assertEqual(sweep.classify(trace, 1, 32), (len(set(trace)), 0, 0))

    def test_shape(self):
        self.assertEqual(sweep.shape(1024, 4), (8, 4))
        self.assertEqual(sweep.shape(1024, 1), (32, 1))
        self.assertEqual(sweep.shape(1024, "full"), (1, 32))
        self.assertEqual(sweep.shape(1024, 4, 128), (2, 4))


class RecorderTests(unittest.TestCase):
    PROGRAM = """
        li   t0, 0x800
        li   t1, 7
        sd   t1, 0(t0)
        ld   t2, 0(t0)
        sb   t1, 9(t0)
        lbu  t3, 9(t0)
        li   t6, 0x10000000
        sb   t1, 0(t6)          # the console: not memory
        li   t6, 0x10000008
        sd   x0, 0(t6)          # halt: not memory
    """

    def test_data_accesses_in_program_order(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "p.S")
            with open(path, "w", encoding="ascii") as handle:
                handle.write(self.PROGRAM)
            sim = sweep.record(path)
        self.assertEqual(sim.data, [(0x800, 1), (0x800, 0), (0x809, 1), (0x809, 0)])
        self.assertEqual(len(sim.fetch), sim.retired)
        self.assertEqual(sim.fetch[:3], [0, 4, 8])

    def test_replaying_a_fetch_log_counts_its_misses(self):
        # The second line is abandoned (no access completes on it), and it still
        # evicted the first.
        log = ["F r 40 - -", "R 40", "I 40", "H r 44 2", "F r 1040 40 c", "R 1040", "I 1040",
               "F r 40 1040 c", "R 40", "I 40", "H r 40 2"]
        count = sweep.replay_fetches(log, sets=2, ways=1)
        self.assertEqual((count["miss"], count["evict"], count["mem_read"]), (3, 2, 3))

    def test_a_line_taken_back_is_missed_again(self):
        log = ["F r 40 - -", "R 40", "I 40", "H r 44 2", "X 40 c",
               "F r 40 - -", "R 40", "I 40", "H r 44 2", "H r 48 2"]
        count = sweep.replay_fetches(log, sets=2, ways=2)
        self.assertEqual((count["miss"], count["evict"], count["inval"]), (2, 0, 1))
        # A replay that did not know the line was taken back would see the
        # second fetch hit, and count one miss where the RTL had two.
        count = sweep.replay_fetches([text for text in log if not text.startswith("X")],
                                     sets=2, ways=2)
        self.assertEqual(count["miss"], 1)


class ChartTests(unittest.TestCase):
    def test_ticks(self):
        self.assertEqual(svgplot.nice_ticks(0.37), [0, 0.1, 0.2, 0.3, 0.4])
        self.assertEqual(svgplot.nice_ticks(1.0), [0, 0.25, 0.5, 0.75, 1.0])
        self.assertEqual(svgplot.nice_ticks(950), [0, 250, 500, 750, 1000])
        self.assertEqual(svgplot.nice_ticks(0), [0, 1])

    def figures(self):
        lines = svgplot.line_panel(["a", "b", "c"], [("one", "s1", [1.0, 2.5, None]),
                                                     ("two & <three>", "s2", [0.5, 0.25, 3])],
                                   title="lines", reference=1.0)
        bars = svgplot.bar_panel(["x", "y"], [("one", "o1", [1, 0]), ("two", "o5", [None, 4])],
                                 y_title="count", value_labels=True)
        stack = svgplot.hstack_panel(["p", "q"], [("one", "s1", [0.2, 0]), ("two", "s2", [0.1, 0.4])],
                                     x_max=1.0, x_title="share")
        rows = svgplot.hbar_panel([("first", "n", 1.0), ("second", "s1", 2.5, "note")],
                                  x_title="times")
        return svgplot.figure([lines, bars, stack, rows], cols=2, title="A <title>",
                              subtitle=["first line", "second"], note="small print",
                              legend=[("one", "s1", "line"), ("two & <three>", "s2", "box")])

    def test_a_figure_is_well_formed_and_repeatable(self):
        svg = self.figures()
        self.assertEqual(svg, self.figures())
        root = ElementTree.fromstring(svg)
        self.assertTrue(root.tag.endswith("svg"))
        self.assertIn("prefers-color-scheme:dark", svg)

    def test_every_colour_has_a_light_and_a_dark_value(self):
        self.assertEqual(set(svgplot.LIGHT), set(svgplot.DARK))


class DocumentTests(unittest.TestCase):
    """docs/SWEEPS.md, its charts and docs/data/sweeps.json belong together,
    and to the kernels as they are now."""

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(ROOT, "docs", "data", "sweeps.json"), encoding="utf-8") as handle:
            cls.data = json.load(handle)

    def read(self, *parts):
        with open(os.path.join(ROOT, *parts), encoding="utf-8") as handle:
            return handle.read()

    def test_the_document_and_charts_are_up_to_date(self):
        markdown, charts = sweep.document(self.data)
        self.assertEqual(markdown, self.read("docs", "SWEEPS.md"))
        for name, svg in charts.items():
            self.assertEqual(svg, self.read("docs", "img", name), name)
            self.assertIn(f"img/{name}", markdown)
            ElementTree.fromstring(svg)

    def test_the_data_file_is_in_its_own_format(self):
        self.assertEqual(sweep.dump(self.data), self.read("docs", "data", "sweeps.json"))

    def test_the_numbers_belong_to_the_kernels_as_they_are(self):
        folder = os.path.join(ROOT, "sw", "bench")
        now = {name[:-2]: sweep.source_hash(os.path.join(folder, name))
               for name in sorted(os.listdir(folder)) if name.endswith(".S")}
        self.assertEqual(now, self.data["inputs"])

    def test_the_model_reproduces_a_sample_of_the_numbers(self):
        for kernel in ("bsearch", "ws_walk"):
            sim = sweep.record(os.path.join(ROOT, "sw", "bench", kernel + ".S"))
            model = sweep.model_results({kernel: sim})
            for section, values in model.items():
                self.assertEqual(values[kernel], self.data["model"][section][kernel],
                                 (kernel, section))
            self.assertEqual(sweep.stream_facts({kernel: sim})[kernel],
                             self.data["streams"][kernel])

    def test_every_run_of_the_grid_is_there(self):
        kernels = set(self.data["inputs"])
        for config, runs in self.data["rtl"].items():
            self.assertEqual(set(runs), kernels, config)
        summary = self.data["agreement"]["summary"]
        self.assertEqual(summary["runs"], len(self.data["rtl"]) * len(kernels))


if __name__ == "__main__":
    unittest.main()
