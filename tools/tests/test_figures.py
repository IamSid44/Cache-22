"""Unit tests for the figures (tools/figures.py)."""

import json
import os
import sys
import unittest
import xml.etree.ElementTree as ElementTree

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import figures  # noqa: E402


class FigureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = figures.load()
        cls.built = figures.build(cls.data)

    def test_every_figure_is_up_to_date(self):
        for name, svg in self.built.items():
            with open(os.path.join(figures.IMG, name), encoding="utf-8") as handle:
                self.assertEqual(svg, handle.read(), name)

    def test_every_figure_is_well_formed(self):
        for name, svg in self.built.items():
            root = ElementTree.fromstring(svg)
            self.assertTrue(root.tag.endswith("svg"), name)
            self.assertIn("aria-label", root.attrib, name)

    def test_the_data_file_is_in_its_own_format(self):
        with open(figures.DATA, encoding="utf-8") as handle:
            text = handle.read()
        self.assertEqual(figures.dump(self.data), text)
        self.assertEqual(json.loads(text), self.data)

    def test_the_planted_bugs_add_up(self):
        mutation = self.data["mutation"]
        self.assertEqual(sum(count for _, count in figures.BUG_GROUPS), mutation["total"])
        self.assertEqual(sum(mutation["seen"].values()), mutation["total"])
        self.assertEqual(self.data["history"][-1]["bugs"], mutation["total"])

    def test_the_history_ends_with_the_files_as_they_are(self):
        last = self.data["history"][-1]
        self.assertEqual(last["phase"], figures.LAST_PHASE)
        for folder, count in (("tests", "directed"), ("bench", "kernels")):
            names = os.listdir(os.path.join(ROOT, "sw", folder))
            self.assertEqual(sum(name.endswith(".S") for name in names), last[count])

    def test_every_kernel_has_every_run_the_charts_use(self):
        runs, kernels = self.data["bench"]["runs"], self.data["bench"]["kernels"]
        for kernel in kernels["first"]:
            for config in ("nocache", "dm", "dm-vb", "2way", "bp1", "bp2", "pf-next",
                           "pf-stride", "ooo", "ooo-32", "ss", "lru", "fifo", "random", "plru",
                           "wt", "wt-buf", "wt-merge", "wb-na-merge"):
                self.assertIn(f"{kernel}@{config}", runs)
        for kernel in kernels["all"]:
            for config in ("h-l1", "h-l2", "h-incl", "h-vb"):
                self.assertIn(f"{kernel}@{config}", runs)

    def test_every_figure_is_shown_somewhere(self):
        pages = [os.path.join(ROOT, "README.md")]
        pages += [os.path.join(ROOT, "docs", name) for name in os.listdir(
            os.path.join(ROOT, "docs")) if name.endswith(".md")]
        shown = ""
        for page in pages:
            with open(page, encoding="utf-8") as handle:
                shown += handle.read()
        for name in os.listdir(figures.IMG):
            self.assertIn(f"img/{name})", shown, name)


if __name__ == "__main__":
    unittest.main()
