import os, random, shutil, tempfile, unittest

from minidb import Database, BufferPoolFullError, StorageError, SchemaError, POLICIES
from minidb import page
from minidb.disk import DiskManager
from minidb.buffer_pool import BufferPool
from minidb.replacer import make_replacer

SCHEMA = [("id", "INT"), ("name", "TEXT"), ("score", "FLOAT"), ("note", "TEXT")]


class TmpDirCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="minidb_test_")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def p(self, name):
        return os.path.join(self.dir, name)


# --------------------------------------------------------------------------- disk
class TestDisk(TmpDirCase):
    def test_roundtrip_and_restart(self):
        d = DiskManager(self.p("f.db"), 512)
        pid = d.allocate_page()
        data = bytearray(os.urandom(512))
        d.write_page(pid, data)
        d.close()
        d2 = DiskManager(self.p("f.db"), 512)
        self.assertEqual(d2.num_pages, 2)
        self.assertEqual(d2.read_page(pid), data)

    def test_unwritten_page_reads_zero_and_bad_ids(self):
        d = DiskManager(self.p("f.db"), 512)
        pid = d.allocate_page()
        self.assertEqual(d.read_page(pid), bytearray(512))
        for bad in (0, 99, -1):
            with self.assertRaises(StorageError):
                d.read_page(bad)

    def test_free_list_reuse_and_counters(self):
        d = DiskManager(self.p("f.db"), 512)
        a, b = d.allocate_page(), d.allocate_page()
        d.write_page(a, bytearray(512)); d.write_page(b, bytearray(512))
        d.deallocate_page(a)
        self.assertEqual(d.allocate_page(), a)          # reused
        self.assertEqual(d.allocate_page(), b + 1)      # then grow
        d.close()
        self.assertEqual(DiskManager(self.p("f.db"), 512).free_head, 0)

    def test_page_size_mismatch_rejected(self):
        DiskManager(self.p("f.db"), 512).close()
        with self.assertRaises(StorageError):
            DiskManager(self.p("f.db"), 1024)


# --------------------------------------------------------------------------- replacement policies
class TestPolicies(TmpDirCase):
    def _pool(self, policy):
        d = DiskManager(self.p(f"{policy}.db"), 256)
        bp = BufferPool(d, 3, policy)
        for _ in range(3):
            pid, _ = bp.new_page(); bp.unpin_page(pid, True)          # pages 1,2,3
        pid4 = d.allocate_page(); d.write_page(pid4, bytearray(256))   # page 4 only on disk
        bp.flush_all(); bp.drop_cache()
        for pid in (1, 2, 3):                                          # load order 1,2,3
            bp.fetch_page(pid); bp.unpin_page(pid)
        bp.fetch_page(1); bp.unpin_page(1)                             # re-touch page 1
        bp.reset_stats()
        return bp

    def evicted_after_loading_page4(self, policy):
        bp = self._pool(policy)
        bp.fetch_page(4); bp.unpin_page(4)
        resident = set(bp.page_table)
        self.assertEqual(len(resident), 3)
        return ({1, 2, 3} | {4}) - resident

    def test_lru_evicts_least_recent(self):
        self.assertEqual(self.evicted_after_loading_page4("lru"), {2})

    def test_fifo_ignores_hits(self):
        self.assertEqual(self.evicted_after_loading_page4("fifo"), {1})

    def test_mru_evicts_most_recent(self):
        self.assertEqual(self.evicted_after_loading_page4("mru"), {1})

    def test_clock_second_chance(self):
        # all ref bits set -> one full sweep clears them -> frame at hand (page 1) goes
        self.assertEqual(self.evicted_after_loading_page4("clock"), {1})

    def test_unknown_policy(self):
        with self.assertRaises(ValueError):
            make_replacer("nope", 4)

    def test_pinned_never_evicted(self):
        for pol in POLICIES:
            d = DiskManager(self.p(f"p_{pol}.db"), 256)
            bp = BufferPool(d, 2, pol)
            pids = []
            for _ in range(2):
                pid, _ = bp.new_page(); pids.append(pid)          # both stay pinned
            with self.assertRaises(BufferPoolFullError):
                bp.new_page()
            bp.unpin_page(pids[0], True)
            pid3, _ = bp.new_page()                               # now one victim exists
            self.assertNotIn(pids[0], bp.page_table)
            self.assertIn(pids[1], bp.page_table)


