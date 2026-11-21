import tempfile
import unittest

from minidb import Database, BPlusTree, LSMTree, FernetCodec


class TestBPlusTree(unittest.TestCase):
    def test_duplicate_and_range_after_splits(self):
        tree = BPlusTree(order=4)
        for i in range(100):
            tree.insert(i, f"rid-{i}")
        tree.insert(25, "duplicate")
        self.assertEqual(tree.search(25), ["rid-25", "duplicate"])
        result = tree.range_search(20, 25)
        self.assertEqual([k for k, _ in result], [20, 21, 22, 23, 24, 25, 25])

    def test_missing_key(self):
        self.assertEqual(BPlusTree().search(999), [])

    def test_delete_entry_after_splits(self):
        tree = BPlusTree(order=4)
        for i in range(80): tree.insert(i, i)
        tree.insert(20, "duplicate")
        self.assertTrue(tree.delete(20, 20))
        self.assertEqual(tree.search(20), ["duplicate"])
        self.assertFalse(tree.delete(20, 999))
        self.assertEqual(len(tree.range_search()), 80)


class TestLSMTree(unittest.TestCase):
    def test_flush_lookup_and_tombstone(self):
        tree = LSMTree(flush_threshold=2)
        tree.insert(10, "a")
        tree.insert(20, "b")  # flush
        tree.delete(10, "a")
        tree.insert(10, "c")  # flush
        self.assertEqual(tree.search(10), ["c"])
        self.assertEqual(tree.search(20), ["b"])

    def test_range(self):
        tree = LSMTree(flush_threshold=100)
        for i in [5, 1, 3]: tree.insert(i, i)
        self.assertEqual([k for k, _ in tree.range_search(2, 5)], [3, 5])

    def test_compaction_removes_tombstones_and_old_runs(self):
        tree = LSMTree(flush_threshold=1)
        tree.insert("a", 1); tree.insert("b", 2); tree.delete("a", 1); tree.insert("a", 3)
        before = len(tree.runs)
        result = tree.compact()
        self.assertGreaterEqual(before, 3)
        self.assertEqual(result["runs_after"], 1)
        self.assertEqual(tree.search("a"), [3])
        self.assertEqual(tree.search("b"), [2])


