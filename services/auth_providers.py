"""Pluggable sign-in providers (Google, Meta).

Checkout and orders never talk to a provider directly: a provider only turns an OAuth callback into an Identity,
and app.link_social_identity() attaches that identity to the customer record with the same email, so orders placed
through guest checkout show up in the signed-in account.

Google is implemented (fill GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET to enable). For Meta: implement authorize_url() and
fetch_identity(), then set implemented = True.
"""
import base64
import json
import time
from dataclasses import dataclass
from urllib.parse import urlencode

import requests

from utils.config import env_value


@dataclass
class Identity:
    provider: str
    provider_user_id: str
    email: str
    email_verified: bool
    name: str = ''


class AuthProvider:
    name = ''
    label = ''
    env_keys = ()
    implemented = False  # flip once authorize_url / fetch_identity are written and tested

    def missing_keys(self):
        return [k for k in self.env_keys if not env_value(k)]

    @property
    def enabled(self):
        return self.implemented and not self.missing_keys()

    def disabled_reason(self):
        if self.missing_keys():
            return f"{', '.join(self.missing_keys())} not set"
        if not self.implemented:
            return 'provider not implemented yet'
        return ''

    def authorize_url(self, state, redirect_uri):
        raise NotImplementedError

    def fetch_identity(self, code, redirect_uri):
        """Exchange the OAuth code for an Identity. Must only report email_verified=True when the provider says so."""
        raise NotImplementedError


class GoogleProvider(AuthProvider):
    """Google Sign-In (OpenID Connect authorization-code flow).

    The ID token is fetched directly from Google's token endpoint over TLS with our client secret, so per OpenID
    Connect Core 3.1.3.7 TLS stands in for the signature check; the claims (aud, iss, exp, email_verified) are
    still validated here.
    """
    name = 'google'
    label = 'Google'
    env_keys = ('GOOGLE_CLIENT_ID', 'GOOGLE_CLIENT_SECRET')
    implemented = True
    AUTH_URL = 'https://accounts.google.com/o/oauth2/v2/auth'
    TOKEN_URL = 'https://oauth2.googleapis.com/token'
    ISSUERS = ('https://accounts.google.com', 'accounts.google.com')

    def authorize_url(self, state, redirect_uri):
        return self.AUTH_URL + '?' + urlencode({
            'client_id': env_value('GOOGLE_CLIENT_ID'), 'redirect_uri': redirect_uri, 'response_type': 'code',
            'scope': 'openid email profile', 'state': state, 'prompt': 'select_account',
        })

    def fetch_identity(self, code, redirect_uri):
        if not code:
            raise ValueError('Google sign-in was cancelled.')
        try:
            res = requests.post(self.TOKEN_URL, timeout=15, data={
                'code': code, 'client_id': env_value('GOOGLE_CLIENT_ID'), 'client_secret': env_value('GOOGLE_CLIENT_SECRET'),
                'redirect_uri': redirect_uri, 'grant_type': 'authorization_code'})
            token = res.json().get('id_token') if res.ok else None
        except (requests.RequestException, ValueError):
            token = None
        if not token:
            raise ValueError('Could not complete Google sign-in. Please try again.')
        claims = _jwt_claims(token)
        if (claims.get('aud') != env_value('GOOGLE_CLIENT_ID') or claims.get('iss') not in self.ISSUERS
                or float(claims.get('exp', 0)) < time.time() or not claims.get('sub')):
            raise ValueError('Google sign-in could not be verified.')
        return Identity(provider=self.name, provider_user_id=str(claims['sub']), email=claims.get('email', ''),
                        email_verified=claims.get('email_verified') in (True, 'true'), name=claims.get('name', ''))


def _jwt_claims(token):
    """Payload of a JWT (no signature check: only for tokens received directly from the issuer over TLS)."""
    try:
        payload = token.split('.')[1]
        return json.loads(base64.urlsafe_b64decode(payload + '=' * (-len(payload) % 4)))
    except (IndexError, ValueError):
        raise ValueError('Google sign-in could not be verified.')


class MetaProvider(AuthProvider):
    name = 'meta'
    label = 'Facebook / Instagram'
    env_keys = ('META_APP_ID', 'META_APP_SECRET')
    # Later: Facebook Login dialog + Graph API /me?fields=id,name,email. Meta does not always return an email;
    # fetch_identity must reject accounts without one, since email is the customer key.


PROVIDERS = {p.name: p for p in (GoogleProvider(), MetaProvider())}
