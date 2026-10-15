import os
import sys
import unittest
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app as shop


class ReturnWindowTest(unittest.TestCase):
    def delivered(self, days_ago):
        return {'delivered_at': (datetime.now() - timedelta(days=days_ago)).isoformat()}

    def test_open_inside_window(self):
        self.assertTrue(shop.return_window_open(self.delivered(shop.BUSINESS['return_days'] - 1)))

    def test_closed_after_window(self):
        self.assertFalse(shop.return_window_open(self.delivered(shop.BUSINESS['return_days'] + 1)))

    def test_unknown_delivery_time_left_to_admin(self):
        self.assertTrue(shop.return_window_open({}))


if __name__ == '__main__':
    unittest.main()
