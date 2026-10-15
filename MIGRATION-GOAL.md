# Goal: move NTS from Flask to Spring Boot + React

Paste this into a new session to start (or point the session at this file).

---

## Goal

Rebuild the NTS storefront backend in **Spring Boot** and the frontend in **React**, keeping every behaviour the
current Flask app has (see `SPEC.md`, `SPEC-fulfilment.md`, `SPEC-guest-checkout.md`, `CONTEXT.md`), on the **same
MongoDB data** so we can switch over without a data migration. The new backend must be safe to run as **several
instances** (the Flask app can only run as one process).

## Stack

| Layer | Choice |
|---|---|
| Backend | Java 21, Spring Boot 3.x, Maven |
| Data | Spring Data MongoDB, same collections and document shapes as today |
| Auth | Spring Security: session cookie + CSRF, Google sign-in via `spring-boot-starter-oauth2-client`, admin role |
| Payments | Razorpay Java SDK, webhook with signature check |
| Shipping | Shiprocket REST client (`RestClient`), token cached in Mongo |
| SMS | Twilio Java SDK (admin sign-in only) |
| Frontend | React + TypeScript on **Next.js** (server-rendered storefront so search engines, link previews and AI shopping agents can read products without running JS); admin can be client-side |
| Tests | JUnit 5, Testcontainers (real MongoDB), WireMock for Razorpay/Shiprocket |
| Ops | Docker image, GitHub Actions CI, Sentry, structured JSON logs |

## Layout

```
backend/    Spring Boot app (new)
frontend/   React app (new)
app.py ...  Flask app stays as the reference until cutover, then is deleted
```

## Rules

1. **Behaviour comes from the Flask code and specs, not from guesses.** When the spec and code disagree, ask.
2. **Port the tests first.** Each slice starts by porting the matching `tests/test_*.py` cases to JUnit; the slice
   is done when they pass against Testcontainers MongoDB.
3. **Same data.** Same collection names and field names. Read existing documents without a migration script.
   If a field must change, write it down and add a one-off migration.
4. **Per-document writes only.** No "load whole collection, rewrite whole collection". Stock changes use atomic
   `$inc` with a guard (`findAndModify` where `available >= qty`). This fixes the last-unit race.
5. **Same API paths and JSON** as Flask where an endpoint already exists, so slices can be tested side by side.
6. **Secrets only from environment variables.** Nothing in `application.yml` except placeholders.
7. Encrypt order address and phone at rest (Flask stores them in plain text).
8. Small slices, one commit each, CI green before the next.
9. **Deploy happens only on the new stack** (owner decision). The Flask app is never deployed.
10. **Razorpay goes live on the new stack:** Razorpay validates the running website before sharing integration docs
    and live keys, so the first deploy (Phase 4) must look like a real store. Keep the domain and payment paths
    (`/api/payment/*`, webhook `/api/payment/webhook`) stable from then on so the approval never needs repeating.
11. **Agent-ready by default:** every product page server-renders schema.org `Product`/`Offer` JSON-LD (INR price,
    per-size availability, `MerchantReturnPolicy`, shipping), per-page meta + Open Graph; site serves `robots.txt`
    (allow OAI-SearchBot, ChatGPT-User, Google-Extended, ClaudeBot, PerplexityBot), `sitemap.xml`, `llms.txt`;
    forms use real buttons/labels/radio inputs so agents can operate them.
12. **Minimal product-to-paid path** (owner requirement). Fewest screens, fields and clicks:
    - **PDP:** image(s), name, price (₹ incl. taxes), size S/M/L as radio buttons (sold-out struck through),
      one primary button. Size guide and details collapsed. Nothing else above the fold.
    - **Primary button = "BUY NOW"** goes straight to checkout with that item; cart drawer stays only for adding
      more than one item.
    - **Checkout = one page, no sign-up:** email, full name, mobile, PIN, address line, then one PAY button that
      opens Razorpay with UPI first. City/state auto-filled from PIN; serviceability checked as soon as PIN is typed.
      Coupon behind a small "Have a code?" link. Google sign-in optional, never required. No wallet.
    - Remember the guest's details on the device for the next order; show the order summary in a single line
      that expands.
    - Target: product page → paid in **2 screens, 5 fields, 3 clicks** (size, BUY NOW, PAY) plus UPI approval.
