from flask import Flask, render_template, redirect, url_for, send_from_directory, request, jsonify, session
from werkzeug.utils import secure_filename
from werkzeug.utils import secure_filename
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from flask_bcrypt import Bcrypt
from twilio.rest import Client
import razorpay
from flask_wtf.csrf import CSRFProtect
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
import os
import json
import time
import random
import re
import uuid
import hmac
import hashlib
from functools import wraps
from datetime import datetime, timedelta
import base64
import threading
from dotenv import load_dotenv
from utils.config import env_value, env_flag
from urllib.parse import urlencode

coupon_lock = threading.Lock()
user_lock = threading.Lock()
order_lock = threading.RLock()  # re-entrant: refund helpers take it again while sync_order_status holds it
otp_lock = threading.Lock()
rating_lock = threading.Lock()

load_dotenv()

app = Flask(__name__)
from services.shiprocket_service import ShiprocketService

# Initialize Shiprocket Service
shiprocket_service = ShiprocketService()

from utils.security import encrypt_data, decrypt_data, hash_data, hash_otp, verify_otp

@app.template_filter('b64encode')
def b64encode_filter(s):
    if s is None: return ""
    return base64.b64encode(s.encode('utf-8')).decode('utf-8')
    
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY')
app.config['ADMIN_USER_ID'] = os.environ.get('ADMIN_USER_ID')
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SECURE'] = True # Set to True for production/HTTPS
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=7)

# Upload Config
UPLOAD_FOLDER = os.path.join(app.root_path, 'static', 'uploads', 'products')
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp', 'mp4', 'webm'}
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp', 'mp4', 'webm'}
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
PROFILE_UPLOAD_FOLDER = os.path.join(app.root_path, 'static', 'uploads', 'profiles')
os.makedirs(PROFILE_UPLOAD_FOLDER, exist_ok=True)
app.config['PROFILE_UPLOAD_FOLDER'] = PROFILE_UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 64 * 1024 * 1024  # Global max, specific routes can check stricter limits for app generally (PFP restricted to 5MB in logic)

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# --- SECURITY & UTILS ---
bcrypt = Bcrypt(app)
login_manager = LoginManager(app)
login_manager.login_view = 'index' # Changed to index as login is a popup/modal mostly, or 'index' checks auth
# login_manager.login_view = 'login' # If you have a dedicated login page

# 1. CSRF Protection
csrf = CSRFProtect(app)

# 2. Rate Limiting
limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=["2000 per day", "500 per hour"],
    storage_uri="memory://"
)

# Twilio Config
TWILIO_PHONE = env_value('TWILIO_PHONE')
TWILIO_SID = env_value('TWILIO_SID')
TWILIO_TOKEN = env_value('TWILIO_TOKEN')
twilio_client = None

if TWILIO_SID and TWILIO_TOKEN and TWILIO_PHONE:
    try:
        twilio_client = Client(TWILIO_SID, TWILIO_TOKEN)
    except Exception as e:
        print(f"Twilio Init Error: {e}")

# Razorpay Config
RAZORPAY_KEY = env_value('RAZORPAY_KEY')
RAZORPAY_SECRET = env_value('RAZORPAY_SECRET')
razorpay_client = None

if RAZORPAY_KEY and RAZORPAY_SECRET:
    try:
        razorpay_client = razorpay.Client(auth=(RAZORPAY_KEY, RAZORPAY_SECRET))
    except Exception as e:
        print(f"Razorpay Init Error: {e}")

# JSON DB PATHS
DATA_DIR = os.path.join(app.root_path, 'data')
USERS_FILE = os.path.join(DATA_DIR, 'users.json')
OTPS_FILE = os.path.join(DATA_DIR, 'otps.json')
ORDERS_FILE = os.path.join(DATA_DIR, 'orders.json')
RATINGS_FILE = os.path.join(DATA_DIR, 'ratings.json')
REPORTS_FILE = os.path.join(DATA_DIR, 'reports.json')
WALLET_TRANSACTIONS_FILE = os.path.join(DATA_DIR, 'wallet_transactions.json')
NOTIFICATIONS_FILE = os.path.join(DATA_DIR, 'notifications.json')
COUPONS_FILE = os.path.join(DATA_DIR, 'coupons.json')

from utils.db import read_json, write_json


def cleanup_otps(otps):
    """Remove expired OTPs (older than 10 mins/600s) to save space."""
    now = time.time()
    # 600 seconds = 10 minutes.
    # We check keys or iterate. 'expires_at' is usually set to +300s (5 mins).
    # So if now > expires_at + 300 (buffer) or just strictly expires_at?
    # User asked "autoclear ... after 10 mins".
    # Let's clean anything where expires_at < now (expired) 
    # OR create_time + 600 < now.
    # Our records have 'expires_at'. If it's expired, it's useless.
    # Let's delete anything expired.
    
    to_delete = []
    for phone_hash, record in otps.items():
        if float(record['expires_at']) < now:
            to_delete.append(phone_hash)
            
    for k in to_delete:
        del otps[k]
    
    return otps

@app.route('/settings')
@login_required
def settings():
    return render_template('settings.html', user=current_user)

@app.route('/api/user/update', methods=['POST'])
@login_required
@limiter.limit("10 per hour") # Limit profile updates preventing spam
def update_user_profile():
    data = request.json
    first_name = data.get('first_name')
    last_name = data.get('last_name')
    email = data.get('email')
    
    # Input Length Validation
    if len(first_name) > 50 or len(last_name) > 50:
         return jsonify({'error': 'Name too long (max 50 chars)'}), 400
    if email and len(email) > 100:
         return jsonify({'error': 'Email too long (max 100 chars)'}), 400
    
    # Validation & Sanitization
    if not first_name or not last_name:
         return jsonify({'error': 'First and Last name are required'}), 400
         
    # Basic email validation
    if email and '@' not in email:
         return jsonify({'error': 'Invalid email format'}), 400
         
    with user_lock:
        users = read_json(USERS_FILE)
    for u in users:
        if u['id'] == current_user.id:
            u['first_name'] = first_name.strip()
            u['last_name'] = last_name.strip()
            u['email'] = email.strip() if email else ""
            
            # Phone update logic with OTP
            if data.get('phone'):
                 new_phone = data.get('phone').strip()
                 
                 if not validate_phone(new_phone):
                     return jsonify({'error': 'Invalid phone number format'}), 400
                     
                 current_phone = decrypt_data(u.get('phone_encrypted'), 'phone')
                 
                 if new_phone != current_phone:
                     # Generate OTP
                     otp = str(random.randint(100000, 999999))
                     # Store in session for verification (HASHED OTP)
                     session['phone_update_pending'] = {
                         'phone': new_phone,
                         'otp_hash': hash_otp(otp),
                         'timestamp': time.time()
                     }
                     with otp_lock:
                         # Send OTP logic
                         if not send_otp_sms(new_phone, otp):
                             return jsonify({'error': 'SMS verification is not available right now.'}), 503
                     
                     with user_lock:
                        write_json(USERS_FILE, users)
                     return jsonify({'status': 'otp_required', 'message': 'OTP sent to new phone number'})
            
            with user_lock:
                write_json(USERS_FILE, users)
            return jsonify({'status': 'success'})
            
    return jsonify({'error': 'User not found'}), 404

@app.route('/api/user/verify-phone-update', methods=['POST'])
@login_required
def verify_phone_update_otp():
    data = request.json
    otp = data.get('otp')
    
    pending_update = session.get('phone_update_pending')
    
    if not pending_update or not otp:
        return jsonify({'error': 'No pending update or OTP missing'}), 400
        
    # Verify OTP
    if verify_otp(otp, pending_update.get('otp_hash')):
        # Check expiry (e.g. 5 mins)
        if time.time() - pending_update['timestamp'] > 300:
             session.pop('phone_update_pending', None)
             return jsonify({'error': 'OTP expired'}), 400
             
        # Apply Update
        users = read_json(USERS_FILE)
        for u in users:
            if u['id'] == current_user.id:
                # Encrypt new phone
                u['phone_encrypted'] = encrypt_data(pending_update['phone'], 'phone')
                u['phone_hash'] = hash_data(pending_update['phone'])
                
                write_json(USERS_FILE, users)
                session.pop('phone_update_pending', None)
                return jsonify({'status': 'success', 'message': 'Phone number updated successfully'})
                
        return jsonify({'error': 'User not found'}), 404
    else:
        return jsonify({'error': 'Invalid OTP'}), 400

@app.route('/api/user/pfp', methods=['POST'])
@login_required
def upload_pfp():
    """Secure Profile Picture Upload"""
    if 'profile_image' not in request.files:
        return jsonify({'error': 'No file part'}), 400
    file = request.files['profile_image']
    if file.filename == '':
        return jsonify({'error': 'No selected file'}), 400
        
    if file and allowed_file(file.filename):
        # 1. Size Check (5MB)
        file.seek(0, os.SEEK_END)
        size = file.tell()
        file.seek(0)
        
        if size > 5 * 1024 * 1024:
             return jsonify({'error': 'File too large. Max 5MB allowed.'}), 400
             
        # 2. Secure Filename & Rename
        ext = file.filename.rsplit('.', 1)[1].lower()
        if ext not in ['jpg', 'jpeg', 'png', 'webp']:
             return jsonify({'error': 'Only JPG, PNG, and WebP allowed for profiles'}), 400
             
        filename = secure_filename(f"pfp_{current_user.id}_{int(time.time())}.{ext}")
        filepath = os.path.join(app.config['PROFILE_UPLOAD_FOLDER'], filename)
        
        # 3. Save
        try:
            file.save(filepath)
        except Exception as e:
            return jsonify({'error': 'Failed to save file'}), 500
        
        # 4. Update Database
        users = read_json(USERS_FILE)
        public_path = f"/static/uploads/profiles/{filename}"
        
        for u in users:
            if u['id'] == current_user.id:
                # Optional: Delete old PFP if exists to save space (skipping for now)
                u['profile_image'] = public_path
                write_json(USERS_FILE, users)
                return jsonify({'status': 'success', 'image_url': public_path})
    else:
        return jsonify({'error': 'Invalid file type'}), 400
                
    return jsonify({'error': 'Upload failed'}), 500

# --- USER MODEL ---
class User(UserMixin):
    def __init__(self, id, first_name, last_name, phone_encrypted, is_verified, address_encrypted=None, email=None, profile_image=None, addresses=None, default_address_id=None, is_admin=False, wallet_balance=0.0):
        self.id = id
        self.first_name = first_name
        self.last_name = last_name
        self._phone_encrypted = phone_encrypted
        self.is_verified = is_verified
        self._address_encrypted = address_encrypted
        self.email = email
        self.profile_image = profile_image
        self._addresses = addresses or []
        self.default_address_id = default_address_id
        self.is_admin = is_admin
        self.wallet_balance = float(wallet_balance)

    @property
    def phone(self):
        return decrypt_data(self._phone_encrypted, 'phone') if self._phone_encrypted else None

    @property
    def address(self):
        if not self._address_encrypted:
            return {}
        try:
            return json.loads(decrypt_data(self._address_encrypted, 'address'))
        except:
             return {}

    @property
    def addresses(self):
         decrypted_list = []
         for addr in self._addresses:
             new_addr = addr.copy()
             if 'data_encrypted' in addr:
                 try:
                     pii = json.loads(decrypt_data(addr['data_encrypted'], 'address'))
                     new_addr.update(pii)
                 except:
                     pass
             decrypted_list.append(new_addr)
         return decrypted_list

@login_manager.user_loader
def load_user(user_id):
    users = read_json(USERS_FILE)
    user_data = next((u for u in users if u['id'] == user_id), None)
    if user_data:
        # Check if user is admin based on ADMIN_USER_ID from .env
        admin_user_id = app.config.get('ADMIN_USER_ID') or os.environ.get('ADMIN_USER_ID')
        is_admin = (user_id == admin_user_id) or user_data.get('is_admin', False)
        
        return User(
            id=user_data['id'], 
            first_name=user_data['first_name'], 
            last_name=user_data['last_name'], 
            phone_encrypted=user_data.get('phone_encrypted'), 
            is_verified=user_data.get('is_verified'),
            address_encrypted=user_data.get('address_encrypted'),
            email=user_data.get('email'),
            profile_image=user_data.get('profile_image'),
            addresses=user_data.get('addresses'),
            default_address_id=user_data.get('default_address_id'),
            is_admin=is_admin,
            wallet_balance=user_data.get('wallet_balance', 0.0)
        )
    return None

def send_otp_sms(phone, otp):
    """Returns False when the SMS could not be sent. Without Twilio, OTP is off (dev mode prints it to the console)."""
    if not twilio_client:
        if env_flag('FLASK_DEBUG'):
            print(f"[DEV] Twilio not configured. OTP for {phone}: {otp}")
            return True
        print("[OTP] Not sent: Twilio is not configured (TWILIO_SID / TWILIO_TOKEN / TWILIO_PHONE).")
        return False
    try:
        twilio_client.messages.create(
            body=f"Your NTS Verification Code is: {otp}",
            from_=TWILIO_PHONE,
            to=phone
        )
        return True
    except Exception as e:
        print(f"Failed to send SMS: {e}")
        return False

def validate_phone(phone):
    return re.match(r'^\+[1-9]\d{1,14}$', phone)

# --- WALLET & REFUND HELPERS ---

def add_wallet_balance(user_id, amount, reason, order_id=None):
    """
    Atomically adds to user wallet balance and logs transaction.
    """
    # Use user_lock to prevent race conditions on wallet balance
    with user_lock:
        users = read_json(USERS_FILE)
        user_idx = next((i for i, u in enumerate(users) if u['id'] == user_id), -1)
        
        if user_idx == -1:
            return False, "User not found"
            
        current_balance = users[user_idx].get('wallet_balance', 0.0)
        new_balance = current_balance + float(amount)
        users[user_idx]['wallet_balance'] = new_balance
        write_json(USERS_FILE, users)
    
    # Log Transaction
    try:
        wallet_transactions = read_json(WALLET_TRANSACTIONS_FILE)
        if not isinstance(wallet_transactions, list):
             wallet_transactions = []

        txn = {
            'id': str(uuid.uuid4()),
            'user_id': user_id,
            'amount': float(amount),
            'type': 'credit' if amount > 0 else 'debit',
            'reason': reason,
            'order_id': order_id,
            'balance_after': new_balance,
            'created_at': datetime.now().isoformat()
        }
        wallet_transactions.append(txn)
        write_json(WALLET_TRANSACTIONS_FILE, wallet_transactions)
    except Exception as e:
        print(f"Error logging wallet transaction: {e}")
    
    return True, new_balance

def get_wallet_transactions(user_id):
    transactions = read_json(WALLET_TRANSACTIONS_FILE)
    if not isinstance(transactions, list): return []
    # Sort by recent first
    user_txns = [t for t in transactions if t['user_id'] == user_id]
    user_txns.sort(key=lambda x: x['created_at'], reverse=True)
    return user_txns

# --- NOTIFICATIONS HELPER ---
NOTIFICATIONS_FILE = os.path.join(app.root_path, 'data', 'notifications.json')

def get_notifications(user_id=None, unread_only=False):
    """
    Get notifications. Can filter by user_id and unread status.
    Returns a list of notifications sorted by created_at (descending).
    """
    notifications = read_json(NOTIFICATIONS_FILE)
    if not isinstance(notifications, list):
        return []
    
    # Filter by user_id if provided
    if user_id:
        notifications = [n for n in notifications if n.get('user_id') == user_id]
    
    # Filter by unread if requested
    if unread_only:
        notifications = [n for n in notifications if not n.get('is_read', False)]
    
    # Sort by recent first
    notifications.sort(key=lambda x: x.get('created_at', ''), reverse=True)
    return notifications

# Context processor to make get_notifications available in all templates
@app.context_processor
def inject_feature_flags():
    return dict(phone_login_enabled=PHONE_LOGIN_ENABLED, payments_enabled=razorpay_client is not None,
                social_providers=[p for p in AUTH_PROVIDERS.values() if p.enabled])


@app.context_processor
def inject_notifications_helper():
    return dict(get_notifications=get_notifications, support_email=BUSINESS['email'], business=BUSINESS)


# Shown on policy/contact pages; Razorpay live activation needs real values here (set them in .env).
BUSINESS = {
    'name': 'NTS — Not That Simple',
    'legal_name': os.environ.get('BUSINESS_LEGAL_NAME', ''),
    'address': os.environ.get('BUSINESS_ADDRESS', ''),
    'email': os.environ.get('SUPPORT_EMAIL', ''),
    'phone': os.environ.get('SUPPORT_PHONE', ''),
    'gstin': os.environ.get('BUSINESS_GSTIN', ''),
    'jurisdiction': os.environ.get('BUSINESS_JURISDICTION_CITY', ''),
    'return_days': int(os.environ.get('RETURN_WINDOW_DAYS', '7')),
    # Grievance officer is mandatory under the Consumer Protection (E-Commerce) Rules, 2020
    'grievance_name': os.environ.get('GRIEVANCE_OFFICER_NAME', ''),
    'grievance_email': os.environ.get('GRIEVANCE_OFFICER_EMAIL', '') or os.environ.get('SUPPORT_EMAIL', ''),
}
for _key in ('legal_name', 'address', 'email', 'phone', 'grievance_name'):
    if not BUSINESS[_key]:
        print(f"[CONFIG] BUSINESS {_key} is empty — policy pages will show a gap. Set it in .env before going live.")


PHONE_LOGIN_ENABLED = env_flag('PHONE_LOGIN_ENABLED')  # customer phone+OTP login; admins can always sign in
REQUIRE_EMAIL_CONFIRMATION = env_flag('REQUIRE_EMAIL_CONFIRMATION')  # needs an email sender before turning on
CUSTOMERS_FILE = os.path.join(DATA_DIR, 'customers.json')
CARTS_FILE = os.path.join(DATA_DIR, 'carts.json')
EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$')

