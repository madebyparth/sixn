# Spec: guest checkout, integration placeholders, social sign-in seam (branch `pre-launch-guest-checkout`)

## Objective
Let shoppers buy without an account (name + email + a mobile number for the courier, no OTP), start cleanly with
integration keys still empty, and prepare Google / Meta sign-in so they can be added later without touching checkout.

## Decisions
- **Customer key = email** (lower-cased, validated). `customers` collection `{id: email, name, user_id?, email_verified}`;
  `carts` collection `{id: email, items, user_id?}`; orders store `customer_email`, `customer_name`, `customer_phone`
  and an optional `user_id`.
- **Mobile number is still collected** at checkout: couriers (Shiprocket) require a delivery phone. It is not a login.
- **`Buyer`** (app.py) is the single place checkout learns who is ordering: signed-in user or guest. Coupon and COD
  rate limits use `Buyer.key` (`user_id` or `email:<address>`). Wallet credit is only for signed-in customers.
- **Order access for guests**: the browser session that placed the order (`session['customer_email']`) can open its
  confirmation; later, `/track-order` with email + order number.
- **Guest refunds**: online → Razorpay to source (unchanged). COD → marked `cod_refund_status: manual_required` with the
  amount and email for the admin to pay by UPI/bank (guests have no wallet).
- **Phone login** stays in the code behind `PHONE_LOGIN_ENABLED` (default off). Admin accounts can always sign in
  (password + OTP), so Twilio is still needed for the admin.
- **OTP without Twilio is off** (`send_otp_sms` returns False, endpoints answer 503). OTPs are no longer printed to
  logs except with `FLASK_DEBUG=1`.
- **Integrations** read through `utils.config.env_value`, which treats empty / `REPLACE_ME` / `your_..._here` as missing.
  `log_integration_status()` prints what is disabled at startup; preflight separates required vs optional.
- **Google Pay** has no key: it is paid through Razorpay's UPI option.
- **Social sign-in**: `services/auth_providers.py` (`AuthProvider`, `GoogleProvider`, `MetaProvider`). A provider is
  enabled only when its keys are set *and* `implemented = True`. `/auth/<provider>/login|callback` routes exist;
  `link_social_identity()` finds/creates the user by provider id or verified email, marks the customer verified and
  attaches guest orders with that email (`user_id` filled in), so order history carries over.
- **`REQUIRE_EMAIL_CONFIRMATION`** flag exists and is enforced for guests, but no confirmation email is sent yet —
  leave it off (preflight fails if it is on).

## Migration
`python migrate_guest_checkout.py [--dry-run]`: idempotent; sets `customer_email` (+ name, phone) on existing orders
from the account's email, creates `customers` records. Orders from phone-only accounts without an email are left as
they are (still reachable through `user_id`). Nothing is deleted.

## Verified
`tests/test_guest_checkout.py` (13 tests): guest COD order keyed by email, confirmation only for the session that
placed it, invalid email/name/phone rejected, email-confirmation flag, online payments disabled without Razorpay,
checkout page open to guests, cart saved and cleared, order status by email + number, social sign-in links guest orders
(no duplicate users), unverified social email rejected, providers disabled, phone sign-up off, OTP off without Twilio,
migration keeps every order.
