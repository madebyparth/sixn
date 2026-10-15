# SIXN | Premium E-Commerce Storefront

![SIXN Storefront Showcase Banner](assets/web-media/website.png)

SIXN is a premium, feature-rich, and highly secure e-commerce storefront built with **Flask** and **MongoDB**. Designed for maximum visual impact, it integrates **Twilio OTP** authentication, **Razorpay** payment gateway, a **custom Wallet system** for store credit, and **Shiprocket** automated shipping logistics.

---

## Table of Contents
- [Key Features](#key-features)
- [System Architecture](#system-architecture)
- [Tech Stack](#tech-stack)
- [Environment Configuration](#environment-configuration)
- [Installation & Local Setup](#installation--local-setup)
- [Project Directory Structure](#project-directory-structure)
- [Security & PII Data Protection](#security--pii-data-protection)
- [License](#license)

---

## Key Features

### 🌟 Immersive Frontend Experience
*   **Dynamic Product Showcase**: Responsive sliding hero catalog with custom keyboard/mouse transition controls.
*   **Integrated Cart Drawer**: Accessible side panel cart with real-time subtotal and discount logic.
*   **Clean Design Language**: Curated typography (Syne & Space Grotesk), dark mode aesthetics, and micro-interactions.

### 🔐 Passwordless SMS Authentication
*   **Twilio OTP Integration**: Seamless phone-number sign-up and login utilizing SMS verification.
*   **Robust Session Management**: Secure user sessions powered by Flask-Login and bcrypt password-hashing.

### 💳 Payments & Checkout Ecosystem
*   **Razorpay Gateway Integration**: Direct prepaid checkout flow with signature verification to prevent order tampering.
*   **Custom Store Wallet**: Users can earn store credit, check transaction logs, and perform full or split payments (Wallet + Razorpay).
*   **Automated Cancellations & Refunds**: Cancelling a prepaid order automatically triggers an instant refund request back to the customer's payment source.

### 🚚 Automated Shipping (Shiprocket Integration)
*   **Serviceability Checks**: Real-time pin code verification before checkout to check courier availability.
*   **Smart Courier Selection**: Selects the best courier partner based on shipping rates, pickup speeds, and delivery estimates.
*   **Automated Consignment Creation**: Automates order creation, schedules courier pick-up, and generates shipping labels from the admin panel.
*   **Live Tracking**: Customers can check live tracking logs and shipment updates directly from their user dashboard.

### 📊 Full-Scale Admin Control Center
*   **Sales & Product Analytics**: High-level charts tracking total users, orders, revenue, and product stock counts.
*   **Catalog & Inventory Editor**: Add, edit, or remove products, manage variant sizes, upload images, and control stock status.
*   **Custom Coupon Generator**: Create percentage or fixed-discount coupon codes with min-spend rules.
*   **Order Orchestration**: Dispatch orders, download shipping invoices, create pickups, or cancel shipments.
*   **Review & Flag Moderation**: Inspect reviews, filter flagged items, and manage customer ratings.

---

## System Architecture

The following diagram illustrates how the core application server interfaces with MongoDB and third-party APIs:

```mermaid
graph TD
    Client[Client Web Browser] <-->|HTTPS / HTML, CSS & JS| Server[Flask Application Server]
    Server <-->|Secure Query| Mongo[(MongoDB Database)]
    Server <-->|PII Encrypt / Decrypt| Crypto[Fernet Symmetric Cryptography]
    Server <-->|Send OTP / SMS| Twilio[Twilio SMS API]
    Server <-->|Checkout / Auto-Refund| Razorpay[Razorpay API]
    Server <-->|Courier Serviceability & Tracking| Shiprocket[Shiprocket API]
```

---

## Tech Stack

*   **Backend Framework**: Python (Flask)
*   **Authentication & Security**: Flask-Login, Flask-Bcrypt, Flask-Limiter, Cryptography (Fernet)
*   **Database**: MongoDB (via PyMongo)
*   **Payment Processing**: Razorpay SDK
*   **SMS Gateway**: Twilio SDK
*   **Logistics Engine**: Shiprocket REST API
*   **Frontend Technologies**: HTML5, Vanilla CSS, JS (ES6), TailwindCSS

---

## Environment Configuration

Copy `.env.example` to `.env`. Empty values (or `REPLACE_ME`) mean "not configured": the app still starts, logs which
integrations are disabled (`[STARTUP] Integrations:`), and turns the dependent feature off instead of crashing.
`python preflight.py` lists everything still missing for launch and exits non-zero until it passes.

### Required for launch

| Key | What it is | If missing |
| :--- | :--- | :--- |
| `SECRET_KEY` | Flask session key (`python -c "import secrets; print(secrets.token_hex(32))"`) | Sessions are insecure |
| `MONGO_URI` | MongoDB connection string | App cannot store anything |
| `ENCRYPTION_KEY`, `PHONE_ENCRYPTION_KEY`, `ADDRESS_ENCRYPTION_KEY` | Fernet keys for PII | Addresses/phones cannot be saved |
| `ADMIN_USER_ID` | User id of the admin account | No access to `/admin`, `/admin/fulfilment` |
| `RAZORPAY_KEY`, `RAZORPAY_SECRET` | Razorpay live keys (after KYC) | Checkout shows "Online payments not yet enabled"; Cash on Delivery still works |
| `RAZORPAY_WEBHOOK_SECRET` | Webhook to `https://<domain>/api/payment/webhook` (events `payment.captured`, `order.paid`) | A paid order is lost if the shopper closes the tab before the browser confirms |
| `SHIPROCKET_EMAIL`, `SHIPROCKET_PASSWORD`, `SHIPROCKET_PICKUP_LOCATION`, `SHIPROCKET_PICKUP_PINCODE` | Shiprocket API user + pickup address | No PIN-code checks, shipments, labels or pickups |
| `TWILIO_SID`, `TWILIO_TOKEN`, `TWILIO_PHONE` | SMS for OTP | OTP is off, so the admin cannot sign in (customers do not need it) |
| `BUSINESS_LEGAL_NAME`, `BUSINESS_ADDRESS`, `SUPPORT_EMAIL`, `SUPPORT_PHONE`, `GRIEVANCE_OFFICER_NAME` (+ `BUSINESS_GSTIN`, `BUSINESS_JURISDICTION_CITY`, `GRIEVANCE_OFFICER_EMAIL`) | Shown on Terms / Privacy / Returns / Contact | Highlighted gaps on those pages; Razorpay will not activate live mode |

**Google Pay** has no key of its own: in India it is paid through Razorpay's UPI option, which checkout shows first.

### Optional for now

| Key | Default | What it does |
| :--- | :--- | :--- |
| `RETURN_WINDOW_DAYS` | `7` | Days after delivery a return can be requested (enforced and shown on `/returns`) |
| `PHONE_LOGIN_ENABLED` | `0` | `1` = customers can sign up / log in with phone + OTP. `0` = guest checkout by email (admins can always sign in) |
| `REQUIRE_EMAIL_CONFIRMATION` | `0` | `1` = guests must confirm their email first. Keep `0` until a confirmation email is implemented |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | empty | "Continue with Google" (sign-in modal + checkout). Redirect URI: `<site>/auth/google/callback` |
| `PUBLIC_BASE_URL` | empty | Site URL for OAuth redirect URIs in production, e.g. `https://ntslabel.com` |
| `META_APP_ID`, `META_APP_SECRET` | empty | Facebook / Instagram sign-in, same as above |
| `FLASK_DEBUG` | `0` | Development only; with no Twilio, OTPs are printed to the console |

### Checkout and sign-in

- **Guest checkout:** name, email and a mobile number for the courier. No login or OTP. Orders, carts and customer
  records are keyed by email (`customers`, `carts` collections; `orders.customer_email` + optional `orders.user_id`).
- **Order status:** guests use `/track-order` with their email + order number.
- **Google sign-in:** optional "Continue with Google"; earlier guest orders with the same verified Gmail move into
  the account (`link_social_identity()`). Meta can plug into the same seam in `services/auth_providers.py`.

---

## Installation & Local Setup

### 1. Prerequisites
Ensure you have the following installed on your system:
*   [Python 3.8+](https://www.python.org/downloads/)
*   [MongoDB Community Server](https://www.mongodb.com/try/download/community) (running locally on port `27017`)

### 2. Clone the Repository
```bash
git clone https://github.com/madebyparth/sixn
cd sixn
```

### 3. Create a Virtual Environment
```bash
# Windows
python -m venv venv
venv\Scripts\activate

# macOS / Linux
python3 -m venv venv
source venv/bin/activate
```

### 4. Install Dependencies
```bash
pip install -r requirements.txt
```

### 5. Configure Environment Variables
Copy `.env.example` to `.env` and fill in your actual credentials:
```bash
cp .env.example .env
```

### 6. Initialize & Seed MongoDB (Database Setup)
Since you are setting up the project with a fresh local MongoDB database, your database will initially be completely empty. If you start the app at this stage, the storefront will have no products, no collections, and no layout banners, resulting in a blank or malfunctioning UI.

To bootstrap your database with default demo data, run the migration seeding script:
```bash
python migrate_to_mongo.py
```

**What this script does:**
*   **Populates Showcase Catalog**: It reads default catalog products from `assets/products.json` and writes them into your local MongoDB `products` collection, seeding your store with items out-of-the-box.
*   **Configures Homepage Layout**: It imports homepage layout configs (slider details, meta banners) from `assets/content.json` to establish default themes and styling.
*   **Initializes Collection Schemas**: It boots empty structures for operational databases (users, orders, ratings, OTP keys, coupons, notifications) so your backend can query and record transactions immediately.

> [!WARNING]
> `migrate_to_mongo.py` re-imports **every** collection from local JSON files and empties collections whose file is
> missing. On a live database use these instead:
> ```bash
> python seed_nts.py                      # replace the product catalog with assets/products.json (4 NTS tees)
> python migrate_guest_checkout.py        # key existing orders by email (idempotent, add --dry-run to preview)
> ```

### 7. Run the Application
Start the Flask development server:
```bash
python app.py
```
Open your browser and navigate to `http://127.0.0.1:5000`.

---

## Project Directory Structure

```text
├── assets/                  # Static design and database seed resources
│   ├── user-media/          # User default profile placeholders
│   ├── web-media/           # Store theme assets and visual logos
│   ├── content.json         # Seeding layout data for MongoDB
│   ├── products.json        # Seeding product catalog data for MongoDB
│   └── website.png          # Main repository banner / screenshot
├── services/                # Integration microservices
│   ├── data/                # Temporary service cache files (git-ignored)
│   └── shiprocket_service.py # Shiprocket API courier service handler
├── static/                  # Client-side static assets
│   └── uploads/             # User and product media uploads (git-ignored)
├── templates/               # Jinja2 HTML templates
│   ├── base.html            # Main wrapper, navigation, drawer components
│   ├── index.html           # Brand landing page & sliding showcase
│   ├── product.html         # Detailed variant view, inventory checks & reviews
│   ├── checkout.html        # Coupons, wallet splits, Razorpay gateway triggers
│   └── admin.html           # Full storefront catalog and sales dashboard
├── utils/                   # Shared utility modules
│   ├── db.py                # PyMongo engine and JSON migration drivers
│   └── security.py          # PII Field encryption and data anonymization
├── .env.example             # Configuration settings template
├── .gitignore               # Excludes secrets, uploads, and caches
├── LICENSE                  # MIT open-source license
├── app.py                   # Central Flask controller, middleware and route handlers
├── migrate_to_mongo.py      # Database bootstrap initialization script
└── requirements.txt         # Project package dependencies
```

---

## Security & PII Data Protection

To comply with modern user privacy standards, SIXN incorporates **Fernet symmetric data encryption** via the Python `cryptography` library. 
*   All user **phone numbers**, **shipping addresses**, and **billing addresses** are automatically encrypted before being written to MongoDB.
*   Data is decrypted transparently on reading, ensuring that even in the event of a database dump or leakage, user PII remains entirely secure and unreadable.
*   Make sure to keep your `ENCRYPTION_KEY`, `PHONE_ENCRYPTION_KEY`, and `ADDRESS_ENCRYPTION_KEY` backed up securely. If these keys are lost, existing encrypted user profiles cannot be decrypted.

---

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