from services.auth_providers import PROVIDERS as AUTH_PROVIDERS


def integration_status():
    """(name, enabled, what is missing / what happens) for the startup log and preflight."""
    def missing(*keys):
        return ', '.join(k for k in keys if not env_value(k))
    rows = [
        ('Razorpay online payments (UPI / Google Pay / cards)', razorpay_client is not None,
         f"{missing('RAZORPAY_KEY', 'RAZORPAY_SECRET')} missing -> checkout shows 'Online payments not yet enabled'; COD still works"),
        ('Razorpay webhook', bool(env_value('RAZORPAY_WEBHOOK_SECRET')),
         "RAZORPAY_WEBHOOK_SECRET missing -> orders rely on the browser /verify call only"),
        ('Twilio SMS OTP', twilio_client is not None,
         f"{missing('TWILIO_SID', 'TWILIO_TOKEN', 'TWILIO_PHONE')} missing -> OTP sending is off (admin sign-in needs it)"),
        ('Shiprocket', bool(env_value('SHIPROCKET_EMAIL') and env_value('SHIPROCKET_PASSWORD')),
         "SHIPROCKET_EMAIL / SHIPROCKET_PASSWORD missing -> no serviceability checks, labels or pickups"),
        ('Phone login for customers', PHONE_LOGIN_ENABLED, "PHONE_LOGIN_ENABLED is off -> guest checkout by email"),
    ]
    for provider in AUTH_PROVIDERS.values():
        rows.append((f"{provider.label} sign-in", provider.enabled, provider.disabled_reason()))
    return rows


def log_integration_status():
    print("[STARTUP] Integrations:")
    for name, enabled, why in integration_status():
        print(f"  - {name}: {'enabled' if enabled else 'DISABLED (' + why + ')'}")
    if REQUIRE_EMAIL_CONFIRMATION:
        print("  ! REQUIRE_EMAIL_CONFIRMATION is on, but no confirmation email is sent yet: guest checkout will be refused.")


def normalize_email(value):
    email = (value or '').strip().lower()
    return email if len(email) <= 254 and EMAIL_RE.match(email) else None


class Buyer:
    """Who is placing an order: a signed-in user, or a guest identified by email (SPEC-guest-checkout.md)."""
    def __init__(self, id=None, email=None, first_name='', last_name='', phone=None, wallet_balance=0.0):
        self.id, self.email, self.phone = id, email, phone
        self.first_name, self.last_name = first_name or '', last_name or ''
        self.wallet_balance = float(wallet_balance or 0)

    @property
    def name(self):
        return f"{self.first_name} {self.last_name}".strip()

    @property
    def key(self):
        """Identity for coupon limits and rate limits."""
        return self.id or f"email:{self.email}"

    def order_fields(self):
        return {'customer_email': self.email, 'customer_name': self.name, 'customer_phone': self.phone}


def order_key(order):
    return order.get('user_id') or f"email:{order.get('customer_email')}"


def upsert_customer(email, name, user_id=None):
    with user_lock:
        customers = read_json(CUSTOMERS_FILE)
        customer = next((c for c in customers if c['id'] == email), None)
        if customer is None:
            customer = {'id': email, 'email': email, 'name': name, 'user_id': user_id,
                        'email_verified': False, 'created_at': datetime.now().isoformat()}
            customers.append(customer)
        else:
            customer['name'] = name or customer.get('name')
            customer['user_id'] = user_id or customer.get('user_id')
        write_json(CUSTOMERS_FILE, customers)
    return customer


def resolve_buyer(data):
    """Buyer for a checkout request. Raises ValueError with a shopper-facing message on bad input."""
    data = data or {}
    address = data.get('address') or {}
    email = normalize_email(data.get('email'))
    if not email:
        raise ValueError('Enter a valid email address.')
    if current_user.is_authenticated:
        buyer = Buyer(current_user.id, email, current_user.first_name, current_user.last_name,
                      current_user.phone, current_user.wallet_balance)
    else:
        name = ' '.join(str(data.get('name') or '').split())
        if not name or len(name) > 100:
            raise ValueError('Enter your name.')
        first, _, last = name.partition(' ')
        buyer = Buyer(None, email, first, last)
    phone = ''.join(ch for ch in str(data.get('phone') or address.get('phone') or buyer.phone or '') if ch.isdigit())[-10:]
    if len(phone) != 10:
        raise ValueError('Enter a 10-digit mobile number for the courier.')
    buyer.phone = phone
    customer = upsert_customer(email, buyer.name, buyer.id)
    if REQUIRE_EMAIL_CONFIRMATION and not customer.get('email_verified') and not buyer.id:
        raise ValueError('Please confirm your email address before ordering.')
    session['customer_email'] = email
    return buyer


def owns_order(order):
    if current_user.is_authenticated and order.get('user_id') == current_user.id:
        return True
    return bool(order.get('customer_email')) and order.get('customer_email') == session.get('customer_email')


def order_contact(order):
    """Name / phone / email for courier and invoices: stored on the order, or from the account for old orders."""
    if order.get('customer_email'):
        first, _, last = (order.get('customer_name') or '').partition(' ')
        return Buyer(order.get('user_id'), order['customer_email'], first, last, order.get('customer_phone'))
    return load_user(order['user_id']) if order.get('user_id') else None


def clear_saved_cart(email):
    if email:
        write_json(CARTS_FILE, [c for c in read_json(CARTS_FILE) if c['id'] != email])


def link_social_identity(identity):
    """Sign-in with Google/Meta: find or create the account for this verified email and attach the guest orders
    and customer record with the same email, so order history carries over. Returns the user id."""
    email = normalize_email(identity.email)
    if not email or not identity.email_verified:
        raise ValueError('This account has no verified email address.')
    with user_lock:
        users = read_json(USERS_FILE)
        user = next((u for u in users if (u.get('social_ids') or {}).get(identity.provider) == identity.provider_user_id), None) \
            or next((u for u in users if (u.get('email') or '').lower() == email), None)
        if user is None:
            first, _, last = (identity.name or email.split('@')[0]).partition(' ')
            user = {'id': str(uuid.uuid4()), 'first_name': first, 'last_name': last, 'email': email,
                    'is_verified': True, 'addresses': [], 'wallet_balance': 0.0, 'created_at': datetime.now().isoformat()}
            users.append(user)
        user.setdefault('social_ids', {})[identity.provider] = identity.provider_user_id
        user['email'] = user.get('email') or email
        write_json(USERS_FILE, users)
    customer = upsert_customer(email, f"{user['first_name']} {user.get('last_name', '')}".strip(), user['id'])
    with user_lock:
        customers = read_json(CUSTOMERS_FILE)
        for c in customers:
            if c['id'] == email:
                c['email_verified'] = True
        write_json(CUSTOMERS_FILE, customers)
    with order_lock:
        orders = read_json(ORDERS_FILE)
        for o in orders:
            if o.get('customer_email') == email and not o.get('user_id'):
                o['user_id'] = user['id']
        write_json(ORDERS_FILE, orders)
    return user['id']


def return_window_open(order):
    """Returns are allowed for RETURN_WINDOW_DAYS after delivery (matches /returns policy)."""
    delivered_at = order.get('delivered_at')
    if not delivered_at:
        return True  # delivery time unknown (status synced without timestamp); let the admin approval step decide
    return datetime.now() <= datetime.fromisoformat(delivered_at) + timedelta(days=BUSINESS['return_days'])

# --- REFUND LOGIC ---

def refund_prepaid_order(order_id, cancellation_reason=None):
    """
    Refunds a prepaid order via Razorpay and cancels shipment.
    Returns: (success: bool, message: str)
    """
    with order_lock:
        orders = read_json(ORDERS_FILE)
        order_idx = next((i for i, o in enumerate(orders) if o['id'] == order_id), -1)
        
        if order_idx == -1:
            return False, "Order not found"
            
        order = orders[order_idx]
        
        # Only process refund if status is not already refunded
        if order.get('status') == 'refunded':
            return False, "Order already refunded"

        if order.get('payment_method') != 'online':
             return False, "Not a prepaid order"
             
        if order.get('refund_status') == 'processed':
             return False, "Refund already processed"

        # NEW LOGIC: Mixed Payment (Wallet + Online) -> FULL Refund to Wallet
        wallet_usage = order.get('wallet_amount', 0)
        online_amount = order.get('amount', 0)
        
        if wallet_usage > 0:
             # Mixed Payment detected
             total_refund = online_amount + wallet_usage
             
             # Credit Full Amount to Wallet
             succ_w, msg_w = add_wallet_balance(
                 order['user_id'], 
                 total_refund, 
                 f"Refund (Full): {cancellation_reason or 'Order Cancelled'}", 
                 order_id
             )
             
             if succ_w:
                 order['status'] = 'refunded'
                 order['refund_status'] = 'processed'
                 order['refund_amount'] = total_refund
                 order['wallet_refunded'] = total_refund # Track that everything went to wallet
                 order['refund_processed_at'] = datetime.now().isoformat()
                 if cancellation_reason:
                     order['cancellation_reason'] = cancellation_reason
                 
                 # Cancel Shiprocket if needed
                 sr_id = order.get('shiprocket_order_id')
                 if sr_id:
                     try:
                         shiprocket_service.cancel_order([sr_id])
                     except Exception as e:
                         print(f"SR Cancel Error: {e}")
                 
                 write_json(ORDERS_FILE, orders)
                 create_notification('action_required', f"Order #{order.get('razorpay_order_id', order_id[:8])} cancelled. Refund credited to Wallet.", order_id)
                 
                 return True, "Order cancelled. Full amount refunded to Wallet."
             else:
                 return False, f"Wallet Refund Failed: {msg_w}"

        # 3. Pure Online Refund (Razorpay)
        payment_id = order.get('razorpay_payment_id')
        amount_to_refund = order.get('amount') 
        
        if not payment_id:
             # Should not happen for pure online unless error
             return False, "Payment ID missing"

        try:
            # Amount in paisa
            refund_amount_paisa = int(round(amount_to_refund * 100))
            
            refund_data = {
                'amount': refund_amount_paisa
            }
            
            print(f"DEBUG: Razorpay Refund Payload: {refund_data}")
            
            try:
                refund = razorpay_client.payment.refund(payment_id, refund_data)
            except Exception as e:
                # If invalid request sent (likely UPI needing reverse_all), retry
                if "invalid request" in str(e).lower():
                     print("Razorpay Invalid Request. Retrying with reverse_all=1 (Likely UPI)...")
                     refund_data['reverse_all'] = 1
                     refund = razorpay_client.payment.refund(payment_id, refund_data)
                else:
                     raise e
            print(f"DEBUG: Razorpay Refund Response: {refund}")
            
            order['status'] = 'refunded'
            order['refund_status'] = 'processed'
            order['refund_amount'] = amount_to_refund
                
            order['razorpay_refund_id'] = refund.get('id')
            order['refund_processed_at'] = datetime.now().isoformat()
            if cancellation_reason:
                order['cancellation_reason'] = cancellation_reason
            
            # Cancel Shiprocket
            sr_id = order.get('shiprocket_order_id')
            if sr_id:
                try:
                    shiprocket_service.cancel_order([sr_id])
                except Exception as e:
                     print(f"SR Cancel Error: {e}")

            write_json(ORDERS_FILE, orders)
            
            # Create admin notification
            create_notification('action_required', f"Prepaid Order #{order.get('razorpay_order_id', order_id[:8])} was cancelled and refunded via Razorpay.", order_id)
            
            return True, "Refund processed successfully via Razorpay"
            
        except Exception as e:
            print(f"Razorpay Refund Error: {e}")
            import traceback
            traceback.print_exc()
            
            order['refund_status'] = 'failed'
            write_json(ORDERS_FILE, orders)
            return False, f"Razorpay Error: {str(e)}"

def refund_cod_order(order_id):
    """
    Refunds a COD order to Wallet and cancels shipment.
    Returns: (success: bool, message: str)
    """
    with order_lock:
        orders = read_json(ORDERS_FILE)
        order_idx = next((i for i, o in enumerate(orders) if o['id'] == order_id), -1)
        
        if order_idx == -1:
            return False, "Order not found"
            
        order = orders[order_idx]
        
        if order.get('status') == 'refunded':
            return False, "Order already refunded"
        
        if order.get('payment_method') != 'cod':
             return False, "Not a COD order"
             
        if order.get('cod_refund_status') == 'credited':
             return False, "Refund already credited"

        if not order.get('user_id'):
            # Guest checkout: no NTS wallet. Cash was only collected if the parcel was delivered.
            if order.get('status') in ['delivered', 'return_initiated', 'returned'] or order.get('delivered_at'):
                order['cod_refund_status'] = 'manual_required'
                order['status'] = 'refunded'
                write_json(ORDERS_FILE, orders)
                return True, (f"Guest COD order: refund ₹{order.get('final_payable', 0)} to {order.get('customer_email')} "
                              "manually (UPI / bank transfer).")
            order['status'] = 'cancelled'
            write_json(ORDERS_FILE, orders)
            return True, "Order cancelled. Nothing was paid, so nothing to refund."

        # Logic:
        # 1. Always refund Wallet usage (if any).
        # 2. Refund Cash component (final_payable) ONLY if order was delivered/returned (meaning cash was paid).
        #    If status is 'cancelled' (pre-delivery) or 'cod_pending', cash wasn't paid.
        
        wallet_usage = order.get('wallet_amount', 0)
        cash_paid = order.get('final_payable', 0)
        
        # Check current status to decide on Cash Refund
        # delivered, return_initiated, returned -> Cash was paid
        # cod_pending, processing, shipped (if cancelled before delivery), cancelled -> Cash NOT paid
        # However, caller might have changed status to 'cancelled' already?
        # Usually checking 'delivered_at' presence is safer? 
        # Or simplistic: If this function is called, we assume we want to refund what is due.
        # But for 'cancellation' (pre-delivery), we must NOT refund cash.
        
        # We'll allow an optional arg or check status. 
        # But safely: Refund Cash Part only if 'delivered_at' is present or status indicates post-delivery.
        
        should_refund_cash = False
        if order.get('status') in ['delivered', 'return_initiated', 'returned'] or order.get('delivered_at'):
             should_refund_cash = True
             
        # refund total
        total_refund_to_wallet = 0
        refund_details = []
        
        if wallet_usage > 0:
             total_refund_to_wallet += wallet_usage
             refund_details.append(f"Wallet Part: {wallet_usage}")
             
        if should_refund_cash and cash_paid > 0:
             total_refund_to_wallet += cash_paid
             refund_details.append(f"Cash Part: {cash_paid}")
             
        if total_refund_to_wallet > 0:
             succ_w, msg_w = add_wallet_balance(
                 order.get('user_id'), 
                 total_refund_to_wallet, 
                 f"Refund ({', '.join(refund_details)}): Order {order.get('razorpay_order_id', order_id)}", 
                 order_id
             )
             
             if succ_w:
                 order['status'] = 'refunded'
                 order['cod_refund_status'] = 'credited'
                 order['wallet_credit_amount'] = total_refund_to_wallet
                 order['refund_amount'] = total_refund_to_wallet
                 order['credited_at'] = datetime.now().isoformat()
                 
                 # Cancel Shiprocket (if not delivered/returned, i.e., strict cancellation)
                 # If returned, we don't 'cancel' the forward order in SR usually, we create Return.
                 # But if pre-delivery cancellation, we cancel.
                 if not should_refund_cash:
                     sr_id = order.get('shiprocket_order_id')
                     if sr_id:
                         try:
                             shiprocket_service.cancel_order([sr_id])
                         except Exception as e:
                             print(f"SR Cancel Error: {e}")
                 
                 write_json(ORDERS_FILE, orders)
                 return True, "Refund credited to wallet"
             else:
                 return False, f"Wallet Error: {msg_w}"
        else:
             # Nothing to refund (Pure COD Cancelled Pre-Delivery)
             order['status'] = 'cancelled'
             write_json(ORDERS_FILE, orders)
             # Cancel SR
             sr_id = order.get('shiprocket_order_id')
             if sr_id:
                 shiprocket_service.cancel_order([sr_id])
             return True, "Order cancelled (No refund needed)"

def refund_wallet_order(order_id, cancellation_reason=None):
    """
    Refunds a wallet-paid order back to Wallet and cancels shipment.
    Returns: (success: bool, message: str)
    """
    with order_lock:
        orders = read_json(ORDERS_FILE)
        order_idx = next((i for i, o in enumerate(orders) if o['id'] == order_id), -1)
        
        if order_idx == -1:
            return False, "Order not found"
            
        order = orders[order_idx]
        
        if order.get('status') == 'refunded':
            return False, "Order already refunded"
        
        if order.get('payment_method') != 'wallet':
             return False, "Not a wallet order"
             
        if order.get('wallet_refund_status') == 'credited':
             return False, "Refund already credited"

        # 1. Cancel Shiprocket Order
        sr_id = order.get('shiprocket_order_id')
        if sr_id:
            try:
                shiprocket_service.cancel_order([sr_id])
            except Exception as e:
                print(f"SR Cancel Error during wallet refund: {e}")
        
        # 2. Credit Wallet (using the wallet_amount or actual_value)
        amount_to_refund = order.get('wallet_amount') or order.get('actual_value', 0)
        user_id = order.get('user_id')
        
        success, msg = add_wallet_balance(user_id, amount_to_refund, "Wallet Order Refund", order_id)
        
        if success:
            order['status'] = 'refunded'
            order['wallet_refund_status'] = 'credited'
            order['wallet_credit_amount'] = amount_to_refund
            order['credited_at'] = datetime.now().isoformat()
            if cancellation_reason:
                order['cancellation_reason'] = cancellation_reason
            
            write_json(ORDERS_FILE, orders)
            
            # Create admin notification
            create_notification('action_required', f"Wallet Order #{order.get('razorpay_order_id', order_id[:8])} was cancelled and refunded.", order_id)
            
            return True, "Wallet credited successfully"
        else:
            return False, f"Wallet Error: {msg}"