13. **Apple-class motion** (owner requirement): every interaction answers instantly and moves smoothly.
    - **Library:** `motion` (Framer Motion) for components + the browser View Transitions API for page changes.
      One shared file of motion tokens (durations, springs, easings); no ad-hoc timings in components.
    - **Feel:** spring physics, not linear. Defaults: tap feedback 100 ms, small UI 200 ms, panels/drawers 350 ms
      spring (stiffness ~300, damping ~30), page transitions ≤ 400 ms. Ease-out on enter, faster ease-in on exit.
    - **Signature moments:**
      - Grid → PDP: the product image morphs from its tile into the PDP hero (shared-element transition).
      - Size select: the selection pill slides between S/M/L; sold-out sizes don't react.
      - BUY NOW / ADD: button presses (scale 0.97), the item flies to the bag icon, the count bumps.
      - Cart drawer: slides with a spring, background dims and blurs, drag/swipe to close on mobile.
      - Checkout: fields lift on focus, errors shake once gently, the PIN check resolves inline with a tick,
        PAY morphs into a progress state then a check mark on success.
      - Order success: one calm reveal, no confetti.
    - **Never feel slow:** prefetch pages on hover/viewport (Next.js `<Link>`), optimistic UI for cart actions,
      skeletons instead of spinners, images with blur placeholders, no layout shift.
    - **Performance:** animate only `transform` and `opacity`; 60 fps on a mid-range Android phone; Core Web Vitals
      LCP < 2.5 s, INP < 200 ms, CLS < 0.1 on 4G. Motion must never delay an action (tap → navigation starts at once).
    - **Restraint:** motion explains where things came from and went; nothing loops or bounces for decoration.
    - **Accessibility:** honour `prefers-reduced-motion` (cross-fade only), keep focus states visible, motion never
      hides content from screen readers or agents.
    - **Done when:** each signature moment is recorded on desktop + 390 px phone and reviewed by the owner.

## Phases

| # | Slice | Done when |
|---|---|---|
| 0 | **API contract**: list every Flask route (`grep @app.route app.py`, 73 routes) → method, path, request, response, auth; list every collection + document shape. Write `backend/API.md`. | Contract reviewed and approved by the owner |
| 1 | **Skeleton**: Spring Boot app, Mongo config, Security (CSRF, session, admin role), `env`-based config with "missing = disabled + startup log" (port `utils/config.py` + `integration_status`), health endpoint, CI, Dockerfile; Next.js app with NTS look (`templates/base.html` tokens) | Both apps boot locally, CI green |
| 2 | **Catalog + stock** (API + storefront pages): products, sizes, `reserved_units` / `available_units`, content; PLP, PDP (S/M/L), cart drawer, policy pages (Terms, Privacy, Refund/Cancellation, Shipping, Contact with `BUSINESS_*` details), size guide; rule 11 (JSON-LD, sitemap, robots, llms.txt) | Ported inventory tests pass; pages render with JS disabled |
| 3 | **Cart + guest checkout**: buyer resolution (`resolve_buyer`), carts by email, `calculate_order_total` (server-side price, shipping floor, coupons), PIN serviceability; checkout page | Ported `test_order_total`, `test_guest_checkout` pass |
| 4 | **Payments + first deploy (Razorpay review)**: Razorpay order create, pending orders, `/verify`, webhook, idempotent `finalize_paid_order` (test keys); order success + track order pages. Deploy to the real domain with HTTPS, Atlas (Mumbai, backups), Sentry. COD off until Phase 5. Owner submits the URL to Razorpay | Ported `test_payment_finalize` pass; concurrent last-unit test passes; test-mode UPI payment works on the live domain |
| 5 | **Fulfilment desk + COD**: stages, AWB, label, pickup (stock deducted once), tracking sync, RTO, restock, returns window, refunds; COD order; admin UI | Ported `test_fulfilment`, `test_return_window` pass |
| 6 | **Auth**: Google sign-in + account linking by verified email, admin OTP via Twilio, `create-admin` CLI command | Ported `test_google_signin` pass |
| 7 | **Transactional email** (new): order confirmation, shipped, delivered, refund | Emails sent in a test with a fake mail server |
| 8 | **Agent interface** (new): public read-only catalog API + MCP server (`search_products`, `check_size_stock`, `check_pin_serviceability`, `get_policies`, `track_order`); Google Merchant Center feed | An MCP client can find a tee, check a size and a PIN |
| 9 | **Go live**: Razorpay live keys + webhook per their docs, load test (k6, a "drop" of 300 concurrent checkouts), live ₹1 + COD smoke test to a real pickup, delete Flask | All of `CONTEXT.md` §2 go-live checklist done |

## Out of scope (for now)

Wallet, ratings, notifications and other legacy SIXN features unless the owner asks; Meta login; multiple warehouses.
Confirm this list in Phase 0.

## Start

Begin with **Phase 0 only**: read `app.py`, `utils/db.py`, `services/`, the three SPEC files and the tests, and
produce `backend/API.md` (routes + collections). Stop and ask for review before writing any Java.
