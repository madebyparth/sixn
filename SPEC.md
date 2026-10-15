# Spec: NTS — Not That Simple (storefront v1)

## Objective
Re-skin the existing SIXN Flask storefront into **NTS — Not That Simple**, a minimal
storefront modelled on the yeezy.com shopping flow, ready to take real orders in India.

Reference flow (analysed from yeezy.com, Oct 2026):

| Step | Reference behaviour | NTS v1 |
|---|---|---|
| PLP | Home page *is* the shop. White page, monospace type, centred grid of square product images, no prices on tiles, style code (e.g. `TS-03`) appears on hover. Header: menu (left), filter (centre), cart (right). Footer: one row of small links. | Same. 4 tiles. Code label = product `code` (e.g. `NTS-01`). No filter (only one category). |
| PDP | Tile opens product view: large images, colour swatches, size buttons, sold-out state, price, add to cart. | `/product/<id>`: image gallery, name, price (INR), S/M/L buttons (unavailable sizes disabled), description, ADD TO CART. |
| Cart | Slide-in drawer, qty − / +, remove, subtotal, checkout. | Same drawer (existing cart logic kept, restyled). |
| Checkout | Single page: contact, address, shipping, payment, place order. Express wallets (Google Pay). | Existing single-page checkout restyled. Payment via **Razorpay Checkout** with UPI (Google Pay / PhonePe / Paytm) shown first, then cards / netbanking. COD + wallet + coupon kept. |
| Verify / confirm | "Verifying payment" → order confirmation with order number. | Existing `/api/payment/verify` → `/order-confirmation/<id>`. |
| Order status | Lookup page. | Existing `/track-order` restyled via theme layer. |

Users: shoppers in India on mobile first. Success = a shopper can go PLP → PDP → size → cart → pay via
GPay/UPI → see confirmation, and the order lands in MongoDB + Shiprocket.

Out of scope (next phase, separate spec): supply-chain / delivery management, new admin pages.
The existing `/admin` keeps working untouched.

## Tech Stack
Unchanged: Flask, Flask-Login, Flask-WTF (CSRF), Flask-Limiter, MongoDB (pymongo), Razorpay, Twilio OTP,
Shiprocket. Frontend: Jinja templates + Tailwind CDN + vanilla JS. Font: IBM Plex Mono (Google Fonts).
No new dependencies.

## Commands
```
Setup:  python -m venv venv && venv\Scripts\pip install -r requirements.txt
Seed:   venv\Scripts\python seed_nts.py          # writes the 4 tees into Mongo `products`
Dev:    venv\Scripts\python app.py
Test:   venv\Scripts\python -m unittest discover tests
```

## Project Structure
```
app.py                 → routes + order logic (size validation added to calculate_order_total)
seed_nts.py            → idempotent seed of the 4 NTS tees
templates/base.html    → new minimal shell: header, cart drawer, auth modal, footer, light theme layer
templates/index.html   → PLP grid
templates/product.html → PDP
templates/checkout.html, order_success.html → restyled, same JS/API contract
tests/                 → unittest for order-total/size validation
```

## Product data (Mongo `products`, editable from /admin → products JSON + image upload)
```json
{ "id": 1, "code": "NTS-01", "name": "NTS TEE 01", "price": 1499, "category": "tees",
  "image": "/static/uploads/products/<file>", "images": ["..."],
  "availableSizes": ["S", "M", "L"], "soldOutSizes": [], "inStock": true,
  "description": "..." }
```
Images are changed by uploading in /admin and editing `image` / `images` — no code change.

## Code Style
Match existing: vanilla JS functions on `window`, Tailwind utility classes, Jinja blocks.
Design tokens: `#fff` background, `#000` text, 1px black borders, no radius, uppercase mono, 12–14px.

## Testing Strategy
- unittest (stdlib, no new dependency) for `calculate_order_total`: rejects unknown size, sold-out size, out-of-stock product; computes INR total.
- Manual flow check in browser with Razorpay **test** keys: PLP → PDP → cart → checkout → UPI test payment → confirmation.

## Boundaries
- Always: server recomputes price and validates size; keep CSRF; INR only.
- Ask first: removing COD/wallet/coupons, changing order schema, touching admin, adding deps.
- Never: commit `.env`/keys, trust client prices, break existing order/refund APIs.