# --- NOTIFICATION HELPERS ---

def create_notification(type, message, order_id=None):
    """
    Creates a new notification for admins.
    type: 'info', 'warning', 'action_required'
    """
    try:
        notifications = read_json(NOTIFICATIONS_FILE)
        if not isinstance(notifications, list):
             notifications = []
        
        notif = {
            'id': str(uuid.uuid4()),
            'type': type,
            'message': message,
            'order_id': order_id,
            'is_read': False,
            'created_at': datetime.now().isoformat()
        }
        
        notifications.insert(0, notif) # Recent first
        write_json(NOTIFICATIONS_FILE, notifications)
        return True
    except Exception as e:
        print(f"Error creating notification: {e}")
        return False

def get_notifications(unread_only=False):
    notifications = read_json(NOTIFICATIONS_FILE)
    if not isinstance(notifications, list): return []
    
    if unread_only:
        return [n for n in notifications if not n.get('is_read')]
    return notifications

# --- WALLET ROUTES ---

@app.route('/my-wallet')
@login_required
def my_wallet():
    balance = current_user.wallet_balance
    transactions = get_wallet_transactions(current_user.id)
    return render_template('wallet.html', balance=balance, transactions=transactions)

# --- INVENTORY (SPEC-fulfilment.md) ---
# product['stock'] = units on hand per size; a size with no entry is unlimited.
# Orders placed but not yet handed to the courier reserve units; on-hand drops once, at pickup.
RESERVING_STATUSES = {'paid', 'cod_pending'}


def reserved_units(orders):
    reserved = {}
    for o in orders:
        if o.get('status') in RESERVING_STATUSES and not o.get('stock_deducted'):
            for i in o.get('items', []):
                key = (str(i.get('id')), i.get('selectedSize'))
                reserved[key] = reserved.get(key, 0) + int(i.get('quantity', 1))
    return reserved


def available_units(product, size, reserved):
    """Units a shopper can still buy, or None when stock is not tracked for this size."""
    stock = product.get('stock') or {}
    if size not in stock:
        return None
    return max(0, int(stock[size]) - reserved.get((str(product['id']), size), 0))


def size_availability(product, reserved):
    return {size: available_units(product, size, reserved) for size in product.get('availableSizes') or []}


def calculate_order_total(items, coupon_code, user_id, shipping_cost=None, use_wallet=False):
    """
    Calculate order total server-side using products.json.
    Returns dict with breakdown or raises Exception on error.
    """
    # 1. Load Products
    products_path = os.path.join(app.root_path, 'assets/products.json')
    all_products = read_json(products_path)
    if not all_products:
        raise Exception("Products data missing")
        
    product_map = {str(p['id']): p for p in all_products}
    # shortcut: two shoppers paying online for the last unit at the same moment can both succeed (online orders reserve
    # only once paid); acceptable at 4 SKUs, add a hold at create_order if that starts happening.
    reserved = reserved_units(read_json(ORDERS_FILE))
    wanted = {}

    total_amount = 0
    validated_items = []
    
    for item in items:
        pid = str(item.get('id'))
        qty = int(item.get('quantity', 1))
        
        if pid not in product_map:
            raise ValueError("An item in your cart is no longer available. Please remove it and try again.")
            
        product = product_map[pid]
        price = float(product['price'])

        if qty < 1 or qty > 10:
            raise ValueError(f"Invalid quantity for {product['name']}")
        if product.get('inStock') is False:
            raise ValueError(f"{product['name']} is sold out")
        sizes = product.get('availableSizes') or []
        size = item.get('selectedSize')
        if sizes and (size not in sizes or size in (product.get('soldOutSizes') or [])):
            raise ValueError(f"Size {size} is not available for {product['name']}")
        key = (pid, size)
        wanted[key] = wanted.get(key, 0) + qty
        left = available_units(product, size, reserved)
        if left is not None and wanted[key] > left:
            raise ValueError(f"Only {left} left in size {size} for {product['name']}" if left else f"Size {size} of {product['name']} is sold out")

        item_total = price * qty
        total_amount += item_total
        
        validated_items.append({
            'id': pid,
            'name': product['name'],
            'price': price,
            'quantity': qty,
            'image': product.get('image'),
            'total': item_total,
            'selectedSize': item.get('selectedSize'),
            'selectedColor': item.get('selectedColor')
        })
        
    # 2. Apply Coupon
    discount_amount = 0
    coupon_data = None
    
    if coupon_code:
        coupons = read_json(COUPONS_FILE)
        coupon = next((c for c in coupons if c['code'] == coupon_code.upper()), None)
        
        if coupon:
            # Validate coupon (reuse logic or simplify)
            is_valid = True
            error_msg = None
            
            # Min Amount
            if total_amount < coupon.get('min_order_amount', 0):
                is_valid = False
            
            # Limits
            if coupon.get('limit_type') == 'global' and coupon.get('times_used', 0) >= coupon.get('max_uses', 0):
                is_valid = False
            
            if coupon.get('limit_type') == 'per_user':
                if user_id and user_id in coupon.get('used_by_users', []):
                    is_valid = False
                elif not user_id:
                    is_valid = False

            if is_valid:
                if coupon['discount_type'] == 'percentage':
                    discount_amount = (total_amount * coupon['discount_value']) / 100
                else:
                    discount_amount = coupon['discount_value']
                
                # Cap discount
                if discount_amount > total_amount:
                    discount_amount = total_amount
                    
                coupon_data = coupon
    
    # 3. Shipping
    if shipping_cost is None:
        shipping_cost = 0
        if total_amount < 1000 and total_amount > 0:
            shipping_cost = 99 # Standard Shipping Rule
    elif total_amount >= 1000:
        shipping_cost = 0  # free-shipping rule, same as checkout UI
    else:
        # shortcut: below ₹1000 the client-quoted Shiprocket rate is trusted (floored at 0); re-quote server-side if prices drop under ₹1000
        try:
            shipping_cost = max(0.0, float(shipping_cost))
        except (TypeError, ValueError):
            shipping_cost = 99
        
    final_amount = total_amount - discount_amount + shipping_cost
    
    # 4. Wallet Deduction (if requested)
    wallet_deducted = 0
    if use_wallet and user_id:
        users = read_json(USERS_FILE)
        user = next((u for u in users if u['id'] == user_id), None)
        if user:
            balance = float(user.get('wallet_balance', 0))
            if balance > 0:
                wallet_deducted = min(balance, final_amount)
                final_amount = max(0, final_amount - wallet_deducted)
    
    return {
        'items': validated_items,
        'subtotal': total_amount,
        'discount': discount_amount,
        'shipping': shipping_cost,
        'wallet_deducted': wallet_deducted,
        'final_amount': final_amount,
        'coupon_applied': coupon_code if coupon_data else None,
        'coupon_data': coupon_data
    }

# --- ROUTES ---

@app.route('/assets/<path:filename>')
def serve_assets(filename):
    if filename in ['products.json', 'content.json']:
        filepath = os.path.join(app.root_path, f'assets/{filename}')
        return jsonify(read_json(filepath))
    assets_folder = os.path.join(app.root_path, 'assets')
    return send_from_directory(assets_folder, filename)

PRODUCTS_FILE = os.path.join(app.root_path, 'assets/products.json')

@app.route('/')
def index():
    products = read_json(PRODUCTS_FILE)
    reserved = reserved_units(read_json(ORDERS_FILE))
    for p in products:
        avail = size_availability(p, reserved)
        p['soldOutEverywhere'] = bool(avail) and all(v == 0 for v in avail.values())
    return render_template('index.html', user=current_user, products=products)

@app.route('/collection')
@app.route('/collection/<category_id>')
def collection(category_id=None):
    return redirect(url_for('index'))  # NTS has a single shop grid

@app.route('/product/<product_id>')
def product(product_id):
    item = next((p for p in read_json(PRODUCTS_FILE) if str(p.get('id')) == product_id), None)
    if item is None:
        return redirect(url_for('index'))
    availability = size_availability(item, reserved_units(read_json(ORDERS_FILE)))
    return render_template('product.html', product=item, availability=availability, user=current_user)

@app.route('/checkout')
def checkout():
    # Saved addresses only exist for signed-in customers; guests type theirs in
    addresses = current_user.addresses if current_user.is_authenticated else []
    default_address_id = current_user.default_address_id if current_user.is_authenticated else None
    return render_template('checkout.html', user=current_user, razorpay_key=RAZORPAY_KEY, 
                         addresses=addresses, default_address_id=default_address_id,
                         hide_layout=True)

@app.route('/order-success')
def order_success():
    orders = read_json(ORDERS_FILE)
    user_orders = [o for o in orders if owns_order(o)]
    latest_order = user_orders[-1] if user_orders else None
    return render_template('order_success.html', user=current_user, order=latest_order)

@app.route('/order-confirmation/<order_id>')
def order_confirmation(order_id):
    orders = read_json(ORDERS_FILE)
    order = next((o for o in orders if o['id'] == order_id and owns_order(o)), None)
    if not order:
        return redirect(url_for('track_order_page'))
    return render_template('order_success.html', user=current_user, order=order)


@app.route('/addresses')
@login_required
def my_addresses():
    return render_template('addresses.html', addresses=current_user.addresses, default_address_id=current_user.default_address_id)

@app.route('/orders')
@login_required
def my_orders():
    all_orders = read_json(ORDERS_FILE)
    user_orders = [o for o in all_orders if o.get('user_id') == current_user.id]
    user_orders.sort(key=lambda x: x.get('created_at', ''), reverse=True)
    return render_template('orders.html', orders=user_orders)

@app.route('/contact')
def contact():
    return render_template('contact.html')

@app.route('/about')
def about():
    return redirect(url_for('index'))  # SIXN-era page; NTS has no brand-story page yet

@app.route('/craftsmanship')
def craftsmanship():
    return redirect(url_for('index'))  # SIXN-era page; NTS has no brand-story page yet

@app.route('/privacy')
def privacy():
    return render_template('privacy.html')

@app.route('/terms')
def terms():
    return render_template('terms.html')

@app.route('/faq')
def faq():
    return render_template('faq.html')

@app.route('/returns')
def returns():
    return render_template('returns.html')

@app.route('/size-guide')
def size_guide():
    # Measurements live in content.json -> sizeChart (editable from /admin), e.g. {"S": {"chest": 104, "length": 70}}
    content = read_json(os.path.join(app.root_path, 'assets/content.json')) or {}
    return render_template('size-guide.html', size_chart=content.get('sizeChart', {}))

@app.route('/shipping')
def shipping():
    return render_template('shipping.html')

# --- ADMIN PANEL ---
def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated:
            return redirect('/?login=1')  # opens the sign-in modal (admin phone login stays available)
        if not current_user.is_admin:
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated_function

@app.route('/admin')
@login_required
@admin_required
def admin_panel():
    # Load All Data
    users = read_json(USERS_FILE)
    orders = read_json(ORDERS_FILE)
    ratings = read_json(RATINGS_FILE)
    reports = read_json(REPORTS_FILE)
    coupons = read_json(COUPONS_FILE)
    
    # Map reports
    reported_count = 0
    for r in ratings:
        r['reports'] = [rep for rep in reports if rep.get('rating_id') == r.get('id')]
        if r['reports']: reported_count += 1
    
    # Load Assets Data
    products_path = os.path.join(app.root_path, 'assets/products.json')
    content_path = os.path.join(app.root_path, 'assets/content.json')
    
    products = read_json(products_path)
    content = read_json(content_path)

    # Calculate Stats
    total_sales = sum(o.get('amount', 0) for o in orders)
    
    stats = {
        'users': len(users),
        'orders': len(orders),
        'sales': total_sales,
        'products': len(products),
        'ratings': len(ratings),
        'reports': reported_count
    }

    return render_template('admin.html', 
                         users=users, 
                         orders=orders, 
                         ratings=ratings,
                         reports=reports,
                         coupons=coupons,
                         products=products, 
                         content=content,
                         stats=stats,
                         products_json=json.dumps(products, indent=4),
                         content_json=json.dumps(content, indent=4))

# --- FULFILMENT DESK (SPEC-fulfilment.md) ---
FULFILMENT_STAGES = [('to_ship', 'To ship'), ('in_transit', 'In transit'), ('delivered', 'Delivered'),
                     ('returns', 'Returns'), ('closed', 'Closed')]


def fulfilment_stage(order):
    status = order.get('status')
    if status in RESERVING_STATUSES:
        return 'in_transit' if order.get('stock_deducted') else 'to_ship'
    if status == 'shipped':
        return 'in_transit'
    if status == 'delivered':
        return 'delivered'
    if status in ('return_requested', 'return_approved', 'return_initiated', 'returned', 'rto_received'):
        return 'returns'
    return 'closed'


def _adjust_stock(items, sign):
    """Add (sign=+1) or remove (sign=-1) order units from on-hand stock for sizes that are tracked."""
    products = read_json(PRODUCTS_FILE)
    by_id = {str(p['id']): p for p in products}
    for item in items:
        product = by_id.get(str(item.get('id')))
        stock = (product or {}).get('stock') or {}
        size = item.get('selectedSize')
        if size in stock:
            stock[size] = max(0, int(stock[size]) + sign * int(item.get('quantity', 1)))
    write_json(PRODUCTS_FILE, products)


def _assign_awb(order):
    """Pick the recommended courier for this PIN and assign an AWB to an existing Shiprocket shipment."""
    clean_zip = ''.join(filter(str.isdigit, str((order.get('address') or {}).get('zip', ''))))
    is_cod = 1 if order.get('payment_method') == 'cod' else 0
    pickup_pin = os.environ.get('SHIPROCKET_PICKUP_PINCODE', '')
    best = shiprocket_service.select_best_courier(shiprocket_service.check_serviceability(pickup_pin, clean_zip, 0.5, is_cod))
    if not best:
        return "No courier serves this PIN code right now."
    res = shiprocket_service.generate_awb(order['shiprocket_shipment_id'], best['courier_company_id'])
    if res and res.get('awb_assign_status') == 1:
        order['awb_code'] = res['response']['data']['awb_code']
        return None
    return f"Shiprocket did not assign an AWB: {res}"


@app.route('/admin/fulfilment')
@login_required
@admin_required
def admin_fulfilment():
    orders = sorted(read_json(ORDERS_FILE), key=lambda o: o.get('created_at', ''), reverse=True)
    products = read_json(PRODUCTS_FILE)
    reserved = reserved_units(orders)
    stages = {key: [] for key, _ in FULFILMENT_STAGES}
    for o in orders:
        stages[fulfilment_stage(o)].append(o)
    stock_rows = [{'product': p, 'sizes': [{'size': size, 'on_hand': (p.get('stock') or {}).get(size),
                                            'reserved': reserved.get((str(p['id']), size), 0),
                                            'available': available_units(p, size, reserved)}
                                           for size in p.get('availableSizes') or []]}
                  for p in products]
    return render_template('fulfilment.html', stages=stages, stage_names=FULFILMENT_STAGES,
                           active=request.args.get('stage', 'to_ship'), stock_rows=stock_rows)


@app.route('/api/admin/stock', methods=['POST'])
@login_required
@admin_required
def admin_set_stock():
    data = request.json or {}
    try:
        on_hand = int(data.get('on_hand'))
    except (TypeError, ValueError):
        return jsonify({'error': 'Stock must be a whole number'}), 400
    if on_hand < 0:
        return jsonify({'error': 'Stock cannot be negative'}), 400
    with order_lock:
        products = read_json(PRODUCTS_FILE)
        product = next((p for p in products if str(p['id']) == str(data.get('product_id'))), None)
        if not product or data.get('size') not in (product.get('availableSizes') or []):
            return jsonify({'error': 'Unknown product or size'}), 404
        product.setdefault('stock', {})[data['size']] = on_hand
        write_json(PRODUCTS_FILE, products)
    return jsonify({'status': 'success'})


@app.route('/api/admin/fulfilment/<order_id>/<action>', methods=['POST'])
@login_required
@admin_required
def admin_fulfilment_action(order_id, action):
    with order_lock:
        order = next((o for o in read_json(ORDERS_FILE) if o['id'] == order_id), None)
        if not order:
            return jsonify({'error': 'Order not found'}), 404

        if action == 'shipment':
            if not order.get('shiprocket_shipment_id'):
                if order.get('payment_method') == 'cod':
                    return jsonify({'error': 'COD shipments are created at checkout; create this one in the Shiprocket dashboard.'}), 400
                user = order_contact(order)
                if not user:
                    return jsonify({'error': 'Customer details not found on this order'}), 400
                create_prepaid_shipment(order, user)
                if not order.get('shiprocket_shipment_id'):
                    return jsonify({'error': 'Shiprocket rejected the order; check the server log.'}), 502
            if not order.get('awb_code'):
                error = _assign_awb(order)
                if error:
                    return jsonify({'error': error}), 502
                _update_order(order)
            return jsonify({'status': 'success', 'awb': order['awb_code']})

        if action == 'label':
            if not order.get('shiprocket_shipment_id') or not order.get('awb_code'):
                return jsonify({'error': 'Create the shipment (AWB) first.'}), 400
            res = shiprocket_service.generate_label([order['shiprocket_shipment_id']])
            if not res or not res.get('label_url'):
                return jsonify({'error': f'Label not generated: {res}'}), 502
            order['label_url'] = res['label_url']
            _update_order(order)
            return jsonify({'status': 'success', 'label_url': res['label_url']})

        if action == 'pickup':
            if fulfilment_stage(order) != 'to_ship':
                return jsonify({'error': 'Only orders waiting to ship can be picked up.'}), 400
            if not order.get('shiprocket_shipment_id') or not order.get('awb_code'):
                return jsonify({'error': 'Create the shipment (AWB) first.'}), 400
            res = shiprocket_service.generate_pickup([order['shiprocket_shipment_id']]) or {}
            already = 'already' in json.dumps(res).lower()
            if res.get('pickup_status') != 1 and not already:
                return jsonify({'error': f'Pickup not scheduled: {res}'}), 502
            _adjust_stock(order.get('items', []), -1)
            order.update(stock_deducted=True, status='shipped', shipped_at=datetime.now().isoformat())
            _update_order(order)
            return jsonify({'status': 'success'})

        if action == 'restock':
            if fulfilment_stage(order) not in ('returns', 'closed') or not order.get('stock_deducted') or order.get('restocked'):
                return jsonify({'error': 'Nothing to restock for this order.'}), 400
            _adjust_stock(order.get('items', []), +1)
            order['restocked'] = True
            _update_order(order)
            return jsonify({'status': 'success'})

    return jsonify({'error': 'Unknown action'}), 400