# --------------------------------------------------------------------------- buffer pool
class TestBufferPool(TmpDirCase):
    def test_hit_miss_counters_and_writeback(self):
        d = DiskManager(self.p("b.db"), 256)
        bp = BufferPool(d, 2, "lru")
        ids = []
        for i in range(4):
            pid, buf = bp.new_page(); buf[0] = i + 10; bp.unpin_page(pid, True); ids.append(pid)
        self.assertEqual(bp.evictions, 2)                # pages 1,2 evicted dirty
        self.assertEqual(bp.dirty_evictions, 2)
        bp.reset_stats()
        for pid in ids:                                  # data survived eviction
            buf = bp.fetch_page(pid); self.assertEqual(buf[0], ids.index(pid) + 10); bp.unpin_page(pid)
        s = bp.stats()
        self.assertEqual(s["hits"] + s["misses"], 4)
        self.assertEqual(s["page_reads"], s["misses"])
        self.assertAlmostEqual(s["hit_ratio"], s["hits"] / 4)

    def test_unpin_errors_and_flush(self):
        d = DiskManager(self.p("b.db"), 256)
        bp = BufferPool(d, 2)
        pid, buf = bp.new_page(); buf[5] = 7; bp.unpin_page(pid, True)
        with self.assertRaises(StorageError):
            bp.unpin_page(pid)                           # already unpinned
        bp.flush_page(pid)
        self.assertEqual(d.read_page(pid)[5], 7)

    def test_reconfigure_keeps_data(self):
        d = DiskManager(self.p("b.db"), 256)
        bp = BufferPool(d, 4, "lru")
        pid, buf = bp.new_page(); buf[1] = 99; bp.unpin_page(pid, True)
        bp.reconfigure("fifo", 8)
        self.assertEqual((bp.policy, bp.pool_size), ("fifo", 8))
        self.assertEqual(bp.fetch_page(pid)[1], 99)
        with self.assertRaises(StorageError):
            bp.reconfigure("lru")                        # pinned page present


# --------------------------------------------------------------------------- slotted page
class TestSlottedPage(unittest.TestCase):
    def new(self, size=256):
        b = bytearray(size); page.init_page(b); return b

    def test_insert_get_delete_reuse(self):
        b = self.new()
        s = [page.insert(b, bytes([i]) * 20) for i in range(5)]
        self.assertEqual(s, [0, 1, 2, 3, 4])
        off, ln = page.slot_span(b, 2); self.assertEqual(bytes(b[off:off + ln]), bytes([2]) * 20)
        self.assertTrue(page.delete(b, 2)); self.assertIsNone(page.slot_span(b, 2))
        self.assertFalse(page.delete(b, 2))
        self.assertEqual(page.insert(b, b"x" * 20), 2)   # dead slot reused

    def test_full_page_and_compaction(self):
        b = self.new()
        slots = []
        while True:
            s = page.insert(b, b"a" * 30)
            if s is None: break
            slots.append(s)
        self.assertGreater(len(slots), 5)
        page.delete(b, slots[1]); page.delete(b, slots[3])
        self.assertIsNotNone(page.insert(b, b"b" * 55))  # needs compaction of two holes

    def test_update_in_place_grow_and_overflow(self):
        b = self.new()
        a = page.insert(b, b"a" * 10); c = page.insert(b, b"c" * 10)
        self.assertTrue(page.update(b, a, b"z" * 5))
        self.assertTrue(page.update(b, a, b"y" * 40))    # grows, relocated inside the page
        off, ln = page.slot_span(b, c); self.assertEqual(bytes(b[off:off + ln]), b"c" * 10)
        self.assertFalse(page.update(b, a, b"q" * 400))
        self.assertIsNone(page.update(b, 9, b"x"))


# --------------------------------------------------------------------------- tables
def mk_row(i, rnd=None):
    rnd = rnd or random.Random(i)
    return (i, "name-%d-%s" % (i, "x" * rnd.randint(0, 25)), i * 1.5, "n" * rnd.randint(0, 40))


