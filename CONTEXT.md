# NTS — Not That Simple: project context & remaining work

Read this first when picking the project up. Last updated 2026-10-09 (branch `pre-launch-guest-checkout`, commit `3c6bdd7`).

Related specs: [SPEC.md](SPEC.md) (storefront) · [SPEC-fulfilment.md](SPEC-fulfilment.md) (stock + delivery desk) ·
[SPEC-guest-checkout.md](SPEC-guest-checkout.md) (guest checkout, integrations, social sign-in seam).

---

## 1. Where things stand

| Area | State |
|---|---|
| Storefront (PLP, PDP S/M/L, cart drawer, checkout, policies) | Built, Yeezy-style, INR only |
| Payments | Razorpay with UPI / Google Pay first; webhook + persisted pending orders; **keys not set** |
| Guest checkout | Name + email + courier mobile, no OTP; orders/carts/customers keyed by email |
| Order status | `/track-order` by email + order number |
| Stock | Per-size on hand, reservations, oversell blocked |
| Fulfilment desk | `/admin/fulfilment`: shipment/AWB, label, pickup, tracking sync, failed delivery/RTO, returns, restock |
| Shipping | Shiprocket; **credentials not set** |
| SMS OTP | Twilio; **credentials not set** (admin sign-in needs it) |
| Social sign-in | Google **enabled locally** (keys in `.env`, top-right Log in + checkout); Meta not implemented |
| Database | Local dev MongoDB 8.0.4 at `D:\nts\mongodb` (data `D:\nts\mongodb-data`), 4 tees seeded |
| Tests | 52 unit tests, all passing (`python -m unittest discover tests`) |
| Preflight | `python preflight.py` → 19 blocking items (keys, business details, photos, stock, size chart, admin) |

### Key code map
- `app.py` — all routes. Look for: `calculate_order_total` (server-side pricing/size/stock), `resolve_buyer` / `Buyer`
  (who is ordering), `finalize_paid_order` (idempotent "mark paid", used by verify + webhook), `create_prepaid_shipment`,
  `reserved_units` / `available_units` (stock), `fulfilment_stage` + `/api/admin/fulfilment/*`, `link_social_identity`,
  `integration_status` (startup report), `BUSINESS` (policy page details from `.env`).
- `services/shiprocket_service.py`, `services/auth_providers.py`, `utils/config.py` (`env_value` treats empty /
  `REPLACE_ME` / `your_..._here` as missing), `utils/db.py` (filename → Mongo collection map).
- `templates/base.html` — NTS shell + `.legacy` CSS layer that restyles not-yet-rebuilt pages (account, wallet, admin).
- Scripts: `seed_nts.py`, `migrate_guest_checkout.py`, `preflight.py`. **Never run `migrate_to_mongo.py` on live data**
  (it empties collections whose JSON file is missing).

### Run locally
```
D:\nts\mongodb\bin\mongod.exe --dbpath D:\nts\mongodb-data --bind_ip 127.0.0.1 --logpath D:\nts\mongodb-log\mongod.log
venv\Scripts\python app.py          # http://127.0.0.1:5000
venv\Scripts\python -m unittest discover tests
venv\Scripts\python preflight.py
```
Open the site at `http://127.0.0.1:5000`, not `localhost` — the Google OAuth client only allows the `127.0.0.1`
callback, so `localhost` fails with `redirect_uri_mismatch`.
MongoDB is not a Windows service: restart it after a reboot. `D:\nts\mongodb-download` (800 MB zip) can be deleted.

---

## 2. Go-live checklist (owner)

Work top to bottom; `preflight.py` must print **Ready**.

