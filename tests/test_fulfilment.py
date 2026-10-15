import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app as shop


def order(oid, status='paid', **extra):
    return dict({'id': oid, 'user_id': 'admin', 'status': status, 'payment_method': 'online', 'created_at': '2026-10-09',
                 'razorpay_order_id': 'order_' + oid, 'address': {'name': 'A', 'city': 'Pune', 'state': 'MH', 'zip': '411001'},
                 'items': [{'id': 1, 'code': 'NTS-01', 'name': 'NTS TEE BLACK', 'selectedSize': 'M', 'quantity': 2}]}, **extra)


class FulfilmentTest(unittest.TestCase):
    def setUp(self):
        self.store = {
            'users.json': [{'id': 'admin', 'first_name': 'A', 'last_name': 'B', 'is_admin': True, 'addresses': [], 'created_at': '2026-01-01'},
                           {'id': 'shopper', 'first_name': 'S', 'last_name': 'C', 'addresses': [], 'created_at': '2026-01-01'}],
            'products.json': [{'id': 1, 'code': 'NTS-01', 'name': 'NTS TEE BLACK', 'price': 1499,
                               'availableSizes': ['S', 'M', 'L'], 'stock': {'S': 5, 'M': 5, 'L': 5}}],
            'orders.json': [order('a', shiprocket_shipment_id=11, awb_code='AWB1'), order('b', status='shipped', stock_deducted=True, awb_code='AWB2')],
        }
        name = os.path.basename
        self.sr = mock.MagicMock()
        for p in [mock.patch.object(shop, 'read_json', side_effect=lambda p: json.loads(json.dumps(self.store.get(name(p), [])))),
                  mock.patch.object(shop, 'write_json', side_effect=lambda p, d: self.store.__setitem__(name(p), json.loads(json.dumps(d)))),
                  mock.patch.object(shop, 'shiprocket_service', self.sr)]:
            p.start()
            self.addCleanup(p.stop)
        shop.app.config['WTF_CSRF_ENABLED'] = False
        self.client = shop.app.test_client()
        self.login('admin')

    def login(self, uid):
        with self.client.session_transaction() as s:
            s['_user_id'] = uid
            s['_fresh'] = True

    def stock(self):
        return self.store['products.json'][0]['stock']

    def get(self, oid):
        return next(o for o in self.store['orders.json'] if o['id'] == oid)

    def test_stage_buckets(self):
        self.assertEqual(shop.fulfilment_stage(order('x')), 'to_ship')
        self.assertEqual(shop.fulfilment_stage(order('x', status='shipped')), 'in_transit')
        self.assertEqual(shop.fulfilment_stage(order('x', status='return_requested')), 'returns')
        self.assertEqual(shop.fulfilment_stage(order('x', status='cancelled')), 'closed')

    def test_page_admin_only(self):
        self.assertEqual(self.client.get('/admin/fulfilment').status_code, 200)
        self.login('shopper')
        self.assertEqual(self.client.get('/admin/fulfilment').status_code, 302)
        self.assertEqual(self.client.post('/api/admin/fulfilment/a/pickup').status_code, 302)
        self.assertNotIn('stock_deducted', self.get('a'))

    def test_pickup_deducts_stock_once(self):
        self.sr.generate_pickup.return_value = {'pickup_status': 1}
        r = self.client.post('/api/admin/fulfilment/a/pickup')
        self.assertEqual(r.status_code, 200, r.get_json())
        self.assertEqual(self.stock()['M'], 3)
        self.assertEqual(self.get('a')['status'], 'shipped')
        self.assertEqual(self.client.post('/api/admin/fulfilment/a/pickup').status_code, 400)
        self.assertEqual(self.stock()['M'], 3)

    def test_failed_pickup_changes_nothing(self):
        self.sr.generate_pickup.return_value = {'message': 'courier unavailable'}
        self.assertEqual(self.client.post('/api/admin/fulfilment/a/pickup').status_code, 502)
        self.assertEqual(self.stock()['M'], 5)
        self.assertEqual(self.get('a')['status'], 'paid')

    def test_restock_once_after_return(self):
        self.get('b')['status'] = 'returned'
        self.assertEqual(self.client.post('/api/admin/fulfilment/b/restock').status_code, 200)
        self.assertEqual(self.stock()['M'], 7)
        self.assertEqual(self.client.post('/api/admin/fulfilment/b/restock').status_code, 400)
        self.assertEqual(self.stock()['M'], 7)

    def test_sync_marks_delivered(self):
        self.sr.track_awb.return_value = {'tracking_data': {'shipment_track': [{'current_status': 'Delivered'}]}}
        r = self.client.post('/api/admin/fulfilment/sync')
        self.assertEqual(r.get_json()['delivered'], 1)
        self.assertEqual(self.get('b')['status'], 'delivered')
        self.assertIn('delivered_at', self.get('b'))

    def test_failed_delivery_flagged_and_rto_restockable(self):
        self.sr.track_awb.return_value = {'tracking_data': {'shipment_track': [{'current_status': 'Undelivered'}]}}
        self.client.post('/api/admin/fulfilment/sync')
        self.assertEqual(self.get('b')['status'], 'shipped')
        self.assertEqual(self.get('b')['delivery_issue'], 'UNDELIVERED')
        self.sr.track_awb.return_value = {'tracking_data': {'shipment_track': [{'current_status': 'RTO Delivered'}]}}
        self.client.post('/api/admin/fulfilment/sync')
        self.assertEqual(self.get('b')['status'], 'rto_received')
        self.assertEqual(shop.fulfilment_stage(self.get('b')), 'returns')
        self.assertEqual(self.client.post('/api/admin/fulfilment/b/restock').status_code, 200)
        self.assertEqual(self.stock()['M'], 7)

    def test_set_stock_validates(self):
        self.assertEqual(self.client.post('/api/admin/stock', json={'product_id': 1, 'size': 'S', 'on_hand': -1}).status_code, 400)
        self.assertEqual(self.client.post('/api/admin/stock', json={'product_id': 1, 'size': 'XL', 'on_hand': 3}).status_code, 404)
        self.assertEqual(self.client.post('/api/admin/stock', json={'product_id': 1, 'size': 'S', 'on_hand': 9}).status_code, 200)
        self.assertEqual(self.stock()['S'], 9)

    def test_label_saved(self):
        self.sr.generate_label.return_value = {'label_created': 1, 'label_url': 'https://x/label.pdf'}
        r = self.client.post('/api/admin/fulfilment/a/label')
        self.assertEqual(r.get_json()['label_url'], 'https://x/label.pdf')
        self.assertEqual(self.get('a')['label_url'], 'https://x/label.pdf')


if __name__ == '__main__':
    unittest.main()
