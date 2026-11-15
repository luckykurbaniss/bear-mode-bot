import unittest

from delivery import is_order_transition_allowed


class OrderTransitionTests(unittest.TestCase):
    def test_new_order_must_be_confirmed_before_cooking(self):
        self.assertTrue(is_order_transition_allowed("new", "confirmed"))
        self.assertFalse(is_order_transition_allowed("new", "cooking"))

    def test_confirmed_order_can_start_cooking(self):
        self.assertTrue(is_order_transition_allowed("confirmed", "cooking"))

    def test_terminal_statuses_cannot_be_reopened(self):
        for status in ("delivered", "cancelled"):
            with self.subTest(status=status):
                self.assertFalse(is_order_transition_allowed(status, "new"))
                self.assertFalse(is_order_transition_allowed(status, "confirmed"))

    def test_courier_status_cannot_regress(self):
        self.assertFalse(is_order_transition_allowed("courier", "cooking"))
        self.assertFalse(is_order_transition_allowed("courier", "confirmed"))

    def test_cancellation_is_allowed_before_delivery(self):
        for status in ("new", "confirmed", "cooking"):
            with self.subTest(status=status):
                self.assertTrue(is_order_transition_allowed(status, "cancelled"))

    def test_unknown_status_is_rejected(self):
        self.assertFalse(is_order_transition_allowed("unknown", "confirmed"))


if __name__ == "__main__":
    unittest.main()
