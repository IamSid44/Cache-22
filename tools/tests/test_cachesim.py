"""Unit tests for the cache model (tools/cachesim.py)."""

import contextlib
import io
import os
import random
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import cachesim  # noqa: E402
from cachesim import Cache, FIFO, LRU, PLRU, RANDOM, check_log  # noqa: E402

LINE = 32
A, B, C, D, E = (LINE * n for n in range(5))    # five lines of a one-set cache


def pattern(cache, addresses):
    """Hits and misses of a sequence of reads, as a string of h and m."""
    return "".join("h" if cache.access(addr) else "m" for addr in addresses)


class ReplacementTests(unittest.TestCase):
    def test_lru_evicts_the_least_recently_used(self):
        cache = Cache(sets=1, ways=2)
        # A B A C: C replaces B, because A was used after B.
        self.assertEqual(pattern(cache, [A, B, A, C, A, B]), "mmhmhm")

    def test_fifo_ignores_use(self):
        cache = Cache(sets=1, ways=2, repl=FIFO)
        # A B A C: C replaces A, the line that came in first, although it was
        # just used.
        self.assertEqual(pattern(cache, [A, B, A, C, A, B]), "mmhmmm")

    def test_pseudo_lru_tree(self):
        cache = Cache(sets=1, ways=4, repl=PLRU)
        # Empty ways are filled in order.
        self.assertEqual(pattern(cache, [A, B, C, D]), "mmmm")
        self.assertEqual(cache.line[0], [0, 1, 2, 3])
        self.assertTrue(cache.access(A))
        self.assertFalse(cache.access(E))
        self.assertEqual(cache.line[0], [0, 1, 4, 3])
        # True LRU would have replaced B. That is the approximation.
        exact = Cache(sets=1, ways=4)
        pattern(exact, [A, B, C, D, A, E])
        self.assertEqual(exact.line[0].count(1), 0)

    def test_pseudo_lru_equals_lru_for_two_ways(self):
        rng = random.Random(1)
        addresses = [LINE * rng.randrange(6) for _ in range(500)]
        self.assertEqual(pattern(Cache(sets=1, ways=2, repl=PLRU), addresses),
                         pattern(Cache(sets=1, ways=2, repl=LRU), addresses))

    def test_random_fills_empty_ways_first_and_is_repeatable(self):
        first = Cache(sets=1, ways=4, repl=RANDOM)
        self.assertEqual(pattern(first, [A, B, C, D, A, B, C, D]), "mmmmhhhh")
        rng = random.Random(2)
        addresses = [LINE * rng.randrange(9) for _ in range(400)]
        self.assertEqual(pattern(Cache(sets=1, ways=4, repl=RANDOM), addresses),
                         pattern(Cache(sets=1, ways=4, repl=RANDOM), addresses))

    def test_random_sequence_is_the_shift_register(self):
        cache = Cache(repl=RANDOM)
        values = []
        for _ in range(5):
            values.append(cache.lfsr)
            cache.step_random()
        # x^16 + x^14 + x^13 + x^11 + 1, starting at ACE1, shifting left.
        self.assertEqual(values, [0xACE1, 0x59C3, 0xB387, 0x670F, 0xCE1E])

    def test_lecture_example_five_lines_in_four_ways(self):
        """Random replacement does much better."""
        cycle = [A, B, C, D, E] * 400
        for policy in (LRU, FIFO):
            self.assertNotIn("h", pattern(Cache(sets=1, ways=4, repl=policy), cycle))
        hits = pattern(Cache(sets=1, ways=4, repl=RANDOM), cycle).count("h")
        self.assertGreater(hits / len(cycle), 0.5)
        self.assertLess(hits / len(cycle), 0.7)

    def test_hot_lines_among_cold_ones(self):
        """The lru_fifo kernel's pattern: three hot lines and a stream of
        cold ones in four ways."""
        addresses = []
        for round_number in range(400):
            addresses += [A, B, C, LINE * (10 + round_number % 32)]
        lru = pattern(Cache(sets=1, ways=4, repl=LRU), addresses)
        fifo = pattern(Cache(sets=1, ways=4, repl=FIFO), addresses)
        self.assertAlmostEqual(lru.count("m") / len(addresses), 0.25, places=2)
        self.assertAlmostEqual(fifo.count("m") / len(addresses), 0.625, places=2)

    def test_a_larger_lru_cache_never_misses_more(self):
        rng = random.Random(3)
        addresses = [LINE * rng.randrange(40) for _ in range(3000)]
        misses = [pattern(Cache(sets=1, ways=ways), addresses).count("m")
                  for ways in range(1, 12)]
        self.assertEqual(misses, sorted(misses, reverse=True))

    def test_sets_are_independent(self):
        cache = Cache(sets=4, ways=1)
        # Lines 0 and 4 share set 0; line 1 is in set 1 and is not disturbed.
        self.assertEqual(pattern(cache, [0, LINE, 4 * LINE, LINE, 0]), "mmmhm")


