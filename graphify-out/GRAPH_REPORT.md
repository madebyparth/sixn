# Graph Report - sixn  (2026-10-16)

## Corpus Check
- 47 files · ~412,417 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 560 nodes · 1260 edges · 44 communities (20 shown, 24 thin omitted)
- Extraction: 91% EXTRACTED · 9% INFERRED · 0% AMBIGUOUS · INFERRED: 111 edges (avg confidence: 0.88)
- Token cost: 262,907 input · 0 output

## Community Hubs (Navigation)
- App Routes (app.py core)
- Startup Config & Integrations
- Orders, Payments & Tracking Concepts
- Pricing & Stock Availability
- Policy Pages & Legal
- Order Finalize & Shipment
- Shiprocket Service
- Guest Checkout Tests
- Fulfilment Tests
- Order Total & Inventory Tests
- Google Sign-in Tests
- Buyer Identity
- Old SIXN Homepage Screenshot
- Architecture Overview (legacy)
- Cart, Tracking & OAuth Callback
- Payment Finalize Tests
- Social Sign-in Seam
- Checkout Page JS
- Account Settings UI
- README & Launch Readiness
- NTS Tee Illustrations
- Notification Helpers
- Return Window Tests
- Red Logo Brand
- Transparent Logo Brand
- Community 25
- Community 26
- Community 27
- Community 28
- Community 29
- Community 36

## God Nodes (most connected - your core abstractions)
1. `read_json()` - 71 edges
2. `write_json()` - 55 edges
3. `NTS project context & remaining work` - 22 edges
4. `admin_required()` - 17 edges
5. `ShiprocketService` - 17 edges
6. `env_value()` - 17 edges
7. `finalize_paid_order()` - 16 edges
8. `GuestCheckoutTest` - 16 edges
9. `admin_fulfilment_action()` - 15 edges
10. `create_cod_order()` - 15 edges

## Surprising Connections (you probably didn't know these)
- `val() gap macro` --semantically_similar_to--> `env_value()`  [INFERRED] [semantically similar]
  templates/_macros.html → utils/config.py
- `placeCODOrderWithWallet()` --references--> `create_cod_order()`  [INFERRED]
  templates/checkout.html → app.py
- `submitPhoneOTP()` --references--> `verify_phone_update_otp()`  [INFERRED]
  templates/settings.html → app.py
- `cropAndUpload()` --references--> `upload_pfp()`  [INFERRED]
  templates/settings.html → app.py
- `my_wallet()` --references--> `Wallet Page`  [INFERRED]
  app.py → templates/wallet.html

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **Homepage section flow (hero -> trust -> categories -> products -> newsletter)** — assets_web_media_website_hero_carousel, assets_web_media_website_value_props, assets_web_media_website_shop_by_category, assets_web_media_website_new_arrivals, assets_web_media_website_newsletter [INFERRED 0.85]
- **Guest checkout order flow** — templates_checkout_initiatepayment, app_resolve_buyer, spec_guest_checkout_email_customer_key, app_calculate_order_total, app_finalize_paid_order, app_create_cod_order [INFERRED 0.85]
- **Stock reservation and deduction lifecycle** — app_reserved_units, app_available_units, spec_fulfilment_stock_deduct_at_pickup, spec_fulfilment_admin_fulfilment_desk, app_calculate_order_total [INFERRED 0.85]
- **Social sign-in seam** — services_auth_providers_authprovider, services_auth_providers_googleprovider, services_auth_providers_metaprovider, app_link_social_identity, templates__macros_google_button [INFERRED 0.85]
- **Fulfilment desk admin actions via post()** — templates_fulfilment_post, templates_fulfilment_act, templates_fulfilment_label, templates_fulfilment_setstock, app_admin_fulfilment_action, app_admin_set_stock [INFERRED 0.95]
- **Policy pages on _policy.html with val() business details** — templates_faq_page, templates_privacy_page, templates_returns_page, templates_shipping_page, templates_terms_page, templates_size_guide_page, templates__macros_val [EXTRACTED 1.00]
- **Order tracking across guest and account views** — templates_track_order_page, templates_orders_page, templates_order_success_page, concept_order_status_lifecycle, concept_awb_tracking [INFERRED 0.85]
- **NTS Blank Tee Colourway Set (placeholder product imagery)** — static_nts_tee_01_image, static_nts_tee_02_image, static_nts_tee_03_image, static_nts_tee_04_image [INFERRED 0.95]

