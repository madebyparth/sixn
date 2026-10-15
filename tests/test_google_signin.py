import base64
import json
import os
import sys
import time
import unittest
from unittest import mock
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app as shop
from services import auth_providers

CLIENT_ID = 'test-client.apps.googleusercontent.com'
ENV = {'GOOGLE_CLIENT_ID': CLIENT_ID, 'GOOGLE_CLIENT_SECRET': 'test-secret'}


def id_token(**overrides):
    claims = {'iss': 'https://accounts.google.com', 'aud': CLIENT_ID, 'sub': 'g-42', 'email': 'Asha@Gmail.com',
              'email_verified': True, 'name': 'Asha Rao', 'exp': time.time() + 600}
    claims.update(overrides)
    enc = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip('=')
    return f"{enc({'alg': 'RS256'})}.{enc(claims)}.sig"


class GoogleSignInTest(unittest.TestCase):
    def setUp(self):
        self.store = {'users.json': [], 'customers.json': [], 'products.json': [], 'carts.json': [],
                      'orders.json': [{'id': 'o1', 'user_id': None, 'customer_email': 'asha@gmail.com', 'status': 'cod_pending',
                                       'razorpay_order_id': 'COD-1', 'items': [], 'created_at': '2026-10-09', 'amount': 1499}]}
        name = os.path.basename
        for p in [mock.patch.object(shop, 'read_json', side_effect=lambda p: json.loads(json.dumps(self.store.get(name(p), [])))),
                  mock.patch.object(shop, 'write_json', side_effect=lambda p, d: self.store.__setitem__(name(p), json.loads(json.dumps(d)))),
                  mock.patch.dict(os.environ, ENV)]:
            p.start()
            self.addCleanup(p.stop)
        shop.app.config['WTF_CSRF_ENABLED'] = False
        self.client = shop.app.test_client()

    def start(self, next_path='/checkout'):
        r = self.client.get(f'/auth/google/login?next={next_path}')
        self.assertEqual(r.status_code, 302)
        q = parse_qs(urlparse(r.headers['Location']).query)
        return q

    def callback(self, token, state=None, **extra):
        with mock.patch.object(auth_providers.requests, 'post') as post:
            post.return_value = mock.Mock(ok=True, json=lambda: {'id_token': token})
            params = {'code': 'abc', **extra}
            if state:
                params['state'] = state
            return self.client.get('/auth/google/callback', query_string=params)

    def test_provider_enabled_with_keys(self):
        self.assertTrue(auth_providers.PROVIDERS['google'].enabled)

    def test_authorize_redirect(self):
        q = self.start()
        self.assertEqual(q['client_id'], [CLIENT_ID])
        self.assertEqual(q['scope'], ['openid email profile'])
        self.assertTrue(q['redirect_uri'][0].endswith('/auth/google/callback'))
        self.assertTrue(q['state'][0])

    def test_sign_in_links_guest_orders_and_returns_to_checkout(self):
        state = self.start()['state'][0]
        r = self.callback(id_token(), state)
        self.assertEqual(r.status_code, 302)
        self.assertTrue(r.headers['Location'].endswith('/checkout'))
        user = self.store['users.json'][0]
        self.assertEqual(user['email'], 'asha@gmail.com')
        self.assertEqual(user['social_ids'], {'google': 'g-42'})
        self.assertEqual(self.store['orders.json'][0]['user_id'], user['id'])
        # signed in: account pages work for a Google user with no phone number
        self.assertEqual(self.client.get('/api/auth/user').get_json()['firstName'], 'Asha')
        for page in ('/orders', '/settings', '/addresses', '/my-wallet', '/checkout'):
            self.assertEqual(self.client.get(page).status_code, 200, page)

    def test_wrong_audience_rejected(self):
        state = self.start()['state'][0]
        r = self.callback(id_token(aud='someone-else'), state)
        self.assertIn('auth_error', r.headers['Location'])
        self.assertEqual(self.store['users.json'], [])

    def test_expired_token_rejected(self):
        state = self.start()['state'][0]
        self.assertIn('auth_error', self.callback(id_token(exp=time.time() - 5), state).headers['Location'])

    def test_unverified_email_rejected(self):
        state = self.start()['state'][0]
        self.assertIn('auth_error', self.callback(id_token(email_verified=False), state).headers['Location'])
        self.assertEqual(self.store['users.json'], [])

    def test_state_mismatch_rejected(self):
        self.start()
        self.assertIn('auth_error', self.callback(id_token(), 'forged').headers['Location'])

    def test_user_cancelled(self):
        state = self.start()['state'][0]
        r = self.client.get('/auth/google/callback', query_string={'error': 'access_denied', 'state': state})
        self.assertIn('auth_error', r.headers['Location'])

    def test_open_redirect_blocked(self):
        state = self.start(next_path='//evil.example')['state'][0]
        r = self.callback(id_token(), state)
        self.assertNotIn('evil', r.headers['Location'])

    def test_google_button_shown(self):
        page = self.client.get('/checkout').get_data(as_text=True)
        self.assertIn('Continue with Google', page)
        self.assertIn('/auth/google/login?next=/checkout', page)


if __name__ == '__main__':
    unittest.main()