1. **Database**: create a MongoDB Atlas cluster (Mumbai region), set `MONGO_URI`, run `seed_nts.py` once.
2. **Business details** in `.env`: `BUSINESS_LEGAL_NAME`, `BUSINESS_ADDRESS`, `BUSINESS_GSTIN`,
   `BUSINESS_JURISDICTION_CITY`, `SUPPORT_EMAIL`, `SUPPORT_PHONE`, `GRIEVANCE_OFFICER_NAME`, `GRIEVANCE_OFFICER_EMAIL`.
   Get the policy text (`templates/terms.html`, `privacy.html`, `returns.html`, `shipping.html`) reviewed;
   confirm the 7-day return window and 48-hour damaged-item promise.
3. **Razorpay**: KYC → live keys (`rzp_live_…`) → enable UPI → webhook `https://<domain>/api/payment/webhook`
   (`payment.captured`, `order.paid`) → `RAZORPAY_WEBHOOK_SECRET`.
4. **Shiprocket**: API user (Settings → API) → `SHIPROCKET_EMAIL` / `SHIPROCKET_PASSWORD`; pickup location name + PIN
   must match Shiprocket exactly.
5. **Twilio**: SID / token / sender; India SMS requires **DLT registration** (entity + sender ID + OTP template).
5a. **Google sign-in**: production redirect URI + `PUBLIC_BASE_URL` + publish consent screen (see §3.1).
6. **Admin account**: create the admin user (see §3.5), put its id in `ADMIN_USER_ID`.
7. **Catalog**: real photos (square, white background), names, prices, `sizeChart` in site content, opening stock
   in `/admin/fulfilment`.
8. **Hosting**: Linux host with HTTPS and a domain. Run **one process with threads**:
   `gunicorn -w 1 --threads 8 -b 0.0.0.0:8000 app:app` (orders/stock are whole-collection rewrites under in-process
   locks — two workers would overwrite each other). Add `gunicorn` to `requirements.txt` when deploying.
   `FLASK_DEBUG=0`. `SESSION_COOKIE_SECURE=True` is already on (needs HTTPS).
9. **Smoke test on live keys**: one ₹1 online order + one COD order → label → pickup → tracking sync → refund from
   `/admin`. Check the server log for the first Shiprocket pickup response (pickup handling is untested live).
10. **Backups**: enable Atlas backups before taking real orders.

---

## 3. Integrations still to implement (with how)

### 3.1 Google Sign-In — DONE, enabled locally
- 2026-10-09: OAuth client (Web) `102245515818-pdq6…apps.googleusercontent.com` added; `GOOGLE_CLIENT_ID` /
  `GOOGLE_CLIENT_SECRET` copied into `.env` from the downloaded `client_secret_*.json` (now in `.gitignore`).
  Verified: startup log `Google sign-in: enabled`, button on home sign-in modal and `/checkout`, `/auth/google/login`
  redirects to Google with the `127.0.0.1` callback. A real end-to-end sign-in has not been tested yet.
- Still to do for live: add `https://<domain>/auth/google/callback` to this client, set `PUBLIC_BASE_URL`, publish the
  consent screen (in Testing mode only listed test users can sign in), set the keys on the production host.
- Implemented in `GoogleProvider` (OIDC code flow; ID token from Google's token endpoint over TLS, claims `aud`/`iss`/
  `exp`/`email_verified` checked). Buttons: sign-in modal (phone form collapses to "Admin sign-in" while
  `PHONE_LOGIN_ENABLED=0`) and guest checkout. Tests: `tests/test_google_signin.py`.
- To enable: Google Cloud Console → OAuth consent screen (External, scopes openid/email/profile; publish it or add
  test users) → OAuth client (Web) with redirect URIs `http://127.0.0.1:5000/auth/google/callback` and
  `https://<domain>/auth/google/callback` → set `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, and in production
  `PUBLIC_BASE_URL`.
- Admin via Google: works once the admin account has the same Gmail address (account linking is by verified email).

### 3.2 Meta (Facebook/Instagram) login (`MetaProvider`)
- Meta for Developers app → Facebook Login → redirect `https://<domain>/auth/meta/callback`; App Review for `email`.
- Dialog `https://www.facebook.com/v19.0/dialog/oauth`, exchange at `graph.facebook.com/v19.0/oauth/access_token`,
  profile `graph.facebook.com/me?fields=id,name,email`.