## Success Criteria
1. `/` shows exactly the products in Mongo (4 tees) as a square-image grid, code on hover, mobile 2 cols / desktop 4 cols.
2. `/product/<id>` lets the user pick S/M/L; ADD TO CART disabled until a size is chosen; sold-out sizes disabled.
3. Cart drawer shows items, qty, remove, INR subtotal; checkout requires OTP login (existing).
4. Checkout opens Razorpay with UPI listed first; on success the order is saved and confirmation shows.
5. Server rejects an order with a size not in `availableSizes` or in `soldOutSizes` (400).
6. All prices shown in ₹ (no currency switching, no ipapi call).
7. Brand reads "NTS" / "NOT THAT SIMPLE" across customer pages.

## Launch checklist (India, live)
Code side (done):
- [x] Paid orders survive a closed tab: pending orders are stored in Mongo `pending_orders`; `/api/payment/webhook`
      (signature-verified, CSRF-exempt) and `/api/payment/verify` both call the idempotent `finalize_paid_order`.
- [x] A coupon over its limit no longer drops a captured payment; the order is saved with `coupon_over_limit: true`.
- [x] Prices validated server-side (size, stock, qty 1–10, INR, shipping ≥ 0).
- [x] Policy pages rewritten for NTS / India: terms, privacy (DPDP Act 2023), returns-cancellations-refunds, shipping
      (new `/shipping`), FAQ, contact, size guide. Facts that only the owner knows come from `.env` and render as
      highlighted gaps until set. Grievance officer section included (Consumer Protection (E-Commerce) Rules 2020).
- [x] Return window enforced in code (`RETURN_WINDOW_DAYS`, default 7, from `delivered_at`), same value shown on /returns.
- [x] Fake contact form removed (it showed "Message sent" without sending); SIXN-era /about and /craftsmanship redirect to /.
- [x] Flask debug off unless `FLASK_DEBUG=1`.
- [x] Fixed a deadlock: return-pickup sync held `order_lock` and the refund helpers re-acquired it (now an RLock).

Owner side (needs your accounts / information) — `python preflight.py` checks all of these and exits 1 until they pass:
- [ ] Fill `.env`: `BUSINESS_LEGAL_NAME`, `BUSINESS_ADDRESS`, `BUSINESS_GSTIN`, `BUSINESS_JURISDICTION_CITY`, `SUPPORT_EMAIL`,
      `SUPPORT_PHONE`, `GRIEVANCE_OFFICER_NAME`, `GRIEVANCE_OFFICER_EMAIL` (see `.env.example`). App logs `[CONFIG]` for each missing one.
- [ ] Razorpay: complete KYC, switch to live keys, enable UPI, add webhook → `https://<domain>/api/payment/webhook`
      with events `payment.captured`, `order.paid`; put its secret in `RAZORPAY_WEBHOOK_SECRET`.
- [ ] Real product photos, names, prices (upload in /admin, edit products JSON) and `sizeChart` in site content.
- [ ] MongoDB (Atlas or self-hosted), Twilio, Shiprocket credentials; run `python seed_nts.py` once.
- [ ] Do one real ₹1 test order end-to-end on live keys, then refund it from /admin.
- [ ] Host behind HTTPS with a production WSGI server, **one process with threads**, e.g.
      `gunicorn -w 1 --threads 8 -b 0.0.0.0:8000 app:app` on Linux (not in requirements yet — "ask first" dependency).
      Must be a single process: orders/stock are rewritten as whole collections under in-process locks, so two workers
      would overwrite each other's orders.
- [ ] Set opening stock per size in /admin/fulfilment.

## Status (2026-10-09)
Done: NTS shell (header/menu/cart drawer/auth modal/footer), PLP, PDP, INR-only pricing, Razorpay UPI-first + NTS theme,
server-side validation, 4-tee catalog + `seed_nts.py`, placeholder tee images (`static/nts/`), legacy light-theme layer
for account/checkout pages, payment webhook + persisted pending orders, policy pages, return-window enforcement.
Verified: 15 unit tests (`python -m unittest discover tests`) incl. webhook signature/idempotency; every customer route
renders (200/302) with an in-memory store; screenshots at desktop and 390px.
Not verified: a live Razorpay payment, OTP SMS, Shiprocket calls (need real credentials + MongoDB).

## Open Questions
1. Prices for the 4 tees (seeded at ₹1,499 placeholder) and their names/codes.
2. ~~Guest checkout~~ — decided: guest checkout by email, phone login off by default. See `SPEC-guest-checkout.md`.
3. Policy copy is a sound default for an Indian D2C apparel store, not legal advice — have it reviewed. In particular the
   "damaged/wrong item within 48 hours" promise and 7-day return window are business choices.

## Phase 2
Inventory + fulfilment desk: see `SPEC-fulfilment.md` (implemented).
