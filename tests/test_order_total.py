import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app as shop

PRODUCTS = [
    {"id": 1, "name": "NTS TEE 01", "price": 1499, "inStock": True,
     "availableSizes": ["S", "M", "L"], "soldOutSizes": ["L"]},
    {"id": 2, "name": "NTS TEE 02", "price": 1499, "inStock": False,
     "availableSizes": ["S", "M", "L"]},
]


def total(items, shipping_cost=0, products=PRODUCTS, orders=()):
    data = {'products.json': products, 'orders.json': list(orders)}
    with mock.patch.object(shop, 'read_json', side_effect=lambda p: data.get(os.path.basename(p), [])):
        return shop.calculate_order_total(items, None, None, shipping_cost=shipping_cost)


STOCKED = [{"id": 3, "name": "NTS TEE 03", "price": 1499, "availableSizes": ["S", "M", "L"], "stock": {"S": 2, "M": 0}}]


class OrderTotalTest(unittest.TestCase):
    def test_valid_size_priced_server_side(self):
        r = total([{"id": 1, "quantity": 2, "selectedSize": "M", "price": 1}])
        self.assertEqual(r['final_amount'], 2998)

    def test_rejects_unknown_size(self):
        with self.assertRaises(ValueError):
            total([{"id": 1, "quantity": 1, "selectedSize": "XL"}])

    def test_rejects_missing_size(self):
        with self.assertRaises(ValueError):
            total([{"id": 1, "quantity": 1}])

    def test_rejects_sold_out_size(self):
        with self.assertRaises(ValueError):
            total([{"id": 1, "quantity": 1, "selectedSize": "L"}])

    def test_rejects_out_of_stock_product(self):
        with self.assertRaises(ValueError):
            total([{"id": 2, "quantity": 1, "selectedSize": "S"}])

    def test_rejects_non_positive_quantity(self):
        with self.assertRaises(ValueError):
            total([{"id": 1, "quantity": -1, "selectedSize": "M"}])

    def test_client_cannot_lower_or_add_shipping_above_free_threshold(self):
        r = total([{"id": 1, "quantity": 1, "selectedSize": "S"}], shipping_cost=-500)
        self.assertEqual(r['final_amount'], 1499)


class InventoryTest(unittest.TestCase):
    def test_within_stock_ok(self):
        self.assertEqual(total([{"id": 3, "quantity": 2, "selectedSize": "S"}], products=STOCKED)['subtotal'], 2998)

    def test_oversell_rejected_across_cart_lines(self):
        with self.assertRaises(ValueError):
            total([{"id": 3, "quantity": 1, "selectedSize": "S"}, {"id": 3, "quantity": 2, "selectedSize": "S"}], products=STOCKED)

    def test_zero_stock_is_sold_out(self):
        with self.assertRaises(ValueError):
            total([{"id": 3, "quantity": 1, "selectedSize": "M"}], products=STOCKED)

    def test_untracked_size_is_unlimited(self):
        total([{"id": 3, "quantity": 9, "selectedSize": "L"}], products=STOCKED)

    def test_open_orders_reserve_until_shipped(self):
        open_order = {"status": "paid", "items": [{"id": 3, "selectedSize": "S", "quantity": 2}]}
        with self.assertRaises(ValueError):
            total([{"id": 3, "quantity": 1, "selectedSize": "S"}], products=STOCKED, orders=[open_order])
        shipped = dict(open_order, stock_deducted=True)
        cancelled = dict(open_order, status="cancelled")
        total([{"id": 3, "quantity": 2, "selectedSize": "S"}], products=STOCKED, orders=[shipped, cancelled])


if __name__ == '__main__':
    unittest.main()
