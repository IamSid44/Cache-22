#!/usr/bin/env python3
"""cachesim - reference model of the cache's policies (rtl/common/cache.sv).

    python tools/cachesim.py build/x.log --sets 4 --ways 2 --repl 1
"""

import argparse
import sys

LRU, FIFO, RANDOM, PLRU = 0, 1, 2, 3
REPL_NAMES = {LRU: "LRU", FIFO: "FIFO", RANDOM: "random", PLRU: "pseudo-LRU"}
LFSR_RESET = 0xACE1

COUNTERS = ("access", "miss", "writeback", "evict", "pf_issued", "pf_useful",
            "mem_read", "mem_write", "vb_hit", "inval", "store_miss")


class Cache:
    """State of one cache and the decisions its policies make."""

    def __init__(self, sets=32, ways=1, line_bytes=32, repl=LRU, write_hit=0,
                 write_miss=0, wbuf_entries=0, wbuf_merge=0, victim_entries=0,
                 inclusion=0):
        if repl == PLRU and ways & (ways - 1):
            raise ValueError("pseudo-LRU needs a power-of-two number of ways")
        if victim_entries and inclusion:
            raise ValueError("an inclusive cache cannot have a victim buffer")
        self.sets, self.ways, self.line_bytes = sets, ways, line_bytes
        self.repl, self.write_hit, self.write_miss = repl, write_hit, write_miss
        self.wbuf_entries, self.wbuf_merge = wbuf_entries, wbuf_merge
        self.victim_entries, self.inclusion = victim_entries, inclusion
        self.vb = [None] * victim_entries           # line number per entry
        self.vb_dirty = [False] * victim_entries
        self.vb_next = 0                            # replaced when all are in use
        self.levels = ways.bit_length() - 1
        self.line = [[None] * ways for _ in range(sets)]    # line number per way
        self.dirty = [[False] * ways for _ in range(sets)]
        self.prefetched = [[False] * ways for _ in range(sets)]
        self.age = [list(range(ways)) for _ in range(sets)]     # 0 = youngest
        self.tree = [[0] * max(ways, 1) for _ in range(sets)]   # node n at index n
        self.lfsr = LFSR_RESET
        self.count = dict.fromkeys(COUNTERS, 0)

    # -- addresses ----------------------------------------------------------
    def line_of(self, addr):
        return addr // self.line_bytes

    def set_of(self, line):
        return line % self.sets

    def find(self, line):
        """The way holding the line, or None."""
        ways = self.line[self.set_of(line)]
        return ways.index(line) if line in ways else None

    # -- replacement --------------------------------------------------------
    def victim(self, index):
        """The way the next line brought into this set will take."""
        for way in range(self.ways):                # an empty way first
            if self.line[index][way] is None:
                return way
        if self.repl in (LRU, FIFO):
            return self.age[index].index(self.ways - 1)
        if self.repl == RANDOM:
            return self.lfsr % self.ways
        node = 1
        for _ in range(self.levels):
            node = 2 * node + self.tree[index][node]
        return node - self.ways

    def _youngest(self, index, way):
        ages = self.age[index]
        mine = ages[way]
        for other in range(self.ways):
            if ages[other] < mine:
                ages[other] += 1
        ages[way] = 0

    def _point_away(self, index, way):
        node = way + self.ways
        for _ in range(self.levels):
            self.tree[index][node >> 1] = 1 - (node & 1)
            node >>= 1

    def used(self, index, way):
        """An access completed on this line."""
        if self.repl == LRU:
            self._youngest(index, way)
        elif self.repl == PLRU:
            self._point_away(index, way)

    def placed(self, index, way):
        """A line was installed in this way."""
        self._youngest(index, way)
        self._point_away(index, way)

    def step_random(self):
        bit = ((self.lfsr >> 15) ^ (self.lfsr >> 13) ^ (self.lfsr >> 12)
               ^ (self.lfsr >> 10)) & 1
        self.lfsr = ((self.lfsr << 1) | bit) & 0xFFFF

    # -- victim buffer ------------------------------------------------------
    def vb_find(self, line):
        """The victim-buffer entry holding the line, or None."""
        return self.vb.index(line) if line in self.vb else None

    def vb_slot(self):
        """The entry the next line put into the victim buffer takes: an empty
        one, the lowest, or else the one the pointer names."""
        return self.vb.index(None) if None in self.vb else self.vb_next

    def vb_put(self, line, dirty):
        """Put an evicted line into the victim buffer."""
        slot = self.vb_slot()
        out, out_dirty = self.vb[slot], self.vb_dirty[slot]
        if out is not None:
            self.vb_next = (self.vb_next + 1) % self.victim_entries
        self.vb[slot], self.vb_dirty[slot] = line, dirty
        return out, out_dirty

    def swap(self, line):
        """Bring a line out of the victim buffer into the cache; the victim
        takes its entry."""
        entry = self.vb_find(line)
        index = self.set_of(line)
        way = self.victim(index)
        old, old_dirty = self.line[index][way], self.dirty[index][way]
        self.line[index][way], self.dirty[index][way] = line, self.vb_dirty[entry]
        self.prefetched[index][way] = False
        self.vb[entry], self.vb_dirty[entry] = old, old_dirty and old is not None
        self.step_random()
        self.placed(index, way)
        return way, old

    # -- one whole access (the model on its own) ----------------------------
    def access(self, addr, write=False):
        """Perform one access."""
        line = self.line_of(addr)
        index = self.set_of(line)
        way = self.find(line)
        self.count["access"] += 1
        if way is None:
            self.count["store_miss"] += bool(write)
        if way is None and self.vb_find(line) is not None:
            self.count["miss"] += 1
            self.count["vb_hit"] += 1
            way, old = self.swap(line)
            self.count["evict"] += old is not None
            hit = False
        elif way is None:
            self.count["miss"] += 1
            if write and self.write_miss:
                self.count["mem_write"] += 1        # the store goes round the cache
                return False
            way = self.victim(index)
            old, old_dirty = self.line[index][way], self.dirty[index][way]
            if old is not None:
                self.count["evict"] += 1
                if self.victim_entries:
                    old, old_dirty = self.vb_put(old, old_dirty)
                if old is not None and old_dirty:
                    self.count["writeback"] += 1
                    self.count["mem_write"] += 1
            self.step_random()
            self.count["mem_read"] += 1
            self.line[index][way] = line
            self.dirty[index][way] = False
            self.placed(index, way)
            hit = False
        else:
            hit = True
        self.used(index, way)
        if write:
            if self.write_hit:
                self.count["mem_write"] += 1
            else:
                self.dirty[index][way] = True
        return hit