## Communities (44 total, 24 thin omitted)

### Community 0 - "App Routes (app.py core)"
Cohesion: 0.05
Nodes (85): about(), add_address(), add_coupon(), add_wallet_balance(), _adjust_stock(), admin_fulfilment(), admin_fulfilment_sync(), admin_panel() (+77 more)

### Community 1 - "Startup Config & Integrations"
Cohesion: 0.05
Nodes (17): b64encode_filter(), integration_status(), missing(), log_integration_status(), migrate(), AuthProvider, GoogleProvider, _jwt_claims() (+9 more)

### Community 2 - "Orders, Payments & Tracking Concepts"
Cohesion: 0.06
Nodes (45): Courier AWB / Shipment Tracking, Cash on Delivery (cod_pending), Order Status Lifecycle (paid/shipped/delivered/return/refund/rto), Razorpay Order ID as order number, Automated Cancellations & Refunds, Razorpay Payment Gateway, confirmDelete(), renderAddresses() (+37 more)

### Community 3 - "Pricing & Stock Availability"
Cohesion: 0.08
Nodes (31): available_units(), calculate_order_total(), fulfilment_stage(), index(), product(), reserved_units(), size_availability(), Admin bootstrap (create_admin.py) (+23 more)

### Community 4 - "Policy Pages & Legal"
Cohesion: 0.09
Nodes (29): BUSINESS policy details, privacy(), Business Legal Details (business.* config), India Digital Personal Data Protection Act, 2023, Consumer Protection (E-Commerce) Rules, 2020, Grievance Officer, India policy pages (DPDP Act, E-Commerce Rules), NTS design tokens (+21 more)

### Community 5 - "Order Finalize & Shipment"
Cohesion: 0.10
Nodes (13): admin_fulfilment_action(), _assign_awb(), clear_saved_cart(), create_cod_order(), create_prepaid_shipment(), finalize_paid_order(), load_user(), order_contact() (+5 more)

### Community 7 - "Guest Checkout Tests"
Cohesion: 0.16
Nodes (3): Identity, GuestCheckoutTest, MigrationTest

### Community 9 - "Order Total & Inventory Tests"
Cohesion: 0.23
Nodes (3): InventoryTest, OrderTotalTest, total()

### Community 11 - "Buyer Identity"
Cohesion: 0.15
Nodes (5): Buyer, resolve_buyer(), CSRF Fetch Interceptor, initiatePayment(), placeCODOrderWithWallet()

### Community 12 - "Old SIXN Homepage Screenshot"
Cohesion: 0.24
Nodes (12): Announcement Bar, Dark Theme with Red Accent Design, Site Footer, Hero Product Carousel, Navigation Header, New Arrivals Product Grid, Newsletter Signup CTA, Oversized Trackpants (Nike, Rs 799) (+4 more)

### Community 13 - "Architecture Overview (legacy)"
Cohesion: 0.21
Nodes (10): Admin Control Center, .env Environment Configuration, Flask Application Server, migrate_to_mongo.py Seeding Script, MongoDB Database, Shiprocket Shipping Integration, SIXN Premium E-Commerce Storefront, Twilio OTP Authentication (+2 more)

### Community 14 - "Cart, Tracking & OAuth Callback"
Cohesion: 0.22
Nodes (6): _auth_failed(), normalize_email(), _oauth_redirect_uri(), save_cart(), social_callback(), track_order_api()

### Community 16 - "Social Sign-in Seam"
Cohesion: 0.32
Nodes (7): link_social_identity(), REQUIRE_EMAIL_CONFIRMATION flag, migrate_guest_checkout.py migration, PHONE_LOGIN_ENABLED flag, Social sign-in seam, Spec: guest checkout & social sign-in seam, Auth modal (Google + phone OTP)

### Community 17 - "Checkout Page JS"
Cohesion: 0.25
Nodes (4): applyCoupon(), checkServiceability(), handleAddressChangeForServiceability(), renderSummary()

### Community 18 - "Account Settings UI"
Cohesion: 0.29
Nodes (5): cropAndUpload(), handleDetailsUpdate(), openOtpModal(), Account Settings Page, submitPhoneOTP()