@app.route('/api/admin/fulfilment/sync', methods=['POST'])
@login_required
@admin_required
def admin_fulfilment_sync():
    """Pull courier status for every in-transit AWB and mark delivered ones."""
    delivered = 0
    with order_lock:
        orders = read_json(ORDERS_FILE)
        for order in orders:
            if fulfilment_stage(order) != 'in_transit' or not order.get('awb_code'):
                continue
            track = (shiprocket_service.track_awb(order['awb_code']) or {}).get('tracking_data') or {}
            events = track.get('shipment_track') or [{}]
            current = (events[0].get('current_status') or '').upper()
            if current:
                order['tracking_status'] = current
            if current.startswith('RTO'):
                # Courier is returning the parcel to us after failed delivery attempts
                order['delivery_issue'] = current
                if current == 'RTO DELIVERED':
                    order['status'] = 'rto_received'
                    order.setdefault('rto_received_at', datetime.now().isoformat())
            elif current == 'DELIVERED' or track.get('track_status') == 7:
                order['status'] = 'delivered'
                order.pop('delivery_issue', None)
                order.setdefault('delivered_at', datetime.now().isoformat())
                delivered += 1
            elif 'UNDELIVERED' in current or 'NDR' in current or 'FAILED' in current:
                order['delivery_issue'] = current  # shown on the desk; resolve with the customer / in Shiprocket NDR
        write_json(ORDERS_FILE, orders)
    return jsonify({'status': 'success', 'delivered': delivered})


@app.route('/api/admin/save_data', methods=['POST'])
@login_required
@admin_required
def admin_save_data():
    data = request.json
    filename = data.get('filename') # 'products.json' or 'content.json'
    content = data.get('content')
    
    # Length Check for Content (prevent massive file writes)
    if len(content) > 5 * 1024 * 1024: # 5MB limit
         return jsonify({'error': 'Content too large (max 5MB)'}), 400
    
    if filename not in ['products.json', 'content.json']:
        return jsonify({'error': 'Invalid file'}), 400
        
    try:
        # Validate JSON
        parsed = json.loads(content)
        
        file_path = os.path.join(app.root_path, f'assets/{filename}')
        write_json(file_path, parsed)
            
        return jsonify({'status': 'success'})
    except json.JSONDecodeError:
        return jsonify({'error': 'Invalid JSON format'}), 400
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/admin/upload_images', methods=['POST'])
@login_required
@admin_required
def upload_images():
    if 'images' not in request.files:
        return jsonify({'error': 'No images provided'}), 400
    
    files = request.files.getlist('images')
    uploaded_urls = []
    
    # Ensure upload folder exists
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
    
    for file in files:
        if file and file.filename and allowed_file(file.filename):
            # Generate unique filename
            ext = file.filename.rsplit('.', 1)[1].lower()
            unique_filename = f"{uuid.uuid4().hex}.{ext}"
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], unique_filename)
            
            file.save(filepath)
            
            # Return relative URL for the frontend
            url = f"/static/uploads/products/{unique_filename}"
            uploaded_urls.append(url)
    
    if not uploaded_urls:
        return jsonify({'error': 'No valid images uploaded'}), 400
    
    return jsonify({'urls': uploaded_urls, 'status': 'success'})

@app.route('/api/admin/ratings/delete/<rating_id>', methods=['POST'])
@login_required
@admin_required
def delete_rating(rating_id):
    ratings = read_json(RATINGS_FILE)
    ratings = [r for r in ratings if r['id'] != rating_id]
    write_json(RATINGS_FILE, ratings)
    return jsonify({'status': 'success'})

@app.route('/api/admin/ratings/edit', methods=['POST'])
@login_required
@admin_required
def edit_rating():
    data = request.json
    rating_id = data.get('id')
    ratings = read_json(RATINGS_FILE)
    
    for r in ratings:
        if r['id'] == rating_id:
            r['rating'] = data.get('rating', r['rating'])
            r['comment'] = data.get('comment', r['comment'])
            break
            
    write_json(RATINGS_FILE, ratings)
    return jsonify({'status': 'success'})