class LogError(Exception):
    pass


def check_log(lines, cache, must_drain=False):
    """Replay an event log against `cache` (a fresh Cache)."""
    count = cache.count
    line_bytes = cache.line_bytes
    full = (1 << line_bytes) - 1
    pending = []            # writes on their way to memory: [line, strobes, sent]
    filling = None          # [line, set, way, cause, requested]
    limit = max(cache.wbuf_entries, 1)
    recalled = None         # inclusive: the line that has just been taken back
    given_up = set()        # lines given up to the cache below, not brought in since

    def queue_store(line, strobes):
        if cache.wbuf_merge:
            for entry in reversed(pending):
                if entry[0] == line and not entry[2]:
                    entry[1] |= strobes
                    return
        pending.append([line, strobes, False])
        if len(pending) > limit:
            raise LogError(f"{len(pending)} writes on their way to memory, room for {limit}")

    def strobes_of(addr, size):
        """The bytes of the line an access covers."""
        if size.startswith("/"):
            return int(size[1:], 16)
        return ((1 << (1 << int(size))) - 1) << (addr % line_bytes)

    def leaves(line, dirty):
        """A line leaves the cache altogether."""
        if dirty:
            count["writeback"] += 1
            pending.append([line, full, False])
            if len(pending) > limit:
                raise LogError(f"{len(pending)} writes on their way to memory, "
                               f"room for {limit}")

    def named(line, dirty):
        return "- -" if line is None else f"{line * line_bytes:x} {'d' if dirty else 'c'}"

    number = 0
    try:
        for number, text in enumerate(lines, 1):
            field = text.split()
            if not field:
                continue
            kind = field[0]
            if kind not in ("B", "F", "w", "M", "X"):
                recalled = None
            if kind == "H":
                addr, size = int(field[2], 16), field[3]
                line = cache.line_of(addr)
                way = cache.find(line)
                if way is None:
                    raise LogError("an access completed on a line that is not in the cache")
                index = cache.set_of(line)
                count["access"] += 1
                cache.used(index, way)
                if cache.prefetched[index][way]:
                    cache.prefetched[index][way] = False
                    count["pf_useful"] += 1
                if field[1] == "w":
                    if cache.write_hit:
                        queue_store(line, strobes_of(addr, size))
                    else:
                        cache.dirty[index][way] = True
            elif kind == "F":
                cause, line = field[1], int(field[2], 16) // line_bytes
                if filling:
                    raise LogError("a line is brought in while another is on its way")
                if cache.find(line) is not None:
                    raise LogError("a line is brought in that is already in the cache")
                if cache.vb_find(line) is not None:
                    raise LogError("a line is brought in that is in the victim buffer")
                if cause == "w" and cache.write_miss:
                    raise LogError("a line is brought in for a store, but stores do not allocate")
                index = cache.set_of(line)
                way = cache.victim(index)
                old, old_dirty = cache.line[index][way], cache.dirty[index][way]
                want = named(old, old_dirty)
                got = f"{field[3]} {field[4]}"
                if got != want:
                    raise LogError(f"victim: the cache chose [{got}], the "
                                   f"{REPL_NAMES[cache.repl]} policy chooses [{want}]")
                if cache.inclusion and old is not None and recalled != old:
                    raise LogError("a line is evicted that was not taken back from the "
                                   "caches above just before")
                recalled = None
                if old is not None:
                    count["evict"] += 1
                    if cache.victim_entries:
                        out, out_dirty = cache.vb_put(old, old_dirty)
                        want, got = named(out, out_dirty), f"{field[5]} {field[6]}"
                        if got != want:
                            raise LogError(f"victim buffer: [{got}] was pushed out, "
                                           f"expected [{want}]")
                        if out is not None:
                            leaves(out, out_dirty)
                    else:
                        leaves(old, old_dirty)
                elif cache.victim_entries and f"{field[5]} {field[6]}" != "- -":
                    raise LogError("a line left the victim buffer although nothing went in")
                given_up.discard(line)
                count["pf_issued" if cause == "p" else "miss"] += 1
                count["store_miss"] += cause == "w"
                cache.line[index][way] = None
                cache.dirty[index][way] = False
                cache.prefetched[index][way] = False
                cache.step_random()
                filling = [line, index, way, cause, False]
            elif kind == "R":
                line = int(field[1], 16) // line_bytes
                if not filling or filling[0] != line or filling[4]:
                    raise LogError("a line was read from memory that is not being brought in")
                if any(entry[0] == line for entry in pending):
                    raise LogError("a line was read from memory while a write to it "
                                   "was still on its way")
                filling[4] = True
                count["mem_read"] += 1
            elif kind == "I":
                line = int(field[1], 16) // line_bytes
                if not filling or filling[0] != line or not filling[4]:
                    raise LogError("a line was installed that was not read from memory")
                _, index, way, cause, _ = filling
                cache.line[index][way] = line
                cache.prefetched[index][way] = (cause == "p")
                cache.placed(index, way)
                filling = None
            elif kind == "S":
                line = int(field[2], 16) // line_bytes
                if filling:
                    raise LogError("a line is swapped in while another is on its way")
                if cache.find(line) is not None:
                    raise LogError("a line is swapped in that is already in the cache")
                if cache.vb_find(line) is None:
                    raise LogError("a line is swapped in that is not in the victim buffer")
                index = cache.set_of(line)
                way = cache.victim(index)
                want = named(cache.line[index][way], cache.dirty[index][way])
                got = f"{field[3]} {field[4]}"
                if got != want:
                    raise LogError(f"victim: the cache chose [{got}], the "
                                   f"{REPL_NAMES[cache.repl]} policy chooses [{want}]")
                _, old = cache.swap(line)
                given_up.discard(line)
                count["miss"] += 1
                count["vb_hit"] += 1
                count["evict"] += old is not None
                count["store_miss"] += field[1] == "w"
            elif kind == "X":
                line = int(field[1], 16) // line_bytes
                way, entry = cache.find(line), cache.vb_find(line)
                index = cache.set_of(line)
                if way is not None:
                    was_dirty = cache.dirty[index][way]
                    cache.line[index][way] = None
                    cache.dirty[index][way] = False
                    cache.prefetched[index][way] = False
                elif entry is not None:
                    was_dirty = cache.vb_dirty[entry]
                    cache.vb[entry], cache.vb_dirty[entry] = None, False
                else:
                    raise LogError("a line was given up that is not in the cache")
                if (field[2] == "d") != was_dirty:
                    raise LogError("a line was given up as "
                                   + ("dirty" if field[2] == "d" else "clean")
                                   + ", but it was " + ("dirty" if was_dirty else "clean"))
                count["inval"] += 1
                given_up.add(line)
            elif kind == "B":
                line = int(field[1], 16) // line_bytes
                way = cache.find(line)
                if not cache.inclusion:
                    raise LogError("a line was taken back, but the cache is not inclusive")
                if way is None:
                    raise LogError("a line was taken back that is not in the cache")
                if field[2] == "d":
                    cache.dirty[cache.set_of(line)][way] = True
                recalled = line
            elif kind == "N":
                addr, size = int(field[1], 16), field[2]
                line = cache.line_of(addr)
                # A store that was waiting for memory when its line was given up
                # to the cache below also ends without its line.
                if not cache.write_miss and line not in given_up:
                    raise LogError("a store completed without its line, but stores allocate")
                if cache.find(line) is not None:
                    raise LogError("a store went round a line that is in the cache")
                if cache.vb_find(line) is not None:
                    raise LogError("a store went round a line that is in the victim buffer")
                if filling and filling[0] == line:
                    raise LogError("a store went round a line that is on its way")
                count["access"] += 1
                count["miss"] += 1
                count["store_miss"] += 1
                queue_store(line, strobes_of(addr, size))
            elif kind == "w":
                line = int(field[1], 16) // line_bytes
                if not pending or pending[0][2] or pending[0][0] != line:
                    raise LogError("a write was sent to memory that is not the oldest one waiting")
                pending[0][2] = True
                count["mem_write"] += 1
            elif kind == "M":
                line, strobes = int(field[1], 16) // line_bytes, int(field[2], 16)
                if not pending:
                    raise LogError("a write reached memory that nothing asked for")
                want_line, want_strobes, sent = pending[0]
                if (line, strobes) != (want_line, want_strobes):
                    raise LogError(f"a write reached memory for line {line * line_bytes:x} "
                                   f"bytes {strobes:x}; expected line "
                                   f"{want_line * line_bytes:x} bytes {want_strobes:x}")
                if not sent:
                    if cache.wbuf_entries:
                        raise LogError("a write reached memory without being sent")
                    count["mem_write"] += 1         # a store sent straight to memory
                pending.pop(0)
            else:
                raise LogError(f"unknown event [{text.strip()}]")
        if must_drain and pending:
            raise LogError(f"{len(pending)} writes never reached memory")
    except LogError as err:
        return f"cache log line {number} [{text.strip()}]: {err}", dict(count)
    except (IndexError, ValueError):
        return f"cache log line {number} [{text.strip()}]: malformed", dict(count)
    return None, dict(count)