class TestTables(TmpDirCase):
    LAYOUTS = ("row", "column")

    def test_crud_all_layouts(self):
        for layout in self.LAYOUTS:
            with self.subTest(layout=layout):
                db = Database(self.p(layout), pool_size=4, page_size=256)
                t = db.create_table("t", SCHEMA, layout)
                ids = t.insert_many([mk_row(i) for i in range(300)])
                self.assertEqual(t.count(), 300)
                self.assertEqual(t.get(ids[123]), mk_row(123))
                self.assertEqual(t.get(ids[7], ["score", "id"]), (10.5, 7))
                self.assertEqual(list(t.rows()), [mk_row(i) for i in range(300)])
                self.assertEqual(list(t.rows(["name"]))[5], (mk_row(5)[1],))
                self.assertEqual(sum(t.column_values("id")), sum(range(300)))
                self.assertTrue(t.delete(ids[10])); self.assertFalse(t.delete(ids[10]))
                self.assertIsNone(t.get(ids[10]))
                self.assertEqual(t.count(), 299)
                self.assertNotIn(10, [r[0] for r in t.rows()])
                new = t.update(ids[20], (20, "short", 99.0, ""))
                self.assertEqual(t.get(new), (20, "short", 99.0, ""))
                new2 = t.update(new, (20, "much longer name" * 3, 1.0, "n" * 60))
                self.assertEqual(t.get(new2)[1], "much longer name" * 3)
                self.assertEqual(db.pool.pinned_count(), 0)      # no pin leaks
                db.close()

    def test_unicode_and_empty_text(self):
        for layout in self.LAYOUTS:
            db = Database(self.p("u" + layout), pool_size=3, page_size=256)
            t = db.create_table("t", [("a", "TEXT"), ("b", "INT")], layout)
            rows = [("héllo wörld ✓ مرحبا", 1), ("", 2), ("x" * 200, 3)]
            ids = t.insert_many(rows)
            self.assertEqual([t.get(i) for i in ids], rows)
            db.close()

    def test_validation(self):
        db = Database(self.p("v"), page_size=256)
        t = db.create_table("t", SCHEMA)
        for bad in [(1, "a", 1.0), ("1", "a", 1.0, ""), (1, 5, 1.0, ""), (True, "a", 1.0, ""), (2 ** 63, "a", 1.0, "")]:
            with self.assertRaises(SchemaError):
                t.insert(bad)
        with self.assertRaises(ValueError):
            t.insert((1, "x" * 500, 1.0, ""))            # record larger than a page
        c = db.create_table("c", SCHEMA, "column")
        with self.assertRaises(ValueError):
            c.insert((1, "x" * 500, 1.0, ""))
        db.close()

    def test_restart_persistence(self):
        for layout in self.LAYOUTS:
            path = self.p("r" + layout)
            db = Database(path, pool_size=4, page_size=256)
            t = db.create_table("t", SCHEMA, layout)
            ids = t.insert_many([mk_row(i) for i in range(500)])
            t.delete(ids[3]); t.update(ids[4], (4, "upd", 0.5, "z"))
            before = list(t.rows())
            db.close()
            db2 = Database(path, pool_size=2, policy="fifo", page_size=256)
            t2 = db2.table("t")
            self.assertEqual(list(t2.rows()), before)
            self.assertEqual(t2.count(), 499)
            t2.insert((999, "after restart", 1.0, "")); db2.close()
            db3 = Database(path, page_size=256)
            self.assertEqual(list(db3.table("t").rows())[-1][0], 999)
            db3.close()

    def test_drop_table_frees_pages(self):
        db = Database(self.p("d"), pool_size=4, page_size=256)
        t = db.create_table("t", SCHEMA, "row"); t.insert_many([mk_row(i) for i in range(200)])
        n_before = db.disk.num_pages
        db.drop_table("t")
        t2 = db.create_table("t2", SCHEMA, "column"); t2.insert_many([mk_row(i) for i in range(200)])
        self.assertLess(db.disk.num_pages, n_before + 15)   # freed pages were reused
        self.assertEqual(db.pool.pinned_count(), 0)
        db.close()

    def test_results_independent_of_policy_and_pool_size(self):
        path = self.p("pol")
        db = Database(path, pool_size=8, page_size=256)
        t = db.create_table("t", SCHEMA, "row"); ids = t.insert_many([mk_row(i) for i in range(400)])
        ref_scan = list(t.rows())
        rnd = random.Random(1); probes = [rnd.choice(ids) for _ in range(300)]
        ref_get = [t.get(r) for r in probes]
        for pol in POLICIES:
            for size in (1 + 1, 5, 64):
                db.configure_buffer_pool(pol, size)
                db.drop_cache()
                self.assertEqual(list(t.rows()), ref_scan, (pol, size))
                self.assertEqual([t.get(r) for r in probes], ref_get, (pol, size))
        db.close()

    def test_random_differential_against_model(self):
        """Row table, column table and a Python dict must agree after random ops + restarts."""
        for pol in POLICIES:
            rnd = random.Random(42)
            path = self.p("fz" + pol)
            db = Database(path, pool_size=3, policy=pol, page_size=256)
            tabs = {l: db.create_table(l, SCHEMA, l) for l in self.LAYOUTS}
            model, handles = {}, {l: {} for l in self.LAYOUTS}
            nxt = 0
            for step in range(1500):
                op = rnd.random()
                if op < 0.55 or not model:
                    row = mk_row(nxt, rnd)
                    for l, t in tabs.items():
                        handles[l][nxt] = t.insert(row)
                    model[nxt] = row; nxt += 1
                elif op < 0.70:
                    k = rnd.choice(list(model))
                    for l, t in tabs.items():
                        self.assertTrue(t.delete(handles[l].pop(k)))
                    del model[k]
                elif op < 0.88:
                    k = rnd.choice(list(model)); row = mk_row(k, rnd)[:2] + (rnd.random(), "u" * rnd.randint(0, 30))
                    row = (k,) + row[1:]
                    for l, t in tabs.items():
                        handles[l][k] = t.update(handles[l][k], row)
                    model[k] = row
                else:
                    k = rnd.choice(list(model))
                    for l, t in tabs.items():
                        self.assertEqual(t.get(handles[l][k]), model[k])
                if step % 500 == 499:
                    db.close()
                    db = Database(path, pool_size=3, policy=pol, page_size=256)
                    tabs = {l: db.table(l) for l in self.LAYOUTS}
            for l, t in tabs.items():
                got = sorted(t.rows())
                self.assertEqual(got, sorted(model.values()), (pol, l))
                self.assertEqual(t.count(), len(model))
            self.assertEqual(db.pool.pinned_count(), 0)
            db.close()


if __name__ == "__main__":
    unittest.main()
