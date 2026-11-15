import sqlite3
import tempfile
import unittest
from pathlib import Path

import bot
import delivery


class OrderDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        db_path = str(Path(self.temp_dir.name) / "orders.db")
        bot.DB_PATH = db_path
        delivery.DB_PATH = db_path
        bot.init_db()
        delivery.init_delivery_db()

    def tearDown(self):
        self.temp_dir.cleanup()

    def create_order(self, submission_id=None):
        return bot.save_order(
            12345, "Test Customer", "+84123456789", "Nha Trang",
            "2026-12-01", "", "", "Сет Энергия × 1", 170000,
            payment_method="cash", submission_id=submission_id,
        )

    def test_clearing_orders_preserves_autoincrement_sequence(self):
        first_id = self.create_order()
        self.assertEqual(first_id, 1)
        self.assertEqual(bot.clear_all_orders(), 1)
        next_id = self.create_order()
        self.assertEqual(next_id, 2)

    def test_submission_id_is_unique(self):
        self.create_order("submission_123456")
        with self.assertRaises(sqlite3.IntegrityError):
            self.create_order("submission_123456")

    def test_submission_id_can_be_omitted_for_legacy_orders(self):
        order_id = self.create_order()
        with bot.db_connect() as conn:
            row = conn.execute(
                "SELECT submission_id, admin_notified FROM orders WHERE id = ?",
                (order_id,),
            ).fetchone()
        self.assertIsNone(row["submission_id"])
        self.assertEqual(row["admin_notified"], 0)


if __name__ == "__main__":
    unittest.main()