class WritePolicyTests(unittest.TestCase):
    def test_write_back_writes_when_the_line_leaves(self):
        cache = Cache(sets=1, ways=1)
        cache.access(A, write=True)
        cache.access(A, write=True)
        self.assertEqual(cache.count["mem_write"], 0)
        cache.access(B)
        self.assertEqual((cache.count["writeback"], cache.count["mem_write"]), (1, 1))
        cache.access(A)                         # B was clean: nothing to write
        self.assertEqual(cache.count["mem_write"], 1)

    def test_write_through_writes_every_store(self):
        cache = Cache(sets=1, ways=1, write_hit=1)
        for _ in range(5):
            cache.access(A, write=True)
        cache.access(B)
        self.assertEqual((cache.count["writeback"], cache.count["mem_write"]), (0, 5))

    def test_no_allocate_leaves_the_line_out(self):
        cache = Cache(sets=1, ways=1, write_miss=1)
        self.assertFalse(cache.access(A, write=True))
        self.assertFalse(cache.access(A, write=True))
        self.assertEqual((cache.count["mem_read"], cache.count["mem_write"]), (0, 2))
        self.assertFalse(cache.access(A))       # a read brings it in
        self.assertTrue(cache.access(A, write=True))
        self.assertEqual(cache.count["mem_read"], 1)


class VictimBufferTests(unittest.TestCase):
    def test_it_catches_what_the_cache_evicts(self):
        cache = Cache(sets=1, ways=1, victim_entries=1)
        # Two lines in a cache of one: after the first two accesses each miss
        # finds its line in the buffer and nothing is read from memory.
        self.assertEqual(pattern(cache, [A, B, A, B, A, B]), "mmmmmm")
        self.assertEqual((cache.count["mem_read"], cache.count["vb_hit"]), (2, 4))
        self.assertEqual((cache.line[0], cache.vb), ([1], [0]))

    def test_lecture_example_with_one_more_place(self):
        """Five lines in four ways never hit under LRU; one entry beside the
        cache is the fifth place, and memory is read five times in all."""
        cache = Cache(sets=1, ways=4, victim_entries=1)
        pattern(cache, [A, B, C, D, E] * 100)
        self.assertEqual((cache.count["mem_read"], cache.count["vb_hit"]), (5, 495))

    def test_the_dirty_bit_travels_with_the_line(self):
        cache = Cache(sets=1, ways=1, victim_entries=1)
        cache.access(A, write=True)
        cache.access(B)                     # A goes to the buffer, dirty, unwritten
        self.assertEqual((cache.vb, cache.vb_dirty, cache.count["mem_write"]), ([0], [True], 0))
        cache.access(A)                     # and comes back dirty; B takes its entry
        self.assertEqual((cache.dirty[0], cache.vb, cache.vb_dirty), ([True], [1], [False]))
        cache.access(C)                     # A to the buffer, B leaves clean
        self.assertEqual(cache.count["mem_write"], 0)
        cache.access(D)                     # C to the buffer, A leaves dirty
        self.assertEqual((cache.count["writeback"], cache.count["mem_write"]), (1, 1))

    def test_entries_are_replaced_in_turn(self):
        cache = Cache(sets=1, ways=1, victim_entries=2)
        pattern(cache, [A, B, C])           # A and B fill the two entries
        self.assertEqual(cache.vb, [0, 1])
        cache.access(D)                     # C replaces the first one
        self.assertEqual(cache.vb, [2, 1])
        cache.access(E)                     # D the second
        self.assertEqual(cache.vb, [2, 3])
        cache.access(C)                     # C comes out, E takes its entry
        self.assertEqual((cache.line[0], cache.vb), ([2], [4, 3]))

    def test_a_store_to_a_line_in_the_buffer_swaps_it_in(self):
        cache = Cache(sets=1, ways=1, write_miss=1, victim_entries=1)
        pattern(cache, [A, B])
        self.assertFalse(cache.access(A, write=True))   # no-allocate, but it is here
        self.assertEqual((cache.line[0], cache.dirty[0]), ([0], [True]))
        self.assertEqual((cache.count["mem_write"], cache.count["vb_hit"]), (0, 1))
        self.assertFalse(cache.access(C, write=True))   # really absent: round the cache
        self.assertEqual(cache.count["mem_write"], 1)

    def test_no_buffer_and_inclusion_together(self):
        with self.assertRaises(ValueError):
            Cache(victim_entries=1, inclusion=1)


