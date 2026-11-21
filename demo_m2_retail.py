"""Small Retail Orders demonstration for MiniDB M2."""
import tempfile
from minidb import Database

with tempfile.TemporaryDirectory() as path:
    db = Database(path, pool_size=16)
    products = db.create_table(
        "products",
        [("product_id", "INT"), ("name", "TEXT"), ("category", "TEXT"),
         ("price", "FLOAT"), ("stock", "INT")],
        layout="row",
    )
    products.insert_many([
        (101, "Keyboard", "Accessories", 35.0, 20),
        (102, "Mouse", "Accessories", 15.0, 50),
        (103, "Monitor", "Displays", 180.0, 12),
        (104, "USB Cable", "Accessories", 5.0, 100),
    ])

    print("B+ Tree:", db.create_index("products", "product_id", "btree"))
    print("LSM Tree:", db.create_index("products", "category", "lsm"))
    print("Product 103:", db.index_lookup("products", "product_id", 103))
    print("IDs 101..103:", db.index_range("products", "product_id", 101, 103))
    print("Accessories:", db.index_lookup("products", "category", "Accessories"))

    db.put_object("product-images/101", b"demo-image-bytes")
    print("Object round trip:", db.get_object("product-images/101"))
    print("Index stats:", db.index_stats("products"))
    db.close()