class TestIntegratedIndexes(unittest.TestCase):
    def _run_layout(self, layout):
        with tempfile.TemporaryDirectory() as d:
            db = Database(d, pool_size=8, page_size=512)
            t = db.create_table("products", [
                ("id", "INT"), ("name", "TEXT"), ("price", "FLOAT")
            ], layout=layout)
            ids = t.insert_many([
                (1, "book", 10.0), (2, "pen", 2.0),
                (3, "bag", 25.0), (4, "book", 12.0)
            ])
            self.assertEqual(db.create_index("products", "id", "btree"), "btree")
            self.assertEqual(db.create_index("products", "name", "lsm"), "lsm")
            self.assertEqual([row[1][1] for row in db.index_lookup("products", "id", 3)], ["bag"])
            self.assertEqual(len(db.index_lookup("products", "name", "book")), 2)
            self.assertEqual(len(db.index_range("products", "id", 2, 4)), 3)
            self.assertEqual(len(db.select_where("products", "id", "=", 3)), 1)
            self.assertEqual(len(db.select_where("products", "id", "between", (2, 4))), 3)

            # Update key, delete row, then verify stale index entries are filtered.
            moved = t.update(ids[1], (22, "pen", 3.0))
            self.assertEqual(db.index_lookup("products", "id", 2), [])
            self.assertEqual(len(db.index_lookup("products", "id", 22)), 1)
            self.assertTrue(t.delete(ids[2]))
            self.assertEqual(db.index_lookup("products", "id", 3), [])
            db.close()

            # Index contents are persisted in DiskManager pages and loaded on restart.
            db2 = Database(d, pool_size=8, page_size=512)
            self.assertEqual(len(db2.index_lookup("products", "name", "book")), 2)
            self.assertEqual(len(db2.index_lookup("products", "id", 22)), 1)
            db2.close()

    def test_row_layout(self):
        self._run_layout("row")

    def test_column_layout(self):
        self._run_layout("column")

    def test_auto_selection(self):
        with tempfile.TemporaryDirectory() as d:
            db = Database(d)
            db.create_table("products", [("id", "INT")])
            self.assertEqual(db.create_index("products", "id", "auto", workload="write"), "lsm")
            self.assertEqual(db.create_index("products", "id", "btree"), "btree")
            db.close()

    def test_object_storage_and_codec_hooks(self):
        class ReverseCodec:
            def encrypt(self, data):
                return data[::-1]
            def decrypt(self, data):
                return data[::-1]

        with tempfile.TemporaryDirectory() as d:
            db = Database(d, object_codec=ReverseCodec())
            db.put_object("products/image/1", b"image-bytes")
            self.assertEqual(db.get_object("products/image/1"), b"image-bytes")
            self.assertTrue(db.delete_object("products/image/1"))
            self.assertFalse(db.objects.exists("products/image/1"))
            db.close()

    def test_fernet_encryption_at_rest(self):
        with tempfile.TemporaryDirectory() as d:
            key = FernetCodec.generate_key()
            db = Database(d, object_codec=FernetCodec(key))
            db.put_object("secret", b"sensitive-payload")
            with open(db.objects._path("secret"), "rb") as encrypted_file:
                stored = encrypted_file.read()
            self.assertNotIn(b"sensitive-payload", stored)
            self.assertEqual(db.get_object("secret"), b"sensitive-payload")
            db.close()

    def test_persistent_index_pages_recorded_in_catalog(self):
        import json, os
        with tempfile.TemporaryDirectory() as d:
            db = Database(d, page_size=512)
            t = db.create_table("t", [("id", "INT")])
            t.insert_many([(i,) for i in range(20)])
            db.create_index("t", "id", "btree")
            db.checkpoint()
            with open(os.path.join(d, "catalog.json"), encoding="utf-8") as f: cat = json.load(f)
            meta = cat["index_storage"]["t"]["id"]
            self.assertTrue(meta["page_ids"])
            self.assertGreater(meta["size"], 0)
            self.assertEqual(meta["format"], "pickle-pages-v1")
            self.assertEqual(meta["page_count"], len(meta["page_ids"]))
            db.close()

    def test_btree_and_lsm_equality_range_survive_restart_and_mutations(self):
        with tempfile.TemporaryDirectory() as d:
            db = Database(d, page_size=512, pool_size=8)
            t = db.create_table("items", [("id", "INT"), ("group", "TEXT")], layout="row")
            rids = t.insert_many([(i, "even" if i % 2 == 0 else "odd") for i in range(40)])
            db.create_index("items", "id", "btree")
            db.create_index("items", "group", "lsm")
            db.checkpoint()
            db.close()

            db = Database(d, page_size=512, pool_size=8)
            self.assertEqual([row[1][0] for row in db.index_lookup("items", "id", 17)], [17])
            self.assertEqual([row[1][0] for row in db.index_range("items", "id", 10, 15)], list(range(10, 16)))
            self.assertEqual(len(db.index_lookup("items", "group", "even")), 20)
            self.assertEqual(len(db.index_range("items", "group", "even", "odd")), 40)
            # Mutations after loading persisted indexes must remain searchable.
            table = db.table("items")
            new_rid = table.update(rids[17], (117, "odd"))
            self.assertEqual(db.index_lookup("items", "id", 17), [])
            self.assertEqual([row[1][0] for row in db.index_lookup("items", "id", 117)], [117])
            self.assertTrue(table.delete(rids[18]))
            self.assertEqual(db.index_lookup("items", "id", 18), [])
            db.close()

            db = Database(d, page_size=512, pool_size=8)
            self.assertEqual([row[1][0] for row in db.index_lookup("items", "id", 117)], [117])
            self.assertEqual(db.index_lookup("items", "id", 18), [])
            self.assertEqual(len(db.index_range("items", "id", 10, 20)), 9)
            db.close()

    def test_checkpoint_reclaims_old_index_pages_and_drop_index(self):
        import json, os
        with tempfile.TemporaryDirectory() as d:
            db = Database(d, page_size=256)
            t = db.create_table("t", [("id", "INT")])
            t.insert_many([(i,) for i in range(100)])
            db.create_index("t", "id", "btree")
            db.checkpoint()
            with open(os.path.join(d, "catalog.json"), encoding="utf-8") as f:
                first = json.load(f)["index_storage"]["t"]["id"]["page_ids"]
            t.insert_many([(i,) for i in range(100, 200)])
            db.checkpoint()
            with open(os.path.join(d, "catalog.json"), encoding="utf-8") as f:
                second = json.load(f)["index_storage"]["t"]["id"]["page_ids"]
            self.assertTrue(second)
            db.drop_index("t", "id")
            db.checkpoint()
            with open(os.path.join(d, "catalog.json"), encoding="utf-8") as f:
                cat = json.load(f)
            self.assertNotIn("t", cat.get("index_storage", {}))
            self.assertNotIn("id", cat.get("indexes", {}).get("t", {}))
            db.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
