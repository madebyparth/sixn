import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app as shop
import migrate_guest_checkout
from services.auth_providers import Identity, PROVIDERS

PRODUCT = {'id': 1, 'code': 'NTS-01', 'name': 'NTS TEE BLACK', 'price': 1499, 'availableSizes': ['S', 'M', 'L']}
ITEM = {'id': 1, 'quantity': 1, 'selectedSize': 'M'}
ADDRESS = {'street': '1 MG Road', 'city': 'Pune', 'state': 'MH', 'zip': '411001', 'country': 'India'}


class GuestCheckoutTest(unittest.TestCase):
    def setUp(self):
        self.store = {'products.json': [PRODUCT], 'orders.json': [], 'users.json': [], 'customers.json': [],
                      'carts.json': [], 'coupons.json': [], 'pending_orders.json': []}
        name = os.path.basename
        self.sr = mock.MagicMock()
        self.sr.create_order.return_value = {'order_id': 9, 'shipment_id': 99}
        for p in [mock.patch.object(shop, 'read_json', side_effect=lambda p: json.loads(json.dumps(self.store.get(name(p), [])))),
                  mock.patch.object(shop, 'write_json', side_effect=lambda p, d: self.store.__setitem__(name(p), json.loads(json.dumps(d)))),
                  mock.patch.object(shop, 'shiprocket_service', self.sr)]:
            p.start()
            self.addCleanup(p.stop)
        shop.app.config['WTF_CSRF_ENABLED'] = False
        self.client = shop.app.test_client()

    def cod(self, **overrides):
        body = dict(items=[ITEM], address=ADDRESS, email='Asha@Gmail.com', name='Asha Rao', phone='+91 98765 43210', amount=1499)
        body.update(overrides)
        return self.client.post('/api/payment/cod_order', json=body)

    def test_guest_cod_order_keyed_by_email(self):
        r = self.cod()
        self.assertEqual(r.status_code, 200, r.get_json())
        order = self.store['orders.json'][0]
        self.assertEqual(order['customer_email'], 'asha@gmail.com')
        self.assertIsNone(order['user_id'])
        self.assertEqual(order['customer_phone'], '9876543210')
        self.assertEqual(self.store['customers.json'][0]['id'], 'asha@gmail.com')
        # the shopper who placed it can open the confirmation page; a stranger cannot
        self.assertEqual(self.client.get(f"/order-confirmation/{order['id']}").status_code, 200)
        self.assertEqual(shop.app.test_client().get(f"/order-confirmation/{order['id']}").status_code, 302)

    def test_order_status_by_email_and_number(self):
        self.cod()
        number = self.store['orders.json'][0]['razorpay_order_id']
        ok = self.client.get(f'/api/track-order?email=ASHA@gmail.com&order={number}')
        self.assertEqual(ok.get_json()['status'], 'cod_pending')
        self.assertEqual(self.client.get(f'/api/track-order?email=other@gmail.com&order={number}').status_code, 404)

    def test_invalid_email_name_phone_rejected(self):
        self.assertEqual(self.cod(email='not-an-email').status_code, 400)
        self.assertEqual(self.cod(name='').status_code, 400)
        self.assertEqual(self.cod(phone='12345').status_code, 400)
        self.assertEqual(self.store['orders.json'], [])

    def test_email_confirmation_flag_blocks_unverified_guests(self):
        with mock.patch.object(shop, 'REQUIRE_EMAIL_CONFIRMATION', True):
            self.assertEqual(self.cod().status_code, 400)

    def test_online_payment_disabled_without_razorpay(self):
        with mock.patch.object(shop, 'razorpay_client', None):
            r = self.client.post('/api/payment/create_order', json=dict(items=[ITEM], address=ADDRESS, email='a@b.co', name='A B', phone='9876543210'))
        self.assertEqual(r.status_code, 503)
        self.assertIn('not yet enabled', r.get_json()['error'])

    def test_checkout_page_open_to_guests(self):
        page = self.client.get('/checkout')
        self.assertEqual(page.status_code, 200)
        self.assertIn(b'id="contact-name"', page.data)

    def test_cart_saved_against_email(self):
        r = self.client.post('/api/cart', json={'email': 'Asha@Gmail.com', 'items': [ITEM]})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.store['carts.json'][0]['id'], 'asha@gmail.com')
        self.cod()
        self.assertEqual(self.store['carts.json'], [])

    def test_social_sign_in_links_guest_orders(self):
        self.cod()
        uid = shop.link_social_identity(Identity('google', 'g-123', 'asha@gmail.com', True, 'Asha Rao'))
        self.assertEqual(self.store['orders.json'][0]['user_id'], uid)
        self.assertEqual(self.store['customers.json'][0]['user_id'], uid)
        self.assertTrue(self.store['customers.json'][0]['email_verified'])
        # same Google account again -> same user, no duplicate
        self.assertEqual(shop.link_social_identity(Identity('google', 'g-123', 'asha@gmail.com', True)), uid)
        self.assertEqual(len(self.store['users.json']), 1)

    def test_unverified_social_email_rejected(self):
        with self.assertRaises(ValueError):
            shop.link_social_identity(Identity('meta', 'm-1', 'x@y.co', False))

    def test_providers_disabled_until_configured_and_implemented(self):
        for p in PROVIDERS.values():
            self.assertFalse(p.enabled)
        self.assertEqual(self.client.get('/auth/google/login').status_code, 404)

    def test_phone_signup_off_by_default(self):
        with mock.patch.object(shop, 'PHONE_LOGIN_ENABLED', False):
            r = self.client.post('/api/auth/signup', json={'firstName': 'A', 'phone': '+919876543210', 'password': 'secret1'})
        self.assertEqual(r.status_code, 403)

    def test_otp_not_sent_without_twilio(self):
        with mock.patch.object(shop, 'twilio_client', None), mock.patch.dict(os.environ, {'FLASK_DEBUG': '0'}):
            self.assertFalse(shop.send_otp_sms('+919876543210', '123456'))


class MigrationTest(unittest.TestCase):
    def test_orders_keyed_by_account_email_and_nothing_lost(self):
        users = [{'id': 'u1', 'first_name': 'A', 'last_name': 'B', 'email': 'A@x.com'}, {'id': 'u2', 'first_name': 'C', 'last_name': ''}]
        orders = [{'id': 'o1', 'user_id': 'u1'}, {'id': 'o2', 'user_id': 'u2'}, {'id': 'o3', 'user_id': None, 'customer_email': 'g@x.com'}]
        out, customers, stats = migrate_guest_checkout.migrate(orders, users, [])
        self.assertEqual(len(out), 3)
        self.assertEqual(out[0]['customer_email'], 'a@x.com')
        self.assertNotIn('customer_email', out[1])
        self.assertEqual(sorted(c['id'] for c in customers), ['a@x.com', 'g@x.com'])
        self.assertEqual(stats, {'orders_keyed': 1, 'orders_without_email': 1, 'customers_added': 2})
        _, again, stats2 = migrate_guest_checkout.migrate(out, users, customers)
        self.assertEqual(stats2['orders_keyed'], 0)
        self.assertEqual(len(again), 2)


if __name__ == '__main__':
    unittest.main()