def check_log_file(path, must_drain=False, **params):
    with open(path, encoding="ascii") as handle:
        return check_log(handle, Cache(**params), must_drain)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Replay a cache event log against the model.")
    parser.add_argument("log")
    parser.add_argument("--sets", type=int, default=32)
    parser.add_argument("--ways", type=int, default=1)
    parser.add_argument("--line-bytes", type=int, default=32)
    parser.add_argument("--repl", type=int, default=LRU, help="0 LRU, 1 FIFO, 2 random, 3 pseudo-LRU")
    parser.add_argument("--write-hit", type=int, default=0, help="0 write-back, 1 write-through")
    parser.add_argument("--write-miss", type=int, default=0, help="0 allocate, 1 no-allocate")
    parser.add_argument("--wbuf-entries", type=int, default=0)
    parser.add_argument("--wbuf-merge", type=int, default=0)
    parser.add_argument("--victim-entries", type=int, default=0)
    parser.add_argument("--inclusion", type=int, default=0)
    args = parser.parse_args(argv)
    error, counters = check_log_file(
        args.log, sets=args.sets, ways=args.ways, line_bytes=args.line_bytes, repl=args.repl,
        write_hit=args.write_hit, write_miss=args.write_miss,
        wbuf_entries=args.wbuf_entries, wbuf_merge=args.wbuf_merge,
        victim_entries=args.victim_entries, inclusion=args.inclusion)
    print(error or "the log agrees with the model")
    print(", ".join(f"{name} {value}" for name, value in counters.items()))
    return 1 if error else 0


if __name__ == "__main__":
    sys.exit(main())