- **Reject accounts with no email** (email is the customer key). Meta emails are verified, so `email_verified=True`.

### 3.3 Transactional email (not built)
Needed for: order confirmation, shipped/delivered updates, return/refund notices, and `REQUIRE_EMAIL_CONFIRMATION`.
- Pick a provider (Amazon SES Mumbai, SendGrid, Postmark, Zoho ZeptoMail). Add `utils/mailer.py` with one
  `send_email(to, subject, html)`; disabled with a startup log line when keys are missing (same pattern as Twilio).
- Hook points: end of `finalize_paid_order` and `create_cod_order` (confirmation), `admin_fulfilment_action`
  pickup (shipped), `admin_fulfilment_sync` (delivered), refund helpers.
- Email confirmation: signed token (`itsdangerous`, already a Flask dependency) → `/confirm-email/<token>` sets
  `customers.email_verified=True`; then `REQUIRE_EMAIL_CONFIRMATION=1` becomes usable.
- Add SPF/DKIM/DMARC for the sending domain.

### 3.4 Razorpay extras (optional)
- Google Pay is covered by the UPI block. Razorpay **Magic Checkout** / saved addresses could replace the address
  form later.
- Hold stock at `create_order` (see shortcut in §6) if last-unit races appear.

### 3.5 Admin bootstrap (small, needed)
Phone sign-up is off (`PHONE_LOGIN_ENABLED=0`), so there is no UI to create the admin. Add `create_admin.py`:
prompt for name + phone + password (getpass), write a user with `is_admin: True` (bcrypt hash, encrypted phone,
`phone_hash`), print the id for `ADMIN_USER_ID`. Sign-in still needs an OTP (Twilio, or `FLASK_DEBUG=1` locally).

### 3.6 Shiprocket follow-ups
- NDR re-attempt requests (API `/ndr/{awb}/action`) from the fulfilment desk.
- COD shipment retry (today only prepaid can be re-created from the desk).
- Multiple pickup locations / warehouses (out of scope for 4 SKUs).
- Shiprocket webhooks for tracking instead of the manual "Sync tracking" button.

---

## 4. Regression testing (manual, before each release)

Run on a staging copy with Razorpay **test** keys and Shiprocket staging/test account.

| # | Flow | Expect |
|---|---|---|
| 1 | PLP `/` desktop + 390px | 4 tees, square grid, code label on hover / always on touch, sold-out label when all sizes 0 |
| 2 | PDP | ADD TO CART disabled until size chosen; sold-out sizes struck through; price ₹ incl. taxes |
| 3 | Cart drawer | add twice → qty 2, −/+ capped at 10, remove, subtotal |
| 4 | Guest checkout validation | bad email / empty name / 9-digit phone each blocked with a message |
| 5 | Unserviceable PIN | blocked before payment |
| 6 | Online payment (UPI test) | Razorpay opens with UPI first → success → confirmation page → order in `/admin/fulfilment` To ship |
| 7 | Close tab after paying | order still appears (webhook) |
| 8 | Payment failure | no order, retry possible |
| 9 | COD order | confirmation page opens (internal id), cart emptied, stock reserved |
| 10 | Coupon | valid / expired / over-limit (order kept, flagged `coupon over limit`) |
| 11 | Stock | last unit bought → size disabled on PDP, checkout of that size rejected |
| 12 | Fulfilment | create shipment → label PDF → schedule pickup → stock on hand drops once |
| 13 | Tracking sync | delivered → Delivered tab; undelivered → red flag; RTO delivered → Returns + Restock |
| 14 | Order status | `/track-order` with email + number; wrong email → not found |
| 15 | Returns | inside window OK, after `RETURN_WINDOW_DAYS` rejected |
| 16 | Refunds | prepaid → Razorpay refund; guest COD → `manual_required` with amount + email |
| 17 | Admin access | non-admin → redirected; `/admin` unauthenticated opens sign-in |
| 18 | Integrations off | empty Razorpay → "Online payments not yet enabled", COD works; empty Twilio → OTP 503 |
| 19 | Policy pages | no highlighted gaps once `.env` business details are set |
| 20 | Startup log | `[STARTUP] Integrations:` lists only intended-off items |