### Community 19 - "README & Launch Readiness"
Cohesion: 0.33
Nodes (6): preflight.py launch readiness check, migrate_to_mongo.py empties collections warning, SIXN README, System architecture (Flask, MongoDB, Twilio, Razorpay, Shiprocket), Custom Store Wallet, Guest refunds (COD manual_required)

### Community 20 - "NTS Tee Illustrations"
Cohesion: 0.53
Nodes (6): Muted Earth-Tone Colourway Palette (black, cream, olive, sand), Tee 01 - Black Blank Tee Illustration, Tee 02 - Off-White Blank Tee Illustration, Tee 03 - Olive Grey Blank Tee Illustration, Tee 04 - Sand Beige Blank Tee Illustration, Shared Minimal Tee Silhouette (dark #101211 bg, 4:5, no graphics)

### Community 21 - "Notification Helpers"
Cohesion: 0.40
Nodes (3): get_notifications(), inject_feature_flags(), inject_notifications_helper()

### Community 23 - "Red Logo Brand"
Cohesion: 0.67
Nodes (4): SIXN Logo (Red Variant), Red/White/Black Brand Palette, Roman Numeral IX Motif, SIXN Brand

### Community 24 - "Transparent Logo Brand"
Cohesion: 1.00
Nodes (3): SIXN Logo (Transparent), Roman Numeral IX Motif, SIXN Brand

## Ambiguous Edges - Review These
- `forceDeliverOrder()` → `Stock deducted once at courier pickup`  [AMBIGUOUS]
  templates/admin.html · relation: conceptually_related_to
- `Checkout Page Template` → `Fernet PII Encryption`  [AMBIGUOUS]
  templates/checkout.html · relation: conceptually_related_to
- `Roman Numeral IX Motif` → `SIXN Logo (Red Variant)`  [AMBIGUOUS]
  assets/web-media/sixn-logo-red.jpg · relation: references
- `Roman Numeral IX Motif` → `SIXN Brand`  [AMBIGUOUS]
  assets/web-media/sixn-logo-transparent.png · relation: conceptually_related_to
- `Roman Numeral IX (Nine) - AMBIGUOUS` → `IX Logo Mark`  [AMBIGUOUS]
  assets/web-media/IX.png · relation: conceptually_related_to
- `handleProductSubmit()` → `Inventory rules: on hand, reserved, available`  [AMBIGUOUS]
  templates/admin.html · relation: conceptually_related_to

## Knowledge Gaps
- **26 isolated node(s):** `get_notifications (Jinja global)`, `.env Environment Configuration`, `Announcement Bar`, `Dark Theme with Red Accent Design`, `Newsletter Signup CTA` (+21 more)
  These have ≤1 connection - possible missing edges or undocumented components. (Counts symbols only; 154 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)
- **24 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `ShiprocketService` connect `Shiprocket Service` to `App Routes (app.py core)`, `Startup Config & Integrations`?**
  _High betweenness centrality (0.059) - this node is a cross-community bridge._
- **What connects `get_notifications (Jinja global)`, `.env Environment Configuration`, `Announcement Bar` to the rest of the system?**
  _26 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `App Routes (app.py core)` be split into smaller, more focused modules?**
  _Cohesion score 0.05324481777536919 - nodes in this community are weakly interconnected._
- **Why does `read_json()` connect `App Routes (app.py core)` to `Startup Config & Integrations`, `Pricing & Stock Availability`, `Order Finalize & Shipment`, `Shiprocket Service`, `Cart, Tracking & OAuth Callback`, `Social Sign-in Seam`, `Notification Helpers`?**
  _High betweenness centrality (0.057) - this node is a cross-community bridge._
- **Should `Startup Config & Integrations` be split into smaller, more focused modules?**
  _Cohesion score 0.05454545454545454 - nodes in this community are weakly interconnected._

### Low-confidence Hints
_AMBIGUOUS edges — the extractor was unsure. Verify before acting on these._

- **What is the exact relationship between `forceDeliverOrder()` and `Stock deducted once at courier pickup`?**
  _Edge tagged AMBIGUOUS (relation: conceptually_related_to) - confidence is low._
- **What is the exact relationship between `Checkout Page Template` and `Fernet PII Encryption`?**
  _Edge tagged AMBIGUOUS (relation: conceptually_related_to) - confidence is low._