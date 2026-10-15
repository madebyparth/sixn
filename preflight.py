"""Go-live check for NTS. Run on the production host after filling .env:  python preflight.py
Read-only: it logs in / lists, never creates orders or charges anything. Exit code 1 if anything blocks launch.
Empty values and placeholders (REPLACE_ME, your_..._here) count as missing."""
import os
import sys

from dotenv import load_dotenv

load_dotenv()
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from utils.config import env_value, env_flag

blocking, optional = [], []


def check(ok, label, fix='', required=True):
    tag = 'OK' if ok else ('FAIL' if required else 'OFF')
    print(f"[{tag}] {label}" + ('' if ok else f" -> {fix}"))
    if not ok:
        (blocking if required else optional).append(label)


print("== Required for launch ==")
for key in ('SECRET_KEY', 'MONGO_URI', 'ENCRYPTION_KEY', 'PHONE_ENCRYPTION_KEY', 'ADDRESS_ENCRYPTION_KEY', 'ADMIN_USER_ID',
            'BUSINESS_LEGAL_NAME', 'BUSINESS_ADDRESS', 'SUPPORT_EMAIL', 'SUPPORT_PHONE', 'GRIEVANCE_OFFICER_NAME',
            'RAZORPAY_KEY', 'RAZORPAY_SECRET', 'RAZORPAY_WEBHOOK_SECRET',
            'SHIPROCKET_EMAIL', 'SHIPROCKET_PASSWORD', 'SHIPROCKET_PICKUP_LOCATION', 'SHIPROCKET_PICKUP_PINCODE',
            'TWILIO_SID', 'TWILIO_TOKEN', 'TWILIO_PHONE'):
    check(bool(env_value(key)), f"{key} set", "fill it in .env (see .env.example)")
check(not env_flag('FLASK_DEBUG'), "Flask debug off", "set FLASK_DEBUG=0")
check(env_value('RAZORPAY_KEY').startswith('rzp_live_'), "Razorpay live key", "use the rzp_live_ key after KYC")
check(not env_flag('REQUIRE_EMAIL_CONFIRMATION'), "REQUIRE_EMAIL_CONFIRMATION off",
      "no confirmation email is sent yet, so this would block guest checkout")

# MongoDB + catalog
from utils.db import mongo_db, read_json
check(mongo_db is not None, "MongoDB reachable", "check MONGO_URI")
if mongo_db is not None:
    products = read_json('assets/products.json')
    check(len(products) > 0, f"{len(products)} products in catalog", "run python seed_nts.py")
    check(all(not p.get('image', '').startswith('/static/nts/tee-') for p in products), "real product photos",
          "upload photos in /admin and set image/images")
    check(all(p.get('stock') for p in products), "opening stock set", "enter stock in /admin/fulfilment")
    check(bool((read_json('assets/content.json') or {}).get('sizeChart')), "size chart set", "add sizeChart in /admin content")
    check(any(u.get('id') == env_value('ADMIN_USER_ID') for u in read_json('data/users.json')), "admin account exists",
          "create the admin user, then put its id in ADMIN_USER_ID")
    unkeyed = [o for o in read_json('data/orders.json') if not o.get('customer_email') and o.get('user_id')]
    check(not unkeyed, "existing orders keyed by email", f"{len(unkeyed)} order(s): run python migrate_guest_checkout.py",
          required=False)

# Live credential checks (read-only), only when the keys are present
if env_value('RAZORPAY_KEY') and env_value('RAZORPAY_SECRET'):
    try:
        import razorpay
        razorpay.Client(auth=(env_value('RAZORPAY_KEY'), env_value('RAZORPAY_SECRET'))).order.all({'count': 1})
        check(True, "Razorpay credentials accepted")
    except Exception as e:
        check(False, "Razorpay credentials accepted", str(e)[:120])

if env_value('SHIPROCKET_EMAIL') and env_value('SHIPROCKET_PASSWORD'):
    from services.shiprocket_service import ShiprocketService
    sr = ShiprocketService()
    check(bool(sr.login()), "Shiprocket login", "check the API user's email/password in Shiprocket -> Settings -> API")
    if env_value('SHIPROCKET_PICKUP_PINCODE'):
        res = sr.check_serviceability(env_value('SHIPROCKET_PICKUP_PINCODE'), '110001', 0.5, 0) or {}
        check(bool((res.get('data') or {}).get('available_courier_companies')), "Couriers available from pickup PIN",
              "verify the pickup location in Shiprocket")

print("\n== Optional for now ==")
check(env_flag('PHONE_LOGIN_ENABLED'), "Customer phone login", "off: customers check out as guests by email", required=False)
from services.auth_providers import PROVIDERS
for p in PROVIDERS.values():
    check(p.enabled, f"{p.label} sign-in", p.disabled_reason(), required=False)

print(f"\n{len(blocking)} blocking item(s), {len(optional)} optional feature(s) off." if blocking
      else f"\nReady ({len(optional)} optional feature(s) off). Do one live ₹1 order end-to-end, then refund it.")
sys.exit(1 if blocking else 0)
