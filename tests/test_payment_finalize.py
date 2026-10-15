import hashlib
import hmac
import json
import os
import sys
import unittest
from unittest import mock

import razorpay

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app as shop

SECRET = 'whsec_test'


def pending(rp_id='order_A', coupon=None):
    return {'id': 'int-' + rp_id, 'user_id': 'u1', 'razorpay_order_id': rp_id, 'items': [], 'amount': 1499,
            'status': 'pending_payment', 'coupon_applied': coupon, 'address': {}, 'created_at': '2099-01-01T00:00:00'}


class PaymentFinalizeTest(unittest.TestCase):
    def setUp(self):
        self.store = {'orders.json': [], 'pending_orders.json': [], 'users.json': [],
                      'coupons.json': [{'code': 'X', 'limit_type': 'global', 'max_uses': 1, 'times_used': 1}]}
        name = lambda p: os.path.basename(p)
        patches = [
            mock.patch.object(shop, 'read_json', side_effect=lambda p: json.loads(json.dumps(self.store.get(name(p), [])))),
            mock.patch.object(shop, 'write_json', side_effect=lambda p, d: self.store.__setitem__(name(p), json.loads(json.dumps(d)))),
            mock.patch.object(shop, 'load_user', return_value=None),  # skips Shiprocket
            mock.patch.object(shop, 'razorpay_client', razorpay.Client(auth=('key', 'secret'))),
            mock.patch.dict(os.environ, {'RAZORPAY_WEBHOOK_SECRET': SECRET}),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        shop.app.config['WTF_CSRF_ENABLED'] = True  # webhook must work with CSRF on
        self.client = shop.app.test_client()

    def webhook(self, event, signature=None):
        body = json.dumps(event)
        sig = signature or hmac.new(SECRET.encode(), body.encode(), hashlib.sha256).hexdigest()
        return self.client.post('/api/payment/webhook', data=body, content_type='application/json',
                                headers={'X-Razorpay-Signature': sig})

    def captured(self, rp_id):
        return {'event': 'payment.captured', 'payload': {'payment': {'entity': {'id': 'pay_1', 'order_id': rp_id}}}}

    def test_pending_order_is_persisted(self):
        shop._save_pending_order(pending())
        self.assertEqual(shop._find_pending_order('order_A')['id'], 'int-order_A')

    def test_webhook_records_paid_order_without_browser(self):
        shop._save_pending_order(pending())
        r = self.webhook(self.captured('order_A'))
        self.assertEqual(r.status_code, 200)
        self.assertEqual([o['status'] for o in self.store['orders.json']], ['paid'])
        self.assertEqual(self.store['pending_orders.json'], [])

    def test_finalize_is_idempotent(self):
        shop._save_pending_order(pending())
        shop.finalize_paid_order(pending(), 'pay_1')
        shop.finalize_paid_order(pending(), 'pay_1')
        self.webhook(self.captured('order_A'))
        self.assertEqual(len(self.store['orders.json']), 1)

    def test_bad_signature_rejected(self):
        shop._save_pending_order(pending())
        r = self.webhook(self.captured('order_A'), signature='forged')
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.store['orders.json'], [])

    def test_coupon_over_limit_still_records_paid_order(self):
        shop.finalize_paid_order(pending(coupon='X'), 'pay_1')
        self.assertTrue(self.store['orders.json'][0].get('coupon_over_limit'))


if __name__ == '__main__':
    unittest.main()