---

## 5. Automated testing: what exists and what to add

### Existing (stdlib `unittest`, Mongo and Shiprocket mocked; 42 tests)
- `test_order_total.py` — pricing, sizes, stock, quantity, shipping floor.
- `test_payment_finalize.py` — pending orders, webhook signature, idempotency, coupon over limit.
- `test_fulfilment.py` — stages, admin-only, pickup deducts once, restock once, tracking/RTO, stock editor, label.
- `test_guest_checkout.py` — guest COD keyed by email, validation, flags, cart, order status, social linking, migration.
- `test_return_window.py` — return window.

### To add
1. **CI** (GitHub Actions): on push/PR → `pip install -r requirements.txt` → `python -m unittest discover tests`.
   Add a `mongo:8` service container for integration tests.
2. **Integration tests against real MongoDB** (no `read_json` mocks): concurrent COD orders on the last unit,
   webhook + verify racing, migration on a copy of production data.
3. **End-to-end browser tests** (Playwright, new dev dependency — ask first): PLP → PDP → cart → guest checkout →
   COD confirmation; Razorpay test-mode UPI success (`success@razorpay`) and failure; `/track-order`; admin fulfilment
   with Shiprocket mocked at the HTTP layer (e.g. `responses`).
4. **Contract tests** for Shiprocket/Razorpay response shapes using recorded fixtures (pickup and label replies are
   currently assumed — record the first real responses).
5. **Smoke check after deploy**: `preflight.py` + HTTP 200 on `/`, `/product/1`, `/checkout`, `/track-order`.
6. **Accessibility/visual**: axe-core run on PLP/PDP/checkout; screenshot diff at 1440px and 390px.

---

## 6. Known risks and shortcuts

- `app.py:1036` — two shoppers paying online for the last unit at the same moment can both succeed (online orders
  reserve stock only once paid). Fix: hold stock at `create_order` with a short expiry.
- `app.py:1129` — below ₹1,000 the client-quoted Shiprocket rate is trusted (floored at 0). All tees are above ₹1,000
  today; re-quote server-side if prices drop.
- Single process only (whole-collection rewrites). Move orders/products to per-document updates before scaling out.
- Flask-Limiter uses in-memory storage (per process; resets on restart). Use Redis when scaling.
- Guests cannot cancel/return online (policy says email support); signed-in customers can.
- Guest COD refunds are manual (no wallet for guests).
- Old SIXN templates (account, wallet, admin) are restyled by the `.legacy` CSS layer, not rebuilt.
- `app.py` is ~3,800 lines; split into blueprints (storefront / payments / fulfilment / auth) when it next grows.
- Admin sign-in needs Twilio, or Google once the admin account's email is the admin's Gmail.
- Order records (incl. guest orders) store delivery address + phone in plain text; only account-saved phone/addresses are
  Fernet-encrypted. Consider encrypting `orders.address` / `customer_phone` too (decrypt in admin + Shiprocket payloads).

---

## 7. Decisions on record
- Guest checkout by email; phone login off by default (`PHONE_LOGIN_ENABLED`), admins can always sign in.
- Courier mobile number is collected at checkout (Shiprocket requires it) — not a login.
- Google Pay = Razorpay UPI; no separate key.
- INR only; India-only shipping; free shipping ≥ ₹1,000.
- Stock leaves on-hand at courier pickup, not at order time.