class LogTests(unittest.TestCase):
    """check_log on logs written out by hand, for a cache of one set."""

    def run_log(self, text, must_drain=True, **params):
        params.setdefault("sets", 1)
        return check_log(text.strip().splitlines(), Cache(**params), must_drain)

    def expect_error(self, fragment, text, **params):
        error, _ = self.run_log(text, **params)
        self.assertIsNotNone(error, "the log was accepted")
        self.assertIn(fragment, error)

    GOOD = """
        F r 0 - -
        R 0
        I 0
        H r 0 3
        H w 8 3
        F r 20 - -
        R 20
        I 20
        H r 20 3
        F p 40 0 d
        w 0
        M 0 ffffffff
        R 40
        I 40
        H r 40 0
    """

    def test_a_correct_log_is_accepted(self):
        error, count = self.run_log(self.GOOD, ways=2)
        self.assertIsNone(error)
        self.assertEqual(count, dict(access=4, miss=2, writeback=1, evict=1, pf_issued=1,
                                     pf_useful=1, mem_read=3, mem_write=1, vb_hit=0,
                                     inval=0, store_miss=0))

    def test_wrong_victim(self):
        # LRU replaces line 0 (line 20 was used later); the log says line 20.
        self.expect_error("victim", self.GOOD.replace("F p 40 0 d", "F p 40 20 c"), ways=2)

    def test_victim_reported_clean_although_written(self):
        self.expect_error("victim", self.GOOD.replace("F p 40 0 d", "F p 40 0 c"), ways=2)

    def test_fifo_expects_a_different_victim(self):
        # Under FIFO the same log is right: line 0 came in first.
        self.assertIsNone(self.run_log(self.GOOD, ways=2, repl=FIFO)[0])
        # But with line 0 used last, LRU keeps it and FIFO does not.
        text = """
            F r 0 - -
            R 0
            I 0
            F r 20 - -
            R 20
            I 20
            H r 0 3
            F r 40 20 c
            R 40
            I 40
        """
        self.assertIsNone(self.run_log(text, ways=2)[0])
        self.expect_error("victim", text, ways=2, repl=FIFO)

    def test_access_to_an_absent_line(self):
        self.expect_error("not in the cache", "H r 0 3")

    def test_line_brought_in_twice(self):
        self.expect_error("already in the cache", """
            F r 0 - -
            R 0
            I 0
            F r 0 - -
        """)

    def test_installed_without_being_read(self):
        self.expect_error("not read from memory", """
            F r 0 - -
            I 0
        """)

    def test_write_lost(self):
        self.expect_error("never reached memory", """
            F w 0 - -
            R 0
            I 0
            H w 0 3
            F r 20 0 d
            R 20
            I 20
        """)

    def test_read_overtakes_a_write_to_the_same_line(self):
        # Line 0 is evicted dirty into the write buffer and wanted again before
        # the write has reached memory.
        start = """
            F w 0 - -
            R 0
            I 0
            H w 0 3
            F r 20 0 d
            R 20
            I 20
            F r 0 20 c
        """
        self.expect_error("still on its way", start + "R 0", wbuf_entries=2)
        self.assertIsNone(self.run_log(start + "w 0\nM 0 ffffffff\nR 0\nI 0",
                                       wbuf_entries=2)[0])

    def test_writes_out_of_order(self):
        self.expect_error("oldest one waiting", """
            N 0 3
            N 20 3
            w 20
        """, write_miss=1, wbuf_entries=2)

    def test_too_many_writes_waiting(self):
        self.expect_error("room for", """
            N 0 3
            N 20 3
            N 40 3
        """, write_miss=1, wbuf_entries=2)

    def test_write_through_strobes(self):
        text = """
            F w 0 - -
            R 0
            I 0
            H w 4 2
            M 0 f0
        """
        self.assertIsNone(self.run_log(text, write_hit=1)[0])
        self.expect_error("expected line 0 bytes f0", text.replace("M 0 f0", "M 0 ff"),
                          write_hit=1)

    def test_merging(self):
        # Two stores to one line: one write with merging, two without.
        merged = """
            N 0 3
            N 8 3
            w 0
            M 0 ffff
        """
        self.assertIsNone(self.run_log(merged, write_miss=1, wbuf_entries=2, wbuf_merge=1)[0])
        self.expect_error("expected line 0 bytes ff", merged, write_miss=1, wbuf_entries=2)
        # A write that has been sent takes no more stores.
        sent = """
            N 0 3
            w 0
            N 8 3
            M 0 ff
            w 0
            M 0 ff00
        """
        self.assertIsNone(self.run_log(sent, write_miss=1, wbuf_entries=2, wbuf_merge=1)[0])

    def test_allocation_when_stores_do_not_allocate(self):
        self.expect_error("do not allocate", "F w 0 - -", write_miss=1)
        self.expect_error("stores allocate", "N 0 3")

    # ---- victim buffer ----
    VICTIM = """
        F r 0 - - - -
        R 0
        I 0
        F r 20 0 c - -
        R 20
        I 20
        S r 0 20 c
        H r 0 3
        H w 0 3
        F r 40 0 d 20 c
        R 40
        I 40
        F r 60 40 c 0 d
        w 0
        M 0 ffffffff
        R 60
        I 60
    """

    def test_victim_buffer_log(self):
        error, count = self.run_log(self.VICTIM, victim_entries=1)
        self.assertIsNone(error)
        self.assertEqual((count["miss"], count["vb_hit"], count["evict"], count["writeback"],
                          count["mem_read"]), (5, 1, 4, 1, 4))

    def test_victim_buffer_mistakes(self):
        # The line pushed out of the buffer is the one that has been there
        # longest, with the dirtiness it had.
        self.expect_error("victim buffer", self.VICTIM.replace("F r 40 0 d 20 c", "F r 40 0 d - -"),
                          victim_entries=1)
        self.expect_error("victim buffer", self.VICTIM.replace("F r 60 40 c 0 d", "F r 60 40 c 0 c"),
                          victim_entries=1)
        # A line that is in the buffer is swapped in, not fetched.
        self.expect_error("in the victim buffer", self.VICTIM.replace("S r 0 20 c", "F r 0 20 c - -"),
                          victim_entries=1)
        # A line that is not in the buffer cannot come out of it.
        self.expect_error("not in the victim buffer", self.VICTIM.replace("S r 0 20 c", "S r 80 20 c"),
                          victim_entries=1)
        # The dirty line that left has to reach memory.
        self.expect_error("never reached memory",
                          self.VICTIM.replace("w 0\n        M 0 ffffffff\n", ""), victim_entries=1)
        # A store does not go round a line that the buffer holds.
        self.expect_error("in the victim buffer", """
            F r 0 - - - -
            R 0
            I 0
            F r 20 0 c - -
            R 20
            I 20
            N 0 3
        """, victim_entries=1, write_miss=1)

    # ---- two levels ----
    def test_line_given_up(self):
        start = """
            F w 0 - -
            R 0
            I 0
            H w 0 3
        """
        error, count = self.run_log(start + "X 0 d")
        self.assertIsNone(error)
        self.assertEqual(count["inval"], 1)
        # The line is gone: nothing completes on it, and nothing is written back
        # (its content went down with it).
        self.expect_error("not in the cache", start + "X 0 d\nH r 0 3")
        self.expect_error("given up as clean", start + "X 0 c")
        self.expect_error("not in the cache", start + "X 20 c")
        # Its place is empty and is used first, before any line is evicted.
        self.assertIsNone(self.run_log("""
            F r 0 - -
            R 0
            I 0
            F r 20 - -
            R 20
            I 20
            X 0 c
            F r 40 - -
            R 40
            I 40
            F r 60 20 c
        """, ways=2)[0])

    def test_line_given_up_out_of_the_victim_buffer(self):
        self.assertIsNone(self.run_log("""
            F w 0 - - - -
            R 0
            I 0
            H w 0 3
            F r 20 0 d - -
            R 20
            I 20
            X 0 d
            F r 40 20 c - -
        """, victim_entries=1)[0])

    def test_store_whose_line_was_given_up_while_it_waited(self):
        # Write-through without a buffer: the store was already on its way to
        # memory when the line was taken, and ends without it.
        text = """
            F w 0 - -
            R 0
            I 0
            X 0 c
            N 0 3
            M 0 ff
        """
        self.assertIsNone(self.run_log(text, write_hit=1)[0])
        self.expect_error("stores allocate", text.replace("X 0 c\n", ""), write_hit=1)

    def test_inclusive_cache_takes_back_before_it_evicts(self):
        text = """
            F r 0 - -
            R 0
            I 0
            B 0 c
            F r 20 0 c
            R 20
            I 20
        """
        self.assertIsNone(self.run_log(text, inclusion=1)[0])
        self.expect_error("not taken back", text.replace("B 0 c\n", ""), inclusion=1)
        self.expect_error("not inclusive", text)
        # Taking back is no licence for later: the eviction follows at once.
        late = text.replace("B 0 c\n", "B 0 c\n            H r 0 /0\n")
        self.expect_error("not taken back", late, inclusion=1)
        # The line taken back must be the one evicted, and must be here.
        self.expect_error("not in the cache", text.replace("B 0 c", "B 40 c"), inclusion=1)

    def test_dirty_copy_from_above_is_written_back(self):
        # The line was clean here; the cache above hands down a dirty copy, so
        # it leaves dirty and has to reach memory.
        text = """
            F r 0 - -
            R 0
            I 0
            B 0 d
            F r 20 0 d
            w 0
            M 0 ffffffff
            R 20
            I 20
        """
        self.assertIsNone(self.run_log(text, inclusion=1)[0])
        self.expect_error("victim", text.replace("F r 20 0 d", "F r 20 0 c"), inclusion=1)
        # Even under write-through, where nothing else is ever dirty.
        self.assertIsNone(self.run_log(text, inclusion=1, write_hit=1)[0])

    def test_line_port_strobes(self):
        # A second-level cache is written a line at a time, with strobes.
        text = """
            F r 0 - -
            R 0
            I 0
            H w 0 /ff00
            M 0 ff00
            H r 0 /0
            N 20 /f
            M 20 f
        """
        self.assertIsNone(self.run_log(text, write_hit=1, write_miss=1)[0])
        self.expect_error("expected line 0 bytes ff00", text.replace("M 0 ff00", "M 0 ff"),
                          write_hit=1, write_miss=1)

    def test_command_line(self):
        path = os.path.join(ROOT, "build", "test_cachesim.log")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="ascii") as handle:
            handle.write("F r 0 - -\nR 0\nI 0\nH r 0 3\n")
        try:
            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(cachesim.main([path, "--sets", "1"]), 0)
                self.assertIn("agrees", output.getvalue())
                with open(path, "w", encoding="ascii") as handle:
                    handle.write("H r 0 3\n")
                self.assertEqual(cachesim.main([path]), 1)
        finally:
            os.remove(path)


if __name__ == "__main__":
    unittest.main()
