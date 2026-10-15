"""One-off, idempotent: key existing orders by email for guest checkout. Nothing is deleted.

- Orders without `customer_email` get it (plus name/phone) from the account that placed them, when that account has an email.
- Every email seen gets a `customers` record linked to its user id.
Usage: python migrate_guest_checkout.py [--dry-run]
"""
import os
import sys
from datetime import datetime

from dotenv import load_dotenv

load_dotenv()
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from utils.db import mongo_db, read_json, write_json
from utils.security import decrypt_data


def migrate(orders, users, customers):
    users_by_id = {u['id']: u for u in users}
    by_email = {c['id']: c for c in customers}
    stats = {'orders_keyed': 0, 'orders_without_email': 0, 'customers_added': 0}
    for o in orders:
        user = users_by_id.get(o.get('user_id')) or {}
        email = (o.get('customer_email') or user.get('email') or '').strip().lower()
        if not email:
            stats['orders_without_email'] += 1  # phone-only account; stays reachable through user_id
            continue
        if not o.get('customer_email'):
            o['customer_email'] = email
            o.setdefault('customer_name', f"{user.get('first_name', '')} {user.get('last_name', '')}".strip())
            if user.get('phone_encrypted') and not o.get('customer_phone'):
                phone = decrypt_data(user['phone_encrypted'], 'phone') or ''
                o['customer_phone'] = ''.join(ch for ch in phone if ch.isdigit())[-10:] or None
            stats['orders_keyed'] += 1
        if email not in by_email:
            by_email[email] = {'id': email, 'email': email, 'name': o.get('customer_name', ''),
                               'user_id': o.get('user_id'), 'email_verified': False,
                               'created_at': o.get('created_at') or datetime.now().isoformat()}
            stats['customers_added'] += 1
    return orders, list(by_email.values()), stats


if __name__ == '__main__':
    if mongo_db is None:
        sys.exit("MongoDB not reachable; set MONGO_URI in .env")
    orders, customers, stats = migrate(read_json('data/orders.json'), read_json('data/users.json'),
                                       read_json('data/customers.json'))
    print(stats)
    if '--dry-run' not in sys.argv:
        write_json('data/orders.json', orders)
        write_json('data/customers.json', customers)
        print("Migration written.")