@app.route('/api/payment/create_order', methods=['POST'])
@limiter.limit("30 per hour")
def create_order():
    data = request.json
    items = data.get('items')
    coupon_code = data.get('coupon_code') or data.get('coupon')
    address = data.get('address')
    currency = 'INR'  # prices are stored in INR; never let the client pick the charge currency
    shipping_cost = data.get('shipping_cost')
    use_wallet = data.get('use_wallet', False)
    
    try:
        buyer = resolve_buyer(data)
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    user_email = buyer.email

    if not items:
        return jsonify({'error': 'Cart is empty'}), 400

    try:
        # Server-side calculation WITH wallet support
        calculation = calculate_order_total(items, coupon_code, buyer.key, shipping_cost=shipping_cost, use_wallet=use_wallet)
        
        final_amount = calculation['final_amount']
        wallet_deducted = calculation.get('wallet_deducted', 0)
        
        # IF FULLY PAID BY WALLET (amount = 0), create order directly
        if use_wallet and final_amount <= 0:
            # Debit wallet
            if wallet_deducted > 0:
                add_wallet_balance(buyer.id, -wallet_deducted, "Order Payment (Full Wallet)", None)
            
            # Create Order directly with 'paid' status
            order_id = str(uuid.uuid4())
            order_number = f"WALLET-{uuid.uuid4().hex[:8].upper()}"
            
            # Calculate actual order value for Shiprocket (subtotal before wallet deduction)
            actual_order_value = calculation['subtotal'] - calculation.get('discount', 0) + calculation.get('shipping', 0)
            
            order = {
                'id': order_id,
                'razorpay_order_id': order_number,  # For template display
                'user_id': buyer.id, **buyer.order_fields(),
                'items': calculation['items'],
                'amount': 0,
                'wallet_amount': wallet_deducted,
                'actual_value': actual_order_value,  # Actual order value before wallet
                'currency': currency,
                'status': 'paid',
                'payment_method': 'wallet',
                'coupon_applied': calculation.get('coupon_applied'),
                'discount_amount': calculation.get('discount', 0),
                'shipping_amount': calculation.get('shipping', 0),
                'created_at': datetime.now().isoformat(),
                'address': address
            }
            
            with order_lock:
                orders = read_json(ORDERS_FILE)
                orders.append(order)
                write_json(ORDERS_FILE, orders)
            
            # Try to create Shiprocket order
            # Try to create Shiprocket order
            try:
                # Sanitize Data
                raw_zip = str(address.get('zip', ''))
                clean_zip = ''.join(filter(str.isdigit, raw_zip))
                
                raw_phone = buyer.phone or ""
                clean_phone = ''.join(filter(str.isdigit, raw_phone))
                if len(clean_phone) > 10: clean_phone = clean_phone[-10:]

                sr_payload = {
                    "order_id": order_id[:20],
                    "order_date": datetime.now().strftime("%Y-%m-%d %H:%M"),
                    "pickup_location": os.getenv('SHIPROCKET_PICKUP_LOCATION', 'Primary'),
                    "billing_customer_name": address.get('name', buyer.first_name),
                    "billing_last_name": buyer.last_name or "",
                    "billing_address": address.get('street', ''),
                    "billing_city": address.get('city', ''),
                    "billing_pincode": int(clean_zip) if clean_zip else 0,
                    "billing_state": address.get('state', ''),
                    "billing_country": address.get('country', 'India'),
                    "billing_email": user_email or buyer.email or "customer@example.com",
                    "billing_phone": clean_phone,
                    "shipping_is_billing": True,
                    "order_items": [{"name": i['name'][:50], "sku": str(i['id'])[:20], "units": int(i.get('quantity', 1)), "selling_price": float(i['price'])} for i in items],
                    "payment_method": "Prepaid",
                    "sub_total": actual_order_value,
                    "length": 10,
                    "breadth": 10,
                    "height": 10,
                    "weight": 0.5
                }
                
                print(f"DEBUG: Wallet Order SR Payload: {json.dumps(sr_payload, indent=2)}")
                
                sr_response = shiprocket_service.create_order(sr_payload)
                print(f"DEBUG: Wallet Order SR Response: {sr_response}")
                
                if sr_response and sr_response.get('order_id'):
                    with order_lock:
                        orders = read_json(ORDERS_FILE)
                        for o in orders:
                            if o['id'] == order_id:
                                o['shiprocket_order_id'] = sr_response['order_id']
                                o['shiprocket_shipment_id'] = sr_response.get('shipment_id')
                                break
                        write_json(ORDERS_FILE, orders)
                else:
                     print(f"SR Creation Failed (Wallet): {sr_response}")
            except Exception as e:
                print(f"Shiprocket order creation failed for wallet order: {e}")
            
            return jsonify({
                'status': 'success',
                'order_status': 'paid',
                'order_id': order_id,
                'internal_order_id': order_id,
                'message': 'Order placed successfully'
            })
        
        # Normal Razorpay flow
        if not razorpay_client:
            return jsonify({'error': 'Online payments are not yet enabled. Please choose Cash on Delivery.'}), 503
        
        final_amount_paise = int(final_amount * 100)
        
        order_data = {
            'amount': final_amount_paise,
            'currency': currency,
            'payment_capture': 1
        }
        
        rp_order = razorpay_client.order.create(data=order_data)
        
        # Save Pending Order to SESSION (not DB)
        pending_order = {
            'id': str(uuid.uuid4()),
            'user_id': buyer.id, **buyer.order_fields(),
            'razorpay_order_id': rp_order['id'],
            'items': calculation['items'],
            'amount': final_amount,
            'wallet_deduction': wallet_deducted,
            'use_wallet': use_wallet,
            'currency': currency,
            'status': 'pending_payment',
            'payment_method': 'online',
            'coupon_applied': calculation.get('coupon_applied'),
            'discount_amount': calculation.get('discount', 0),
            'shipping_amount': calculation.get('shipping', 0),
            'created_at': datetime.now().isoformat(),
            'address': address
        }
        
        session['pending_order'] = pending_order
        session.modified = True
        _save_pending_order(pending_order)
        
        return jsonify({
            'id': rp_order['id'],
            'razorpay_order_id': rp_order['id'],
            'razorpay_key_id': os.getenv('RAZORPAY_KEY'),
            'amount': final_amount_paise,
            'currency': currency,
            'internal_order_id': pending_order['id']
        })

    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        print(f"Order Creation Error: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': 'Error creating order'}), 500

@app.route('/api/payment/cod_order', methods=['POST'])
@limiter.limit("50 per day") # Increased limit for testing
def create_cod_order():
    """Handle Cash on Delivery orders"""
    data = request.json
    items = data.get('items')
    coupon_code = data.get('coupon_code')
    address = data.get('address')
    save_as_default = data.get('save_as_default', False)
    shipping_cost = data.get('shipping_cost')
    use_wallet = data.get('use_wallet', False) # New
    
    if not items or not address:
        return jsonify({'error': 'Missing required order details'}), 400
    
    try:
        buyer = resolve_buyer(data)
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    user_email = buyer.email

    # RATE LIMIT CHECK
    orders = read_json(ORDERS_FILE)
    
    # 1. Check for pending Unpaid CODs
    pending_cods = [o for o in orders if order_key(o) == buyer.key and o.get('status') == 'cod_pending']
    if pending_cods:
         return jsonify({'error': 'You have a pending COD order. Please wait for it to be processed.'}), 429
         
    # 2. Check daily limit (max 50 per day)
    today_str = datetime.now().strftime('%Y-%m-%d')
    today_cods = [
        o for o in orders 
        if order_key(o) == buyer.key 
        and o.get('payment_method') == 'cod' 
        and o.get('created_at', '').startswith(today_str)
    ]
    if len(today_cods) >= 50:
        return jsonify({'error': 'Daily COD limit reached. Pay online instead.'}), 429

    # Validate address fields
    required_fields = ['street', 'city', 'state', 'zip', 'country']
    if not all(address.get(field) for field in required_fields):
        return jsonify({'error': 'Incomplete address'}), 400
        
    # Max length validation
    for field, val in address.items():
        if field == 'data_encrypted': continue
        if isinstance(val, str) and len(val) > 200:
            return jsonify({'error': f'Address field {field} too long (max 200 chars)'}), 400
    
    try:
        # Server-side calculation with wallet support
        use_wallet = data.get('use_wallet', False)
        calculation = calculate_order_total(items, coupon_code, buyer.key, shipping_cost=shipping_cost, use_wallet=use_wallet)
        
        # STRICT PRICE CHECK (COD)
        client_amount = float(data.get('amount', 0))
        server_amount = calculation['final_amount']
        
        if abs(client_amount - server_amount) > 0.05: # Allow 5 cents/paise difference
             return jsonify({'error': 'Price mismatch. Please refresh and try again.'}), 400
        
        # Deduct Wallet Balance Immediately if used
        if calculation.get('wallet_deducted', 0) > 0:
             add_wallet_balance(buyer.id, -calculation['wallet_deducted'], "Order Payment (Partial/COD)", "PENDING_COD")
        
        # Save new address if requested
        if save_as_default:
            users = read_json(USERS_FILE)
            for user in users:
                if user['id'] == buyer.id:
                    if 'addresses' not in user: user['addresses'] = []
                    new_addr = {
                        'id': str(uuid.uuid4()),
                        'label': 'Delivery Address',
                        'is_default': False
                    }
                    
                    pii_data = {
                        'street': address.get('street'),
                        'landmark': address.get('landmark', ''),
                        'city': address.get('city'),
                        'state': address.get('state'),
                        'zip': address.get('zip'),
                        'country': address.get('country')
                    }
                    new_addr['data_encrypted'] = encrypt_data(json.dumps(pii_data), 'address')
                    
                    user['addresses'].append(new_addr)
                    user['default_address_id'] = new_addr['id']
                    break
            write_json(USERS_FILE, users)
             
    except Exception as e:
        return jsonify({'error': str(e)}), 400
        
    cod_order_id = f"COD-{uuid.uuid4().hex[:8].upper()}"
    
    # --- SHIPROCKET ---
    sr_response = None
    try:
            # FORCE EXACT COD AMOUNT STRATEGY
            # Shiprocket calculates COD Amount = Subtotal - Discount + Shipping
            # To guarantee COD Amount == final_payable, we will:
            # 1. Set Discount = 0
            # 2. Set Shipping = 0
            # 3. Adjust Item Prices so their sum equals final_payable exactly.
            
            target_cod_amount = float(calculation['final_amount'])
            
            # If full wallet (prepaid), logic below sets price to 0 which is fine/correct for Prepaid via COD route? 
            # Actually if prepaid, payment_method_sr is Prepaid.
            # But let's apply this logic regardless to ensure "Value" is correct.
            # Be careful: If Value is 0, SR might reject?
            # If Prepaid, we probably want declared value to be Real Value for insurance?
            # But user complained about "COD Amount".
            # So ONLY apply this if Payment Method is COD (i.e. target_cod_amount > 0).
            
            if target_cod_amount > 0:
                 # Distribute target_cod_amount proportionally
                 total_original_price = sum(float(i['price']) * int(i['quantity']) for i in calculation['items'])
                 
                 sr_items = []
                 running_total = 0
                 for idx, item in enumerate(calculation['items']):
                     org_price_total = float(item['price']) * int(item['quantity'])
                     
                     # Calculate ratio based share
                     if total_original_price > 0:
                         share = (org_price_total / total_original_price) * target_cod_amount
                     else:
                         share = 0
                         
                     # Round to 2 decimals
                     share = round(share, 2)
                     
                     # Adjustment for last item to fix rounding errors
                     if idx == len(calculation['items']) - 1:
                         diff = target_cod_amount - (running_total + share)
                         share += diff
                         share = round(share, 2)
                     
                     running_total += share
                     
                     # Per unit price
                     units = int(item['quantity'])
                     unit_price = share / units if units > 0 else 0
                     
                     sr_items.append({
                        "name": item['name'],
                        "sku": str(item['id']),
                        "units": units,
                        "selling_price": unit_price, # Modified Price
                        "discount": 0,
                        "tax": 0,
                        "hsn": 0
                     })
                 
                 sr_items_subtotal = target_cod_amount
                 total_discount = 0
                 shipping_charges = 0
                 
            else:
                 # Prepaid / Full Wallet case (Value should probably be real value?)
                 # Revert to standard logic
                 sr_items_subtotal = 0
                 sr_items = []
                 for item in calculation['items']:
                    s_price = float(item['price'])
                    u = int(item['quantity'])
                    sr_items_subtotal += (s_price * u)
                    sr_items.append({
                        "name": item['name'],
                        "sku": str(item['id']),
                        "units": u,
                        "selling_price": s_price,
                        "discount": 0,
                        "tax": 0,
                        "hsn": 0
                    })
                 total_discount = round(float(calculation['discount']) + float(calculation['wallet_deducted']), 2)
                 shipping_charges = round(float(calculation.get('shipping', 0)), 2)
                 sr_items_subtotal = round(sr_items_subtotal, 2)
                 
                 # Sanity check standard
                 if total_discount > (sr_items_subtotal + shipping_charges):
                     total_discount = sr_items_subtotal + shipping_charges

            # Sanitize ZIP
            clean_zip = ''.join(filter(str.isdigit, str(address.get('zip'))))

            # Sanitize Phone
            raw_phone = buyer.phone or ""
            clean_phone = ''.join(filter(str.isdigit, raw_phone))
            if len(clean_phone) > 10: clean_phone = clean_phone[-10:]
            
            # Determine Payment Method for SR
            # If final_payable <= 0 (Full Wallet), it's Prepaid. Else COD.
            total_payable = calculation['final_amount']
            payment_method_sr = "COD"
            if total_payable <= 0:
                payment_method_sr = "Prepaid"

            sr_order_data = {
                "order_id": cod_order_id,
                "order_date": datetime.now().strftime("%Y-%m-%d %H:%M"),
                "pickup_location": os.environ.get('SHIPROCKET_PICKUP_LOCATION', 'Primary'),
                "billing_customer_name": buyer.first_name,
                "billing_last_name": buyer.last_name,
                "billing_address": address.get('street'),
                "billing_address_2": address.get('landmark', ''),
                "billing_city": address.get('city'),
                "billing_pincode": int(clean_zip) if clean_zip.isdigit() else 0,
                "billing_state": address.get('state'),
                "billing_country": address.get('country'),
                "billing_email": buyer.email,
                "billing_phone": clean_phone,
                "shipping_is_billing": True,
                "order_items": sr_items,
                "payment_method": payment_method_sr,
                "sub_total": sr_items_subtotal, 
                "discount": total_discount,
                "shipping_charges": shipping_charges,
                "length": 10, "breadth": 10, "height": 10, "weight": 0.5
            }
            
            print(f"DEBUG: COD Order SR Payload: {json.dumps(sr_order_data, indent=2)}")
            
            sr_response = shiprocket_service.create_order(sr_order_data)
            print(f"DEBUG: COD Order SR Response: {sr_response}")
            
            if sr_response and 'order_id' not in sr_response:
                 # It failed but returned a dict (errors)
                 print(f"SR Creation Failed (COD): {sr_response}")
                 # Maybe we should throw error to frontend?
                 # But keeping fallback behavior for now.
        
    except Exception as e:
        print(f"SR COD Error: {e}")
        # Proceed with internal order anyway (Manual fulfillment backup)
        
    order_status = 'paid' if calculation['final_amount'] <= 0 else 'cod_pending'

    new_order = {
        'id': str(uuid.uuid4()),
        'user_id': buyer.id, **buyer.order_fields(),
        'razorpay_order_id': cod_order_id, # Internal Ref
        'razorpay_payment_id': None,
        'items': calculation['items'],
        'amount': calculation['subtotal'], # Base Amount
        'final_payable': calculation['final_amount'], # Actual to pay
        'wallet_amount': calculation['wallet_deducted'],
        'currency': 'INR',
        'address': address,
        'status': order_status,
        'payment_method': 'cod',
        'coupon_applied': calculation['coupon_applied'],
        'discount_amount': calculation['discount'],
        'shipping_amount': calculation.get('shipping', 0),
        'created_at': datetime.now().isoformat(),
        'shiprocket_order_id': sr_response.get('order_id') if sr_response else None, 
        'shiprocket_shipment_id': sr_response.get('shipment_id') if sr_response else None,
        'awb_code': sr_response.get('awb_code') if sr_response else None
    }
    
    with order_lock:
        orders = read_json(ORDERS_FILE)
        orders.append(new_order)
        write_json(ORDERS_FILE, orders)
    
    clear_saved_cart(buyer.email)
    return jsonify({'status': 'success', 'order_id': cod_order_id, 'internal_order_id': new_order['id']})


PENDING_ORDERS_FILE = os.path.join(DATA_DIR, 'pending_orders.json')


def _save_pending_order(pending_order):
    """Persist a created-but-unpaid online order so a captured payment can always be matched (webhook or verify)."""
    cutoff = (datetime.now() - timedelta(days=3)).isoformat()  # abandoned checkouts; Razorpay orders are long settled by then
    with order_lock:
        pending = [p for p in read_json(PENDING_ORDERS_FILE)
                   if p.get('razorpay_order_id') != pending_order['razorpay_order_id'] and p.get('created_at', '') > cutoff]
        pending.append(pending_order)
        write_json(PENDING_ORDERS_FILE, pending)


def _find_pending_order(razorpay_order_id):
    return next((p for p in read_json(PENDING_ORDERS_FILE) if p.get('razorpay_order_id') == razorpay_order_id), None)


def _update_order(order):
    with order_lock:
        orders = read_json(ORDERS_FILE)
        for i, o in enumerate(orders):
            if o['id'] == order['id']:
                orders[i] = order
                break
        write_json(ORDERS_FILE, orders)


def create_prepaid_shipment(order, user):
    """Create the Shiprocket order (+ AWB when a courier is available) for a paid online order and store the ids."""
    # --- SHIPROCKET ORDER CREATION (Prepaid) ---
    try:
        # Prepare Items & Stats
        sr_items_subtotal = 0
        sr_items = []
        for item in order['items']:
            s_price = float(item['price'])
            u = int(item['quantity'])
            sr_items_subtotal += (s_price * u)
            sr_items.append({
                "name": item['name'],
                "sku": str(item['id']),
                "units": u,
                "selling_price": s_price,
                "discount": 0,
                "tax": 0,
                "hsn": 0
            })

        # Ensure sub_total is rounded
        sr_items_subtotal = round(sr_items_subtotal, 2)

        # Calculate Discount (Coupon + Wallet)
        # order['amount'] is the Final Payable (which matches RP payment).
        # We want Shiprocket Total to match this.
        # Total = Subtotal - Discount + Shipping
        # Discount = Subtotal + Shipping - Total

        shipping_charges = round(float(order.get('shipping_amount', 0)), 2)

        # Use stored values if available, else derive
        wallet_ded = float(order.get('wallet_deduction', 0))
        coupon_disc = float(order.get('discount_amount', 0))
        total_discount = round(wallet_ded + coupon_disc, 2)

        # Format Phone
        billing_phone = user.phone or ""
        clean_phone = ''.join(filter(str.isdigit, billing_phone))
        if len(clean_phone) > 10: clean_phone = clean_phone[-10:]

        pickup_loc = os.environ.get('SHIPROCKET_PICKUP_LOCATION', 'Primary')

        # 1. Check Serviceability & Pick Best Courier
        # Default pickup location
        pickup_loc_pincode = os.environ.get('SHIPROCKET_PICKUP_PINCODE', '122004')

        # Sanitize ZIP
        raw_zip = str(order['address'].get('zip', ''))
        clean_zip = ''.join(filter(str.isdigit, raw_zip))

        serviceability = shiprocket_service.check_serviceability(pickup_loc_pincode, clean_zip, 0.5, 0) # 0 for Prepaid
        best_courier_prepaid = shiprocket_service.select_best_courier(serviceability)

        selected_courier_id = best_courier_prepaid['courier_company_id'] if best_courier_prepaid else None

        sr_order_data = {
            "order_id": order['id'], # Channel Order ID
            "order_date": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "pickup_location": pickup_loc,
            "billing_customer_name": user.first_name,
            "billing_last_name": user.last_name,
            "billing_address": order['address'].get('street'),
            "billing_address_2": order['address'].get('landmark', ''),
            "billing_city": order['address'].get('city'),
            "billing_pincode": int(clean_zip) if clean_zip else 0,
            "billing_state": order['address'].get('state'),
            "billing_country": order['address'].get('country'),
            "billing_email": user.email or "", 
            "billing_phone": clean_phone,
            "shipping_is_billing": True,
            "order_items": sr_items,
            "payment_method": "Prepaid",
            "sub_total": sr_items_subtotal,
            "discount": total_discount,
            "shipping_charges": shipping_charges,
            "length": 10, "breadth": 10, "height": 10, "weight": 0.5
        }

        print(f"DEBUG: Prepaid Order SR Payload: {json.dumps(sr_order_data, indent=2)}")

        sr_response = shiprocket_service.create_order(sr_order_data)
        print(f"DEBUG: Prepaid Order SR Response: {sr_response}")

        # Update order with Shiprocket ID if successful
        if sr_response and 'order_id' in sr_response:
             order['shiprocket_order_id'] = sr_response['order_id']
             order['shiprocket_shipment_id'] = sr_response.get('shipment_id')

             # AUTOMATE: Generate AWB
             if selected_courier_id and sr_response.get('shipment_id'):
                 awb_res = shiprocket_service.generate_awb(sr_response['shipment_id'], selected_courier_id)
                 if awb_res and awb_res.get('awb_assign_status') == 1:
                     sr_response['awb_code'] = awb_res['response']['data']['awb_code']
                     order['awb_code'] = sr_response['awb_code']
                 # else:
                     # print(f"AWB Generation Failed (Prepaid): {awb_res}")

             _update_order(order)


    except Exception as e:
         print(f"Shiprocket Integration Error (Prepaid): {e}")
    return order


def finalize_paid_order(order, razorpay_payment_id):
    """Turn a pending online order into a paid order. Idempotent: /verify and the webhook may both call it.
    Returns the stored order."""
    with order_lock:
        orders = read_json(ORDERS_FILE)
        existing = next((o for o in orders if o.get('razorpay_order_id') == order['razorpay_order_id']), None)
        if existing:
            return existing
        order['status'] = 'paid'
        order['razorpay_payment_id'] = razorpay_payment_id
        orders.append(order)
        write_json(ORDERS_FILE, orders)
        write_json(PENDING_ORDERS_FILE, [p for p in read_json(PENDING_ORDERS_FILE)
                                         if p.get('razorpay_order_id') != order['razorpay_order_id']])

    user = order_contact(order)

    # --- COUPON USAGE --- payment is already captured, so never drop the order; flag overuse for the admin instead
    if order.get('coupon_applied'):
        with coupon_lock:
            coupons = read_json(COUPONS_FILE)
            for c in coupons:
                if c['code'] == order['coupon_applied']:
                    if c.get('limit_type') == 'global' and c.get('times_used', 0) >= c.get('max_uses', 0):
                        order['coupon_over_limit'] = True
                    c['times_used'] = c.get('times_used', 0) + 1
                    c.setdefault('used_by_users', [])
                    if order_key(order) not in c['used_by_users']:
                        c['used_by_users'].append(order_key(order))
                    break
            write_json(COUPONS_FILE, coupons)

    # --- WALLET DEDUCTION (partial wallet + online) ---
    if order.get('user_id') and order.get('use_wallet') and order.get('wallet_deduction', 0) > 0:
        add_wallet_balance(order['user_id'], -order['wallet_deduction'], "Order Payment (Partial/Online)", "PAID_ONLINE")
        order['wallet_amount'] = order['wallet_deduction']

    _update_order(order)
    clear_saved_cart(order.get('customer_email'))

    if user:
        create_prepaid_shipment(order, user)

    return order


@app.route('/api/payment/verify', methods=['POST'])
def verify_payment():
    data = request.json
    razorpay_order_id = data.get('razorpay_order_id')
    razorpay_payment_id = data.get('razorpay_payment_id')
    razorpay_signature = data.get('razorpay_signature')
    save_as_default = data.get('save_as_default', False)

    params_dict = {
        'razorpay_order_id': razorpay_order_id,
        'razorpay_payment_id': razorpay_payment_id,
        'razorpay_signature': razorpay_signature
    }

    try:
        razorpay_client.utility.verify_payment_signature(params_dict)

        order = session.get('pending_order')
        if not order or order.get('razorpay_order_id') != razorpay_order_id:
            order = _find_pending_order(razorpay_order_id)
        if not order:
            # Already finalized (e.g. by the webhook) or unknown
            done = next((o for o in read_json(ORDERS_FILE) if o.get('razorpay_order_id') == razorpay_order_id), None)
            if done and owns_order(done):
                return jsonify({'status': 'success', 'order_id': done['id']})
            return jsonify({'error': 'Order not found'}), 404
        if not owns_order(order):
            return jsonify({'error': 'Order not found'}), 404

        # Update Address if saved in pending order
        address = order.get('address')
        if save_as_default and address and current_user.is_authenticated:
             # (Address saving logic reuse - ideally should be a function)
             users = read_json(USERS_FILE)
             for user in users:
                if user['id'] == current_user.id:
                    if 'addresses' not in user: user['addresses'] = []
                    new_addr = {
                        'id': str(uuid.uuid4()),
                        'label': 'Delivery Address',
                        'is_default': False
                    }
                    
                    pii_data = {
                        'street': address.get('street'),
                        'landmark': address.get('landmark', ''),
                        'city': address.get('city'),
                        'state': address.get('state'),
                        'zip': address.get('zip'),
                        'country': address.get('country')
                    }
                    new_addr['data_encrypted'] = encrypt_data(json.dumps(pii_data), 'address')
                    
                    user['addresses'].append(new_addr)
                    user['default_address_id'] = new_addr['id']
                    break
             write_json(USERS_FILE, users)
             
        order = finalize_paid_order(order, razorpay_payment_id)

        session.pop('pending_order', None)
        session.modified = True

        return jsonify({'status': 'success', 'order_id': order['id']})
    except razorpay.errors.SignatureVerificationError:
        return jsonify({'error': 'Payment verification failed'}), 400
    except Exception as e:
        print(f"Verify Error: {e}")
        return jsonify({'error': 'Server error'}), 500


@app.route('/api/payment/webhook', methods=['POST'])
@csrf.exempt
@limiter.exempt
def razorpay_webhook():
    """Razorpay server-to-server callback: records paid orders even if the shopper's browser never calls /verify.
    Configure in Razorpay Dashboard -> Webhooks with events payment.captured and order.paid."""
    secret = os.environ.get('RAZORPAY_WEBHOOK_SECRET')
    if not secret or not razorpay_client:
        return jsonify({'error': 'Webhook not configured'}), 503
    body = request.get_data(as_text=True)
    try:
        razorpay_client.utility.verify_webhook_signature(body, request.headers.get('X-Razorpay-Signature', ''), secret)
    except razorpay.errors.SignatureVerificationError:
        return jsonify({'error': 'Invalid signature'}), 400

    event = json.loads(body)
    if event.get('event') in ('payment.captured', 'order.paid'):
        payment = event.get('payload', {}).get('payment', {}).get('entity', {})
        pending = _find_pending_order(payment.get('order_id'))
        if pending:
            finalize_paid_order(pending, payment.get('id'))
    return jsonify({'status': 'ok'})


@app.route('/api/orders/cancel', methods=['POST'])
@login_required
def cancel_order():
    data = request.json
    order_id = data.get('order_id')
    reason = data.get('reason')
    
    if not order_id:
        return jsonify({'error': 'Missing Order ID'}), 400
    if not reason:
        return jsonify({'error': 'Cancellation reason is mandatory'}), 400
        
    # Check permissions and status first (reading without lock for quick check, though race condition possible)
    # Ideally should be inside the refund function or a locked block, but refund functions handle locks.
    # We will do a quick check here for user feedback.
    orders = read_json(ORDERS_FILE)
    target_order = next((o for o in orders if o['id'] == order_id), None)
    
    if not target_order:
         return jsonify({'error': 'Order not found'}), 404
         
    if target_order.get('user_id') != current_user.id:
         return jsonify({'error': 'Unauthorized'}), 403
         
    if target_order.get('status') == 'cancelled':
         return jsonify({'error': 'Order already cancelled'}), 400
         
    if target_order.get('status') == 'refunded':
         return jsonify({'error': 'Order already refunded'}), 400

    method = target_order.get('payment_method')
    current_status = target_order.get('status')
    
    # --- GLOBAL HANDLER FOR MIXED WALLET REFUNDS ---
    # If order has wallet usage, and we are cancelling/returning, we MUST refund the wallet part.
    # We do this independently of the main method (online/cod) if not already handled inside specific functions.
    # Our updated `refund_prepaid_order` handles it. `refund_cod_order` handles it.
    # `refund_wallet_order` handles full wallet.
    # But for standard COD Cancellation (Pre-Delivery), we must refund wallet part if used!
    
    wallet_usage = target_order.get('wallet_amount', 0)
    
    # HANDLE RETURNS separately if status is 'delivered'
    if current_status == 'delivered':
        if not return_window_open(target_order):
            return jsonify({'error': f"The {BUSINESS['return_days']}-day return window for this order has closed."}), 400
        # PROCESS RETURN - Initiate Shiprocket Return
        # 1. Prepare Return Data
        # Sanitize Data
        raw_zip = str(target_order['address'].get('zip', ''))
        clean_zip = ''.join(filter(str.isdigit, raw_zip))
        
        raw_phone = current_user.phone or ""
        # Basic phone cleanup (keep digits)
        clean_phone = ''.join(filter(str.isdigit, raw_phone))
        if len(clean_phone) > 10: clean_phone = clean_phone[-10:] # Last 10 digits
        
        return_data = {
            "order_id": target_order.get('shiprocket_order_id') or order_id, 
            "order_date": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "channel_id": "", 
            "pickup_customer_name": current_user.first_name,
            "pickup_last_name": current_user.last_name,
            "pickup_address": target_order['address'].get('street', '')[:80], # SR limit
            "pickup_address_2": target_order['address'].get('landmark', ''),
            "pickup_city": target_order['address'].get('city'),
            "pickup_state": target_order['address'].get('state'),
            "pickup_country": target_order['address'].get('country'),
            "pickup_pincode": int(clean_zip) if clean_zip else 0,
            "pickup_email": current_user.email,
            "pickup_phone": clean_phone,
            "order_items": [
                {
                    "name": item['name'],
                    "sku": str(item['id']), # Ensure string
                    "units": int(item.get('quantity', 1)),
                    "selling_price": float(item['price']),
                    "discount": 0,
                    "qc_enable": False # Disable QC to simplify for now
                } for item in target_order['items']
            ],
            "payment_method": "Prepaid",
            "length": 10, "breadth": 10, "height": 10, "weight": 0.5
        }
        
        # Calculate Subtotal
        sub_total = sum(item.get('selling_price') * item.get('units') for item in return_data['order_items'])

        # Add Merchant Details as SHIPPING DESTINATION (Where return goes to)
        # Using hardcoded/Env values for now as 'shipping_*'
        return_data.update({
            "sub_total": sub_total,
            "shipping_customer_name": "NTS Warehouse",
            "shipping_last_name": "",
            "shipping_address": "Sector 48, Sohna Road",
            "shipping_address_2": "",
            "shipping_city": "Gurugram",
            "shipping_state": "Haryana",
            "shipping_country": "India",
            "shipping_pincode": 122004, # Merchant Zip
            "shipping_email": os.environ.get('SUPPORT_EMAIL', ''),
            "shipping_phone": "9876543210" # Merchant Phone
        })
        
        print(f"DEBUG: Shiprocket Return Payload: {json.dumps(return_data, indent=2)}")

        # 2. Call Shiprocket
        try:
             sr_return = shiprocket_service.create_return_order(return_data)
             print(f"DEBUG: Shiprocket Return Response: {sr_return}")
             
             if sr_return and (sr_return.get('shipment_id') or sr_return.get('order_id')):
                 # Success
                 with order_lock:
                     # Update order status
                     for o in orders:
                         if o['id'] == order_id:
                             o['status'] = 'return_initiated'
                             o['return_shipment_id'] = sr_return.get('shipment_id')
                             o['return_order_id'] = sr_return.get('order_id')
                             o['return_awb'] = sr_return.get('awb_code') # If available immediately
                             o['return_initiated_at'] = datetime.now().isoformat()
                             o['return_reason'] = reason
                             
                             # Log ID for sync
                             o['shiprocket_return_order_id'] = sr_return.get('order_id')
                             break
                     write_json(ORDERS_FILE, orders)
                     
                 return jsonify({'status': 'success', 'message': 'Return initiated. Pickup will be scheduled.'})
             else:
                 error_msg = sr_return.get('message') or sr_return.get('error') or "Unknown error"
                 return jsonify({'error': f'Failed to create return shipment: {error_msg}'}), 400
                 
        except Exception as e:
            print(f"Return Creation Error: {e}")
            return jsonify({'error': 'System error initiating return'}), 500

    # STANDARD CANCELLATION (Pre-Delivery)
    if method == 'online':
        # Auto-refund via Razorpay (includes wallet logic)
        success, msg = refund_prepaid_order(order_id, cancellation_reason=reason)
        if success:
             return jsonify({'status': 'success', 'message': f'Order cancelled and refund initiated. {msg}'})
        else:
             return jsonify({'error': f'Cancellation failed: {msg}'}), 400
             
    elif method == 'wallet':
        # Auto-refund to Wallet
        success, msg = refund_wallet_order(order_id, cancellation_reason=reason)
        if success:
             return jsonify({'status': 'success', 'message': f'Order cancelled and amount credited to wallet.'})
        else:
             return jsonify({'error': f'Cancellation failed: {msg}'}), 400
             
    else:
        # COD - Standard Cancellation
        # CRITICAL: If wallet was used in partial COD, we MUST refund it!
        # `refund_cod_order` refunds the *paid* amount. 
        # But Pre-Delivery COD means 'amount' (COD part) is NOT paid.
        # So we only want to refund 'wallet_amount'.
        
        with order_lock:
            # Re-read to be safe
            orders = read_json(ORDERS_FILE)
            for order in orders:
                if order.get('id') == order_id:
                     if order.get('status') in ['cancelled', 'refunded']:
                         return jsonify({'error': 'Order already handled'}), 400
                         
                     # Refund Wallet Part if exists
                     w_amt = order.get('wallet_amount', 0)
                     if w_amt > 0:
                         add_wallet_balance(current_user.id, w_amt, f"Refund (Wallet Part): Order Cancelled", order_id)
                         
                     # Cancel Shiprocket
                     sr_id = order.get('shiprocket_order_id')
                     if sr_id:
                        try:
                            shiprocket_service.cancel_order([sr_id])
                        except Exception as e:
                            print(f"SR Cancel Error: {e}")
                            
                     order['status'] = 'cancelled'
                     order['cancellation_reason'] = reason
                     order['cancelled_at'] = datetime.now().isoformat()
                     
                     create_notification('info', f"COD Order {order_id} cancelled by user.", order_id)
                     
                     write_json(ORDERS_FILE, orders)
                     return jsonify({'status': 'success', 'message': 'Order cancelled successfully' + (' (Wallet refunded)' if w_amt > 0 else '')})
            
    return jsonify({'error': 'Order not found during processing'}), 404

# --- ADMIN REFUND ENDPOINTS ---

@app.route('/api/admin/orders/<order_id>/refund/prepaid', methods=['POST'])
@login_required
@admin_required
def admin_refund_prepaid(order_id):
    success, msg = refund_prepaid_order(order_id)
    if success:
        return jsonify({'status': 'success', 'message': msg})
    else:
        return jsonify({'error': msg}), 400

@app.route('/api/admin/orders/<order_id>/refund/cod', methods=['POST'])
@login_required
@admin_required
def admin_refund_cod(order_id):
    success, msg = refund_cod_order(order_id)
    if success:
        return jsonify({'status': 'success', 'message': msg})
    else:
        return jsonify({'error': msg}), 400

@app.route('/api/admin/orders/<order_id>/refund/wallet', methods=['POST'])
@login_required
@admin_required
def admin_refund_wallet(order_id):
    success, msg = refund_wallet_order(order_id)
    if success:
        return jsonify({'status': 'success', 'message': msg})
    else:
        return jsonify({'error': msg}), 400

@app.route('/api/admin/orders/update_status', methods=['POST'])
@login_required
@admin_required
def admin_update_order_status():
    data = request.json
    order_id = data.get('order_id')
    new_status = data.get('status')
    
    if not order_id or not new_status:
        return jsonify({'error': 'Missing parameters'}), 400
        
    with order_lock:
        orders = read_json(ORDERS_FILE)
        order = next((o for o in orders if o['id'] == order_id), None)
        
        if not order:
            return jsonify({'error': 'Order not found'}), 404
            
        if new_status in ('shipped', 'delivered') and order.get('status') in RESERVING_STATUSES and not order.get('stock_deducted'):
            _adjust_stock(order.get('items', []), -1)  # manual dispatch outside the fulfilment desk
            order['stock_deducted'] = True
        order['status'] = new_status
        if new_status == 'delivered' and 'delivered_at' not in order:
             order['delivered_at'] = datetime.now().isoformat()
             
        write_json(ORDERS_FILE, orders)
        
    return jsonify({'status': 'success', 'message': f'Order status updated to {new_status}'})


# --- ADDRESS API ---

@app.route('/api/addresses', methods=['POST'])
@login_required
def add_address():
    """Add a new address for the user"""
    data = request.json
    
    # Validate required fields
    required = ['label', 'street', 'city', 'state', 'zip', 'country']
    if not all(data.get(field) for field in required):
        return jsonify({'error': 'Missing required fields'}), 400
    
    users = read_json(USERS_FILE)
    for user in users:
        if user['id'] == current_user.id:
            # Initialize addresses array if not exists
            if 'addresses' not in user:
                user['addresses'] = []
            
            # Create new address with unique ID
            new_address = {
                'id': str(uuid.uuid4()),
                'label': data.get('label'),
                'is_default': False # Will be set below
            }

            # Encrypt PII
            pii_data = {
                'street': data.get('street'),
                'landmark': data.get('landmark', ''),
                'city': data.get('city'),
                'state': data.get('state'),
                'zip': data.get('zip'),
                'country': data.get('country')
            }
            new_address['data_encrypted'] = encrypt_data(json.dumps(pii_data), 'address')
            
            user['addresses'].append(new_address)
            
            # Set as default if requested or if it's the first address
            if data.get('set_as_default') or len(user['addresses']) == 1:
                user['default_address_id'] = new_address['id']
            
            write_json(USERS_FILE, users)
            
            # Return decrypted for frontend
            response_addr = new_address.copy()
            response_addr.update(pii_data)
            return jsonify({'status': 'success', 'address': response_addr})
    
    return jsonify({'error': 'User not found'}), 404

@app.route('/api/addresses/<address_id>', methods=['PUT'])
@login_required
def update_address(address_id):
    """Update an existing address"""
    data = request.json
    
    users = read_json(USERS_FILE)
    for user in users:
        if user['id'] == current_user.id:
            addresses = user.get('addresses', [])
            for addr in addresses:
                if addr['id'] == address_id:
                    # Decrypt existing
                    try:
                        pii = json.loads(decrypt_data(addr.get('data_encrypted'), 'address'))
                    except:
                        pii = {} # Should trigger update validation if missing?

                    # Update fields
                    addr['label'] = data.get('label', addr.get('label'))
                    
                    # Update PII
                    pii['street'] = data.get('street', pii.get('street'))
                    pii['landmark'] = data.get('landmark', pii.get('landmark'))
                    pii['city'] = data.get('city', pii.get('city'))
                    pii['state'] = data.get('state', pii.get('state'))
                    pii['zip'] = data.get('zip', pii.get('zip'))
                    pii['country'] = data.get('country', pii.get('country'))

                    # Re-encrypt
                    addr['data_encrypted'] = encrypt_data(json.dumps(pii), 'address')
                    
                    # Set as default if requested
                    if data.get('set_as_default'):
                        user['default_address_id'] = address_id
                    elif user.get('default_address_id') == address_id:
                        # If uncheck default and it WAS default, unset it
                        user['default_address_id'] = None
                    
                    write_json(USERS_FILE, users)
                    
                    response_addr = addr.copy()
                    response_addr.update(pii)
                    return jsonify({'status': 'success', 'address': response_addr})
            
            return jsonify({'error': 'Address not found'}), 404
    
    return jsonify({'error': 'User not found'}), 404

@app.route('/api/cart', methods=['POST'])
@limiter.limit("60 per hour")
def save_cart():
    """Store the shopper's cart against their email once they type it at checkout (abandoned-cart follow-up)."""
    data = request.json or {}
    email = normalize_email(data.get('email'))
    items = data.get('items')
    if not email or not isinstance(items, list) or len(items) > 20:
        return jsonify({'error': 'Invalid cart'}), 400
    clean = [{'id': i.get('id'), 'selectedSize': i.get('selectedSize'), 'quantity': int(i.get('quantity', 1))}
             for i in items if isinstance(i, dict)]
    with order_lock:
        carts = [c for c in read_json(CARTS_FILE) if c['id'] != email]
        carts.append({'id': email, 'email': email, 'user_id': current_user.id if current_user.is_authenticated else None,
                      'items': clean, 'updated_at': datetime.now().isoformat()})
        write_json(CARTS_FILE, carts)
    return jsonify({'status': 'success'})


def _oauth_redirect_uri(provider):
    """Must match the redirect URI registered with the provider exactly. PUBLIC_BASE_URL pins it behind proxies."""
    base = env_value('PUBLIC_BASE_URL').rstrip('/')
    path = url_for('social_callback', provider=provider)
    return base + path if base else url_for('social_callback', provider=provider, _external=True)


def _safe_next(target):
    """Only allow redirects back to our own pages."""
    return target if target and target.startswith('/') and not target.startswith('//') and '\\' not in target else None


def _auth_failed(message):
    return redirect('/?' + urlencode({'login': '1', 'auth_error': message}))


@app.route('/auth/<provider>/login')
def social_login(provider):
    p = AUTH_PROVIDERS.get(provider)
    if not p or not p.enabled:
        return jsonify({'error': f'{provider} sign-in is not enabled'}), 404
    state = uuid.uuid4().hex
    session['oauth_state'] = state
    session['oauth_next'] = _safe_next(request.args.get('next'))
    return redirect(p.authorize_url(state, _oauth_redirect_uri(provider)))


@app.route('/auth/<provider>/callback')
def social_callback(provider):
    p = AUTH_PROVIDERS.get(provider)
    if not p or not p.enabled:
        return jsonify({'error': f'{provider} sign-in is not enabled'}), 404
    if request.args.get('error'):
        return _auth_failed(f'{p.label} sign-in was cancelled.')
    if not request.args.get('state') or request.args.get('state') != session.pop('oauth_state', None):
        return _auth_failed('Sign-in expired, please try again.')
    try:
        identity = p.fetch_identity(request.args.get('code'), _oauth_redirect_uri(provider))
        user_id = link_social_identity(identity)
    except ValueError as e:
        return _auth_failed(str(e))
    login_user(load_user(user_id), remember=True)
    session['customer_email'] = normalize_email(identity.email)
    return redirect(session.pop('oauth_next', None) or url_for('my_orders'))


# --- SHIPROCKET ROUTES ---

@app.route('/api/shiprocket/serviceability')
def check_serviceability():
    pincode = request.args.get('pincode', '').strip()
    raw_country = request.args.get('country', 'India')
    
    # Map country to ISO Code
    country_map = {
        'India': 'IN',
        'USA': 'US',
        'UK': 'GB'
    }
    country_code = country_map.get(raw_country, 'IN')
    
    if not pincode:
        return jsonify({'error': 'Pincode is required'}), 400

    # Default weight 0.5kg, Prepaid (cod=0) for check
    # Note: International usually requires dimensions
    pickup_pincode = os.environ.get('SHIPROCKET_PICKUP_PINCODE', '110001') # Default to Delhi Central if missing
    
    result = shiprocket_service.check_serviceability(
        pickup_postcode=pickup_pincode, 
        delivery_postcode=pincode, 
        weight=1,
        length=10,
        breadth=10,
        height=10,
        cod=0,
        country=country_code
    )
    
    # Handle response structure difference for International
    # International response usually has 'data' -> 'available_courier_companies' similar to domestic?
    # Or just 'data' -> list?
    # We'll normalize in select_best_courier if needed, but for now pass result.
    
    print(f"[SHIPROCKET DEBUG] API Result: {result.keys() if result else 'None'}")
    
    # Handle response structure difference for International
    # International response usually has 'data' -> 'available_courier_companies' similar to domestic?
    # Or just 'data' -> list?
    # We'll normalize in select_best_courier if needed, but for now pass result.
    
    if result and 'data' in result and 'available_courier_companies' in result['data']:
         # Recommend best courier
         best = shiprocket_service.select_best_courier(result)
         result['data']['recommended_courier_id'] = best['courier_company_id'] if best else None
         if best:
             print(f"[SHIPROCKET DEBUG] Recommended Courier ID set to: {best['courier_company_id']}")
         else:
             print("[SHIPROCKET DEBUG] No best courier could be selected.")
    else:
        print("[SHIPROCKET DEBUG] 'data' or 'available_courier_companies' missing in result.")

    return jsonify(result)

@app.route('/track-order')
def track_order_page():
    return render_template('track_order.html', user=current_user)

@app.route('/api/orders/sync-status', methods=['POST'])
@login_required
def sync_order_status():
    data = request.json
    order_id = data.get('order_id')
    if not order_id:
        return jsonify({'error': 'Order ID required'}), 400
        
    with order_lock:
        orders = read_json(ORDERS_FILE)
        order = next((o for o in orders if o['id'] == order_id), None)
        
        if not order:
            return jsonify({'error': 'Order not found'}), 404
        if order.get('user_id') != current_user.id:
            return jsonify({'error': 'Unauthorized'}), 403
            
        # If order is already final (delivered, cancelled, refunded), just return status
        # UNLESS it is 'delivered' and we want to allow Return, but we trust the DB status 'delivered'
        # But if it's 'shipped' or 'processing', we want to check if it became 'delivered'.
        
        current_status = order.get('status')
        if current_status in ['cancelled', 'refunded', 'returned']:
             return jsonify({'status': current_status})
             
        # Track via Shiprocket
        sr_id = order.get('shiprocket_order_id')
        # If we have an AWB, use that. Shiprocket API `track_awb` uses AWB.
        # But `shiprocket_service.track_awb` is wrapper.
        # Check if we saved AWB locally? Not yet. 
        # But `track_order_api` uses `shiprocket_service.track_awb(order_id)`. 
        # Wait, line 1927 calls `track_awb(order_id)`. If order_id is NOT AWB, it might fail?
        # Re-reading line 1927 in previous view: `response = shiprocket_service.track_awb(order_id)`
        # The user was confused in comments there. 
        # Ideally we should use `shiprocket_service.track_order_by_id(sr_id)` if we have SR ID.
        
        tracking_res = {}
        if sr_id:
             awb = order.get('awb_code')
             if awb:
                 tracking_res = shiprocket_service.track_awb(awb)
        
        if tracking_res and tracking_res.get('tracking_data'):
             # Check connection
             current_status_sr = tracking_res['tracking_data']['track_status']
             
             # MAP SR Status to App Status
             # Delivered -> delivered
             if current_status_sr == 1: # AWB Assigned
                  pass
             elif current_status_sr == 7: # Delivered
                  if current_status != 'delivered' and current_status != 'return_initiated':
                       order['status'] = 'delivered'
                       order['delivered_at'] = datetime.now().isoformat()
                       write_json(ORDERS_FILE, orders)
                       return jsonify({'status': 'delivered', 'updated': True})

        # --- RETURN STATUS SYNC ---
        if current_status == 'return_initiated':
             # Check status of RETURN shipment
             return_awb = order.get('return_awb')
             
             if return_awb:
                  # Track Return AWB
                  curr_track = shiprocket_service.track_awb(return_awb)
                  if curr_track and curr_track.get('tracking_data'):
                       # Check for "Picked Up" or "Delivered" (to merchant)
                       # Note: Check API response structure for multiple items
                       track_obj = curr_track['tracking_data']
                       track_stat = ""
                       
                       # Shiprocket track response varies. Sometimes 'shipment_track' is list.
                       if 'shipment_track' in track_obj and isinstance(track_obj['shipment_track'], list) and track_obj['shipment_track']:
                             track_stat = track_obj['shipment_track'][0].get('current_status', '').lower()
                       elif 'track_status' in track_obj: # Integer status
                             # mapped status?
                             pass
                       
                       # Safety: If we can't parse string, we might rely on integer status if documented.
                       # SR Status 18 = Picked Up? Need to verify. 
                       # For now relying on string 'picked' or 'transit'
                       
                       if 'picked' in track_stat or 'transit' in track_stat or 'delivered' in track_stat:
                           # Process REFUND now
                           method = order.get('payment_method')
                           order_id = order['id']
                           
                           # Call the appropriate refund function
                           success_ref = False
                           msg_ref = ""
                           
                           if method == 'cod':
                               success_ref, msg_ref = refund_cod_order(order_id)
                           elif method == 'online':
                               success_ref, msg_ref = refund_prepaid_order(order_id, cancellation_reason="Return Picked Up")
                           elif method == 'wallet':
                               success_ref, msg_ref = refund_wallet_order(order_id, cancellation_reason="Return Picked Up")
                               
                           if success_ref:
                               # Update status to 'returned' finally
                               order['status'] = 'returned'
                               write_json(ORDERS_FILE, orders)
                               return jsonify({'status': 'returned', 'refunded': True, 'message': 'Return picked up, refund processed.'})
        
        return jsonify({'status': current_status, 'updated': False})
        
        # If we can't track, we return current status
        if not tracking_res or 'tracking_data' not in tracking_res:
             return jsonify({'status': current_status})
             
        # Parse Status
        # Shiprocket response `tracking_data` -> `track_status` -> 1 (Delivered)? 
        # Or `current_status`: "Delivered"
        try:
            t_data = tracking_res.get('tracking_data', {})
            shipment_status = t_data.get('shipment_track', [{}])[0].get('current_status', '').upper()
            
            if 'DELIVERED' in shipment_status:
                if current_status != 'delivered':
                    order['status'] = 'delivered'
                    order['delivered_at'] = datetime.now().isoformat()
                    write_json(ORDERS_FILE, orders)
                    return jsonify({'status': 'delivered'})
        except:
            pass
            
        return jsonify({'status': current_status})

@app.route('/api/track-order')
@limiter.limit("30 per minute")
def track_order_api():
    """Order status by email + order number (works for guest checkout), or courier tracking by AWB."""
    email = normalize_email(request.args.get('email'))
    number = (request.args.get('order') or '').strip()
    if email and number:
        order = next((o for o in read_json(ORDERS_FILE) if o.get('customer_email') == email
                      and number in (o.get('razorpay_order_id'), o.get('id'))), None)
        if not order:
            return jsonify({'error': 'Order not found. Check the email and order number.'}), 404
        tracking = (shiprocket_service.track_awb(order['awb_code']) or {}).get('tracking_data') if order.get('awb_code') else None
        return jsonify({'order_number': order.get('razorpay_order_id'), 'status': order.get('status'),
                        'placed_at': order.get('created_at'), 'awb': order.get('awb_code'),
                        'items': [{'name': i.get('name'), 'size': i.get('selectedSize'), 'quantity': i.get('quantity')}
                                  for i in order.get('items', [])],
                        'tracking': tracking})
    awb = (request.args.get('order_id') or '').strip()
    if not awb:
        return jsonify({'error': 'Enter your email and order number.'}), 400
    return jsonify({'tracking': (shiprocket_service.track_awb(awb) or {}).get('tracking_data')})

@app.route('/api/addresses/<address_id>', methods=['DELETE'])
@login_required
def delete_address(address_id):
    """Delete an address"""
    users = read_json(USERS_FILE)
    for user in users:
        if user['id'] == current_user.id:
            addresses = user.get('addresses', [])
            original_len = len(addresses)
            user['addresses'] = [a for a in addresses if a['id'] != address_id]
            
            if len(user['addresses']) == original_len:
                return jsonify({'error': 'Address not found'}), 404
            
            # Clear default if deleted address was default
            if user.get('default_address_id') == address_id:
                user['default_address_id'] = user['addresses'][0]['id'] if user['addresses'] else None
            
            write_json(USERS_FILE, users)
            return jsonify({'status': 'success'})
    
    return jsonify({'error': 'User not found'}), 404

@app.route('/api/addresses/<address_id>/default', methods=['POST'])
@login_required
def set_default_address(address_id):
    """Set an address as default"""
    users = read_json(USERS_FILE)
    for user in users:
        if user['id'] == current_user.id:
            addresses = user.get('addresses', [])
            # Verify address exists
            if not any(a['id'] == address_id for a in addresses):
                return jsonify({'error': 'Address not found'}), 404
            
            user['default_address_id'] = address_id
            write_json(USERS_FILE, users)
            return jsonify({'status': 'success'})
    
    return jsonify({'error': 'User not found'}), 404


# --- RETURN & REFUND SECURE FLOW ---

@app.route('/api/orders/<order_id>/return', methods=['POST'])
@login_required
def request_return(order_id):
    """User requests a return for a delivered order."""
    data = request.json
    reason = data.get('reason')
    
    if not reason:
        return jsonify({'error': 'Return reason is required'}), 400

    with order_lock:
        orders = read_json(ORDERS_FILE)
        order_idx = next((i for i, o in enumerate(orders) if o['id'] == order_id), -1)
        
        if order_idx == -1:
            return jsonify({'error': 'Order not found'}), 404
        
        order = orders[order_idx]
        
        # Security: Order MUST be delivered to Request Return
        if order.get('status') != 'delivered':
            return jsonify({'error': 'Return can only be requested for Delivered items.'}), 400
        if not return_window_open(order):
            return jsonify({'error': f"The {BUSINESS['return_days']}-day return window for this order has closed."}), 400
            
        if order.get('user_id') != current_user.id:
            return jsonify({'error': 'Unauthorized'}), 403
            
        # Update Status
        order['status'] = 'return_requested'
        order['return_reason'] = reason
        order['return_requested_at'] = datetime.now().isoformat()
        
        write_json(ORDERS_FILE, orders)
        
        # Notify Admin? (TODO)
        create_notification('action_required', f"Return Requested for Order #{order.get('razorpay_order_id', order_id[:8])}", order_id)
        
        return jsonify({'status': 'return_requested', 'message': 'Return request submitted for approval.'})

@app.route('/api/admin/orders/<order_id>/approve_return', methods=['POST'])
@login_required
def approve_return(order_id):
    """Admin approves return -> triggers Shiprocket Return Order."""
    if not current_user.is_admin:
        return jsonify({'error': 'Unauthorized'}), 403
        
    with order_lock:
        orders = read_json(ORDERS_FILE)
        order_idx = next((i for i, o in enumerate(orders) if o['id'] == order_id), -1)
        
        if order_idx == -1:
            return jsonify({'error': 'Order not found'}), 404
            
        order = orders[order_idx]
        
        if order.get('status') != 'return_requested':
             return jsonify({'error': 'Order is not in return_requested state'}), 400
             
        # Prepare Shiprocket Return Payload
        # We need address details. The order object has 'address' dict.
        addr = order.get('address', {})
        
        # Shiprocket Create Return Payload matches their API
        # We need channel_order_id, and pickup address (which is customer address)
        
        # Retrieve original order creation details if possible to map SKUs?
        # For simplicity, we assume full return of all items in order.
        
        items = []
        for item in order.get('items', []):
            items.append({
                "name": item.get('name'),
                "sku": item.get('id'), # Assuming ID as SKU
                "units": item.get('quantity', 1),
                "selling_price": item.get('price'),
                "discount": 0
            })

        return_payload = {
            "order_id": order.get('shiprocket_order_id') or order_id, # Link to original SR order if possible, or use Channel ID
            "order_date": order.get('created_at', datetime.now().isoformat())[:10],
            "channel_id": "", # Optional if custom
            "pickup_customer_name": addr.get('label', 'Customer'),
            "pickup_last_name": "",
            "pickup_company_name": "",
            "pickup_address": addr.get('street', ''),
            "pickup_address_2": addr.get('landmark', ''),
            "pickup_city": addr.get('city', ''),
            "pickup_state": addr.get('state', ''),
            "pickup_country": addr.get('country', 'India'),
            "pickup_pincode": addr.get('zip', ''),
            "pickup_email": order.get('user_id'), # We don't have email in address always, user ID fallback or fetch user
            "pickup_phone": "9876543210", # Placeholder if encrypted, ideally decrypt phone
            "shipping_customer_name": "NTS Warehouse", # Return TO
            "shipping_last_name": "",
            "shipping_address": "Main Warehouse",
            "shipping_city": "New Delhi",
            "shipping_state": "Delhi",
            "shipping_country": "India",
            "shipping_pincode": os.environ.get('SHIPROCKET_PICKUP_PINCODE', '110001'),
            "payment_method": "PREPAID", # Return shipping is usually prepaid by Merchant ideally
            "order_items": items,
            "sub_total": order.get('amount', 0),
            "length": 10, "breadth": 10, "height": 10, "weight": 0.5
        }
        
        # CALL SHIPROCKET
        # Note: We need detailed phone number. In production decrypt `order.user.phone_encrypted`.
        # Here we use dummy or try to get from user object if loaded.
        
        sr_response = shiprocket_service.create_return_order(return_payload)
        
        if 'return_order_id' in sr_response or 'order_id' in sr_response:
             # Success
             order['status'] = 'return_approved'
             order['return_shiprocket_id'] = sr_response.get('return_order_id') or sr_response.get('order_id')
             order['return_shipment_id'] = sr_response.get('shipment_id')
             order['return_awb'] = sr_response.get('awb_code') # If auto-assigned
             
             write_json(ORDERS_FILE, orders)
             return jsonify({'status': 'return_approved', 'message': 'Return approved & Shiprocket Order Created', 'sr_data': sr_response})
        else:
             # Failed
             return jsonify({'error': 'Shiprocket Return Creation Failed', 'details': sr_response}), 500

@app.route('/api/admin/orders/<order_id>/refund_secure', methods=['POST'])
@login_required
def process_secure_refund(order_id):
    """Admin triggers refund ONLY if Return is verified (Picked Up/Delivered)."""
    if not current_user.is_admin:
        return jsonify({'error': 'Unauthorized'}), 403
        
    with order_lock:
        orders = read_json(ORDERS_FILE)
        order_idx = next((i for i, o in enumerate(orders) if o['id'] == order_id), -1)
        
        if order_idx == -1:
             return jsonify({'error': 'Order not found'}), 404
             
        order = orders[order_idx]
        
        # Safety Check: Must be in return flow
        if order.get('status') not in ['return_approved', 'return_initiated']:
             return jsonify({'error': 'Order is not in approved return state.'}), 400
             
        # CHECK SHIPROCKET STATUS
        return_awb = order.get('return_awb')
        if not return_awb:
             # Try to fetch if we have shipment_id?
             return jsonify({'error': 'No Return AWB found. Cannot verify return status.'}), 400
             
        # Call Tracking
        tracking = shiprocket_service.track_awb(return_awb)
        
        # Verify Status
        # accepted_statuses = ['PICKED UP', 'IN TRANSIT', 'DELIVERED', 'RTO DELIVERED', 'RETURN DELIVERED']
        # We check raw strings or codes.
        
        can_refund = False
        current_track_status = "Unknown"
        
        if tracking and tracking.get('tracking_data'):
             t_data = tracking['tracking_data']
             # Check specific return status logic
             # Usually 'current_status' inside 'shipment_track'
             if 'shipment_track' in t_data and t_data['shipment_track']:
                  current_track_status = t_data['shipment_track'][0].get('current_status', '').upper()
             else:
                  current_track_status = str(t_data.get('track_status', ''))
                  
             if any(s in current_track_status for s in ['PICKED', 'TRANSIT', 'DELIVERED']):
                  can_refund = True
        
        if not can_refund:
             # BYPASS FOR TESTING? User asked to check "if its delivered".
             # So we stick to "Delivered" or "Picked Up" at least.
             return jsonify({'error': f'Secure Check Failed. Return Status: {current_track_status}. Item must be Picked Up or Delivered.'}), 400
             
        # PROCEED REFUND
        method = order.get('payment_method')
        success, msg = False, ""
        
        if method == 'cod':
             success, msg = refund_cod_order(order_id)
        elif method == 'online':
             success, msg = refund_prepaid_order(order_id, cancellation_reason="Secure Return Refund")
        elif method == 'wallet':
             # pure wallet refund
             amt = order.get('amount', 0)
             s, m = add_wallet_balance(order['user_id'], amt, "Refund: Secure Return Processed", order_id)
             if s:
                 order['status'] = 'returned'
                 order['refund_status'] = 'processed'
                 order['refund_amount'] = amt
                 order['wallet_refunded'] = amt
                 order['refund_processed_at'] = datetime.now().isoformat()
                 write_json(ORDERS_FILE, orders)
                 success, msg = True, "Refund processed to Wallet."
             else:
                 success, msg = False, m

        if success:
             return jsonify({'status': 'returned', 'message': msg})
        else:
             return jsonify({'error': msg}), 400


# --- AUTH API ---

@app.route('/api/auth/signup', methods=['POST'])
def signup():
    data = request.json
    first_name = data.get('firstName')
    last_name = data.get('lastName', '')
    phone = data.get('phone')
    password = data.get('password')

    if not PHONE_LOGIN_ENABLED:
        return jsonify({'error': 'Phone sign-up is turned off. You can check out as a guest with your email.'}), 403
    if not all([first_name, phone, password]):
        return jsonify({'error': 'Missing required fields'}), 400
    
    if not validate_phone(phone):
        return jsonify({'error': 'Invalid phone number format.'}), 400

    users = read_json(USERS_FILE)
    phone_hash = hash_data(phone)
    
    # Check if phone already exists
    existing_user = next((u for u in users if u.get('phone_hash') == phone_hash), None)
    
    if existing_user:
        # If user is verified, block registration
        if existing_user.get('is_verified'):
            return jsonify({'error': 'Phone number already registered. Please login.'}), 409
        else:
            # User exists but not verified - update their details and resend OTP
            existing_user['first_name'] = first_name
            existing_user['last_name'] = last_name
            existing_user['password'] = bcrypt.generate_password_hash(password).decode('utf-8')
            existing_user['phone_encrypted'] = encrypt_data(phone, 'phone')
            existing_user['created_at'] = datetime.now().isoformat()
            
            write_json(USERS_FILE, users)
            
            # Generate and send OTP
            otp = str(random.randint(100000, 999999))
            expires = time.time() + 300
            
            otps = read_json(OTPS_FILE)
            otps = cleanup_otps(otps)
            otps[phone_hash] = {
                'otp_hash': hash_otp(otp),
                'expires_at': expires,
                'last_sent': time.time()
            }
            write_json(OTPS_FILE, otps)
            
            if not send_otp_sms(phone, otp):
                return jsonify({'error': 'SMS verification is not available right now.'}), 503
            session['pending_phone_hash'] = phone_hash
            
            return jsonify({'status': 'otp_sent', 'message': 'Verification code sent.'})

    # Hash Password
    pw_hash = bcrypt.generate_password_hash(password).decode('utf-8')

    # Create User Object (Unverified)
    new_user = {
        'id': str(uuid.uuid4()),
        'first_name': first_name,
        'last_name': last_name,
        'phone_encrypted': encrypt_data(phone, 'phone'),
        'phone_hash': phone_hash,
        'password': pw_hash,
        'is_verified': False,
        'created_at': datetime.now().isoformat(),
        'address_encrypted': None,
        'addresses': []
    }
    
    users.append(new_user)
    write_json(USERS_FILE, users)

    # Generate OTP
    otp = str(random.randint(100000, 999999))
    expires = time.time() + 300 
    
    otps = read_json(OTPS_FILE)
    otps = cleanup_otps(otps)
    otps[phone_hash] = {
        'otp_hash': hash_otp(otp), 
        'expires_at': expires,
        'attempts': 0,
        'last_sent': time.time()
    }
    write_json(OTPS_FILE, otps)
    
    # print(f"\n[DEV] OTP for {phone}: {otp}\n")
    if not send_otp_sms(phone, otp):
        return jsonify({'error': 'SMS verification is not available right now.'}), 503
    session['pre_auth_phone'] = phone
    session['auth_flow'] = 'signup'
    
    return jsonify({'message': 'User created. OTP sent.', 'step': 'verify'})

@app.route('/api/auth/login', methods=['POST'])
def login():
    data = request.json
    phone = data.get('phone')
    password = data.get('password')

    if not phone or not password:
        return jsonify({'error': 'Missing credentials'}), 400

    users = read_json(USERS_FILE)
    phone_hash = hash_data(phone)
    user = next((u for u in users if u.get('phone_hash') == phone_hash), None)

    is_admin_account = bool(user) and (user.get('is_admin') or user.get('id') == app.config.get('ADMIN_USER_ID'))
    if user and not PHONE_LOGIN_ENABLED and not is_admin_account:
        user = None  # customer phone login is off; same message as a wrong password
    if user and bcrypt.check_password_hash(user['password'], password):
        # Rate Limit Check (Simple: 1 min cooldown logic could go here, but focusing on structure first)
        
        otp = str(random.randint(100000, 999999))
        expires = time.time() + 300 
        
        otps = read_json(OTPS_FILE)
        otps = cleanup_otps(otps)
        otps[phone_hash] = {
            'otp_hash': hash_otp(otp), 
            'expires_at': expires,
            'attempts': 0,
            'last_sent': time.time()
        }
        write_json(OTPS_FILE, otps)
        
        # print(f"\n[DEV] OTP for {phone}: {otp}\n")
        if not send_otp_sms(phone, otp):
            return jsonify({'error': 'SMS verification is not available right now.'}), 503
        session['pre_auth_phone'] = phone
        session['auth_flow'] = 'login'
        return jsonify({'message': 'Credentials valid. OTP sent.', 'step': 'verify'})
    
    return jsonify({'error': 'Invalid phone number or password'}), 401

@app.route('/api/auth/resend', methods=['POST'])
def resend_otp():
    phone = session.get('pre_auth_phone')
    if not phone:
        return jsonify({'error': 'Session expired. Login again.'}), 401

    otps = read_json(OTPS_FILE)
    phone_hash = hash_data(phone)
    otp_record = otps.get(phone_hash)
    
    # Rate Limit: 60s cooldown
    if otp_record and otp_record.get('last_sent'):
        time_since = time.time() - otp_record['last_sent']
        if time_since < 60:
            return jsonify({'error': f'Please wait {int(60 - time_since)}s before resending.'}), 429

    otp = str(random.randint(100000, 999999))
    expires = time.time() + 300
    
    otps = cleanup_otps(otps) # Cleanup first
    otps[phone_hash] = {
        'otp_hash': hash_otp(otp), 
        'expires_at': expires,
        'attempts': 0,
        'last_sent': time.time()
    }
    write_json(OTPS_FILE, otps)
    
    # print(f"\n[DEV] RESEND OTP for {phone}: {otp}\n")
    if not send_otp_sms(phone, otp):
        return jsonify({'error': 'SMS verification is not available right now.'}), 503
    return jsonify({'message': 'OTP sent successfully.'})

@app.route('/api/auth/forgot-password', methods=['POST'])
def forgot_password():
    data = request.json
    phone = data.get('phone')
    
    if not phone:
        return jsonify({'error': 'System error: Missing phone'}), 400
        
    users = read_json(USERS_FILE)
    phone_hash = hash_data(phone)
    user = next((u for u in users if u.get('phone_hash') == phone_hash), None)
    
    if not user:
        return jsonify({'error': 'No account found with this phone number.'}), 404
        
    # Generate OTP
    otp = str(random.randint(100000, 999999))
    expires = time.time() + 300
    
    otps = read_json(OTPS_FILE)
    otps = cleanup_otps(otps)
    otps[phone_hash] = {
        'otp_hash': hash_otp(otp), 
        'expires_at': expires,
        'attempts': 0,
        'last_sent': time.time()
    }
    write_json(OTPS_FILE, otps)
    
    # print(f"\n[DEV] FORGOT OTP for {phone}: {otp}\n")
    if not send_otp_sms(phone, otp):
        return jsonify({'error': 'SMS verification is not available right now.'}), 503
    
    session['pre_auth_phone'] = phone
    session['auth_flow'] = 'reset'
    
    return jsonify({'status': 'success', 'message': 'OTP sent to your phone.'})

@app.route('/api/auth/reset-password', methods=['POST'])
def reset_password():
    phone = session.get('pre_auth_phone')
    flow = session.get('auth_flow')
    is_verified = session.get('reset_verified')
    
    if not phone or flow != 'reset' or not is_verified:
        return jsonify({'error': 'Unauthorized password reset attempt.'}), 401
        
    data = request.json
    new_password = data.get('password')
    
    if not new_password or len(new_password) < 6:
        return jsonify({'error': 'Password must be at least 6 characters.'}), 400
        
    users = read_json(USERS_FILE)
    phone_hash = hash_data(phone)
    user_idx = next((i for i, u in enumerate(users) if u.get('phone_hash') == phone_hash), -1)
    
    if user_idx == -1:
        return jsonify({'error': 'Account no longer exists.'}), 404
        
    # Hash and save
    pw_hash = bcrypt.generate_password_hash(new_password).decode('utf-8')
    users[user_idx]['password'] = pw_hash
    write_json(USERS_FILE, users)
    
    # Auto login after reset
    u_data = users[user_idx]
    user = User(
        id=u_data['id'],
        first_name=u_data['first_name'], 
        last_name=u_data['last_name'], 
        phone_encrypted=u_data.get('phone_encrypted'), 
        is_verified=True, 
        address_encrypted=u_data.get('address_encrypted'),
        addresses=u_data.get('addresses'),
        default_address_id=u_data.get('default_address_id'),
        is_admin=u_data.get('is_admin', False),
        wallet_balance=u_data.get('wallet_balance', 0.0)
    )
    login_user(user, remember=True)
    
    # Cleanup session
    session.pop('pre_auth_phone', None)
    session.pop('auth_flow', None)
    session.pop('reset_verified', None)
    
    return jsonify({'status': 'success', 'message': 'Password reset successful and logged in.'})

@app.route('/api/auth/verify', methods=['POST'])
def verify_auth_otp():
    data = request.json
    otp_input = data.get('otp')
    phone = session.get('pre_auth_phone')

    if not phone:
         return jsonify({'error': 'Session expired. Please login again.'}), 401

    otps = read_json(OTPS_FILE)
    phone_hash = hash_data(phone)
    otp_record = otps.get(phone_hash)
    
    if not otp_record:
        return jsonify({'error': 'Invalid request'}), 400
    
    # Check Max Attempts
    attempts = otp_record.get('attempts', 0)
    if attempts >= 5:
        # Optionally delete OTP to force resend
        del otps[phone_hash]
        write_json(OTPS_FILE, otps)
        return jsonify({'error': 'Too many failed attempts. Please request a new OTP.'}), 429
        
    if float(otp_record['expires_at']) < time.time():
        return jsonify({'error': 'OTP expired'}), 400
    
    if verify_otp(otp_input, otp_record['otp_hash']):
        # Success - Verify User
        flow = session.get('auth_flow')
        users = read_json(USERS_FILE)
        user_idx = next((i for i, u in enumerate(users) if u.get('phone_hash') == phone_hash), -1)
        
        if user_idx > -1:
            users[user_idx]['is_verified'] = True
            write_json(USERS_FILE, users)
            
            if flow == 'reset':
                session['reset_verified'] = True
                return jsonify({'message': 'OTP Verified', 'step': 'new_password'})
            else:
                u_data = users[user_idx]
                user = User(
                    id=u_data['id'], 
                    first_name=u_data['first_name'], 
                    last_name=u_data['last_name'], 
                    phone_encrypted=u_data.get('phone_encrypted'), 
                    is_verified=True, 
                    address_encrypted=u_data.get('address_encrypted'),
                    addresses=u_data.get('addresses'),
                    default_address_id=u_data.get('default_address_id'),
                    is_admin=u_data.get('is_admin', False),
                    wallet_balance=u_data.get('wallet_balance', 0.0)
                )
                login_user(user, remember=True)
                session.pop('pre_auth_phone', None)
                session.pop('auth_flow', None)
                
                # Clean up OTP
                del otps[phone_hash]
                write_json(OTPS_FILE, otps)
                
                return jsonify({'message': 'Login successful', 'user': {'firstName': user.first_name}})
    
    # Increment attempts on failure
    otp_record['attempts'] = attempts + 1
    write_json(OTPS_FILE, otps)
    return jsonify({'error': f'Invalid OTP. {5 - (attempts + 1)} attempts remaining.'}), 400

@app.route('/api/auth/logout', methods=['POST'])
@login_required
def logout():
    logout_user()
    return jsonify({'message': 'Logged out'})

@app.route('/api/auth/user')
def get_user():
    if current_user.is_authenticated:
        return jsonify({
            'authenticated': True,
            'id': current_user.id,
            'firstName': current_user.first_name,
            'lastName': current_user.last_name,
            'phone': current_user.phone,
            'profile_image': current_user.profile_image
        })
    return jsonify({'authenticated': False})

# --- RATINGS API ---
@app.route('/api/ratings/<int:product_id>')
def get_product_ratings(product_id):
    ratings = read_json(RATINGS_FILE)
    product_ratings = [r for r in ratings if r['product_id'] == product_id]
    
    # Calculate average
    avg = sum(r['rating'] for r in product_ratings) / len(product_ratings) if product_ratings else 0
    
    return jsonify({
        'ratings': product_ratings,
        'average': round(avg, 1),
        'count': len(product_ratings)
    })

@app.route('/api/ratings/check_eligibility/<int:product_id>')
@login_required
def check_eligibility(product_id):
    # 1. Check if user bought the product
    orders = read_json(ORDERS_FILE)
    has_bought = False
    for order in orders:
        if order.get('user_id') == current_user.id:
            # Check items
            for item in order.get('items', []):
                if int(item.get('id')) == int(product_id):
                    has_bought = True
                    break
        if has_bought: break

    if not has_bought:
        return jsonify({'eligible': False, 'reason': 'purchase_required', 'error': 'You must purchase this product before leaving a review.'})

    # 2. Check if user already rated this product
    ratings = read_json(RATINGS_FILE)
    if any(r['product_id'] == product_id and r['user_id'] == current_user.id for r in ratings):
        return jsonify({'eligible': False, 'reason': 'already_rated', 'error': 'You have already rated this product.'})

    return jsonify({'eligible': True})

@app.route('/api/ratings/submit', methods=['POST'])
@login_required
def submit_rating():
    data = request.json
    product_id = data.get('product_id')
    rating_val = data.get('rating')
    comment = data.get('comment', '')

    if not product_id or not rating_val:
        return jsonify({'error': 'Missing required fields'}), 400

    # 1. Check if user bought the product
    orders = read_json(ORDERS_FILE)
    has_bought = False
    for order in orders:
        if order.get('user_id') == current_user.id:
            # Check items
            for item in order.get('items', []):
                if int(item.get('id')) == int(product_id):
                    has_bought = True
                    break
        if has_bought: break

    if not has_bought:
        return jsonify({'error': 'You can only rate products you have purchased.'}), 403

    # 2. Check if user already rated this product
    ratings = read_json(RATINGS_FILE)
    if any(r['product_id'] == product_id and r['user_id'] == current_user.id for r in ratings):
        return jsonify({'error': 'You have already rated this product.'}), 451

    # 3. Add rating
    new_rating = {
        'id': str(uuid.uuid4()),
        'product_id': product_id,
        'user_id': current_user.id,
        'user_name': f"{current_user.first_name} {current_user.last_name}",
        'rating': int(rating_val),
        'comment': comment,
        'created_at': datetime.now().isoformat()
    }
    ratings.append(new_rating)
    write_json(RATINGS_FILE, ratings)

    return jsonify({'status': 'success', 'message': 'Rating submitted successfully.'})

@app.route('/api/ratings/edit', methods=['POST'])
@login_required
def edit_user_rating():
    data = request.json
    rating_id = data.get('rating_id')
    new_rating = data.get('rating')
    new_comment = data.get('comment', '')
    
    ratings = read_json(RATINGS_FILE)
    for r in ratings:
        if r['id'] == rating_id:
            if r['user_id'] != current_user.id:
                return jsonify({'error': 'Unauthorized'}), 403
            r['rating'] = int(new_rating)
            r['comment'] = new_comment
            r['is_edited'] = True
            r['updated_at'] = datetime.now().isoformat()
            write_json(RATINGS_FILE, ratings)
            return jsonify({'status': 'success'})
    return jsonify({'error': 'Rating not found'}), 404

@app.route('/api/ratings/delete', methods=['POST'])
@login_required
def delete_user_rating():
    data = request.json
    rating_id = data.get('rating_id')
    
    ratings = read_json(RATINGS_FILE)
    new_ratings = [r for r in ratings if not (r['id'] == rating_id and r['user_id'] == current_user.id)]
    
    if len(new_ratings) == len(ratings):
        return jsonify({'error': 'Rating not found or unauthorized'}), 404
        
    write_json(RATINGS_FILE, new_ratings)
    return jsonify({'status': 'success'})

@app.route('/api/ratings/report', methods=['POST'])
@login_required
def report_rating():
    data = request.json
    rating_id = data.get('rating_id')
    reason = data.get('reason', 'No reason provided')
    
    if not rating_id:
        return jsonify({'error': 'Missing rating ID'}), 400
        
    reports = read_json(REPORTS_FILE)
    if not isinstance(reports, list): reports = []
    
    # Check if already reported by this user
    if any(rep['rating_id'] == rating_id and rep['user_id'] == current_user.id for rep in reports):
        return jsonify({'error': 'You have already reported this review.'}), 400
        
    new_report = {
        'id': str(uuid.uuid4()),
        'rating_id': rating_id,
        'user_id': current_user.id,
        'reason': reason,
        'created_at': datetime.now().isoformat()
    }
    reports.append(new_report)
    write_json(REPORTS_FILE, reports)
    return jsonify({'status': 'success', 'message': 'Review reported successfully.'})


# --- COUPON API ---

@app.route('/api/admin/coupons/add', methods=['POST'])
@login_required
@admin_required
def add_coupon():
    data = request.json
    code = data.get('code')
    discount_type = data.get('discount_type') # 'percentage' or 'flat'
    discount_value = data.get('discount_value')
    min_order_amount = data.get('min_order_amount', 0)
    limit_type = data.get('limit_type', 'unlimited') # unlimited, global, per_user
    max_uses = data.get('max_uses') # Only if limit_type is global or per_user_limited (if implemented)
    
    if not code or not discount_type or discount_value is None:
        return jsonify({'error': 'Missing required fields'}), 400
        
    coupons = read_json(COUPONS_FILE)
    if any(c['code'] == code.upper() for c in coupons):
        return jsonify({'error': 'Coupon code already exists'}), 400
        
    new_coupon = {
        'id': str(uuid.uuid4()),
        'code': code.upper(),
        'discount_type': discount_type,
        'discount_value': float(discount_value),
        'min_order_amount': float(min_order_amount),
        'limit_type': limit_type,
        'max_uses': int(max_uses) if max_uses else None,
        'times_used': 0,
        'used_by_users': [], # List of user IDs who used this
        'created_at': datetime.now().isoformat()
    }
    
    coupons.append(new_coupon)
    write_json(COUPONS_FILE, coupons)
    return jsonify({'status': 'success', 'coupon': new_coupon})

@app.route('/api/admin/coupons/delete', methods=['POST'])
@login_required
@admin_required
def delete_coupon():
    data = request.json
    coupon_id = data.get('coupon_id')
    
    coupons = read_json(COUPONS_FILE)
    coupons = [c for c in coupons if c['id'] != coupon_id]
    write_json(COUPONS_FILE, coupons)
    return jsonify({'status': 'success'})

@app.route('/api/coupons/validate', methods=['POST'])
def validate_coupon():
    data = request.json
    code = data.get('code')
    cart_amount = data.get('amount') # Passed in current currency value
    user_id = current_user.id if current_user.is_authenticated else None
    
    if not code or not cart_amount:
         return jsonify({'error': 'Invalid request'}), 400
         
    coupons = read_json(COUPONS_FILE)
    coupon = next((c for c in coupons if c['code'] == code.upper()), None)
    
    if not coupon:
        return jsonify({'valid': False, 'error': 'Invalid Coupon Code'})
        
    # Check Min Amount
    if float(cart_amount) < coupon.get('min_order_amount', 0):
         return jsonify({'valid': False, 'error': f'Minimum order amount of {coupon.get("min_order_amount")} required.'})

    # Check Usage Limits
    limit_type = coupon.get('limit_type', 'unlimited')
    times_used = coupon.get('times_used', 0)
    max_uses = coupon.get('max_uses')
    used_by = coupon.get('used_by_users', [])
    
    if limit_type == 'global':
        if max_uses is not None and times_used >= max_uses:
            return jsonify({'valid': False, 'error': 'This coupon has reached its usage limit.'})
    
    if limit_type == 'per_user':
        if not user_id:
             return jsonify({'valid': False, 'error': 'You must be logged in to use this coupon.'})
        if user_id in used_by:
             return jsonify({'valid': False, 'error': 'You have already used this coupon.'})
             
    if limit_type == 'new_user':
        if not user_id:
             return jsonify({'valid': False, 'error': 'You must be logged in to use this coupon.'})
        
        # Check past orders
        orders = read_json(ORDERS_FILE)
        user_orders = [o for o in orders if o.get('user_id') == user_id and o.get('status') != 'cancelled']
        if len(user_orders) > 0:
             return jsonify({'valid': False, 'error': 'This coupon is valid for new users only.'})

    # Calculate Discount
    discount = 0
    if coupon['discount_type'] == 'percentage':
        discount = (float(cart_amount) * coupon['discount_value']) / 100
    else:
        discount = coupon['discount_value']
        
    # Ensure discount doesn't exceed total
    if discount > float(cart_amount):
        discount = float(cart_amount)
        
    new_total = float(cart_amount) - discount
    
    return jsonify({
        'valid': True, 
        'code': coupon['code'],
        'discount_type': coupon['discount_type'],
        'discount_value': coupon['discount_value'],
        'discount_amount': round(discount, 2),
        'new_total': round(new_total, 2)
    })

@app.after_request
def set_security_headers(response):
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Content-Security-Policy'] = "default-src * 'unsafe-inline' 'unsafe-eval' data: blob:;"
    # Note: unsafe-inline/eval required for some JS libs/razorpay unless strictly refactored.
    return response

log_integration_status()

if __name__ == '__main__':
    # Dev server only. Never enable debug in production: the Werkzeug debugger allows remote code execution.
    app.run(debug=os.environ.get('FLASK_DEBUG') == '1')
