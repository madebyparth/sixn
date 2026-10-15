# Spec: NTS fulfilment & inventory (phase 2)

## Capability map
| Module id | Responsibility | Depends on |
|---|---|---|
| storefront | PLP/PDP/cart/checkout/payments (SPEC.md) | — |
| inventory | Per-size stock on hand, reservations, availability shown to shoppers and enforced at checkout | storefront (orders) |
| fulfilment | Admin-only delivery desk: pack → ship (Shiprocket label + pickup) → track → deliver → returns/restock | inventory, storefront |

Build order: inventory → fulfilment. Both read/write the same MongoDB (`products`, `orders`).

## Objective
Give the administrator one page, `/admin/fulfilment`, to run day-to-day delivery and stock for the 4 tees, without
using the Shiprocket dashboard for routine work, and stop the store from overselling.

## Inventory rules
- `product.stock = {"S": int, "M": int, "L": int}` = units physically on hand. Missing key ⇒ unlimited (back-compatible).
- **Reserved** = units in orders that are placed but not yet handed to the courier
  (`status in {paid, cod_pending}` and not `stock_deducted`).
- **Available** = on hand − reserved. Shoppers see a size as sold out at 0; the server rejects any order line that
  exceeds availability (same 400 path as an unknown size).
- On-hand is decremented exactly once per order, when the admin schedules the courier pickup (`stock_deducted: true`).
  Cancelling before that frees the reservation automatically. Returned stock comes back via an explicit "Restock" action.

## Fulfilment stages (derived from the order, no new status values except `shipped`)
| Stage | Condition |
|---|---|
| To ship | `paid`/`cod_pending`, not `stock_deducted` |
| In transit | `shipped` |
| Delivered | `delivered` |
| Returns | `return_requested`, `return_approved`, `return_initiated`, `returned` |
| Closed | `cancelled`, `refunded` |

Actions (all `@admin_required`, CSRF-protected JSON APIs):
- **Create shipment** — when automatic Shiprocket creation failed (prepaid only; COD shipments are created at order time).
- **Label** — Shiprocket `courier/generate/label` → opens label PDF.
- **Schedule pickup** — Shiprocket `courier/generate/pickup` → status `shipped`, deduct stock.
- **Sync tracking** — all in-transit AWBs → `delivered` (+ `delivered_at`) when the courier reports delivered.
- **Approve return** — existing endpoint. **Restock** — adds a returned order's units back to on-hand once.
- **Stock** — edit on-hand per product/size.

## Testing
unittest: availability math, oversell rejection, stage bucketing, pickup deducts once, restock once,
non-admin gets redirected/403. Shiprocket calls mocked.

## Boundaries
- Never: change payment/refund logic here; delete orders.
- Ask first: multi-warehouse, auto-reorder, courier selection rules.

## Out of scope
Purchase orders/suppliers, multiple pickup locations, re-attempt requests to Shiprocket's NDR API — next iterations.

## Status (2026-10-09)
Implemented: stock + reservations (`reserved_units`, `available_units`), oversell rejected at checkout, sold-out sizes on
PDP/PLP, `/admin/fulfilment` (stock editor, stage tabs, create shipment/AWB, label, schedule pickup, sync tracking,
approve return, restock), linked from /admin and the menu (admins only). Manual "shipped/delivered" status changes in
/admin also deduct stock once. Prepaid Shiprocket booking extracted to `create_prepaid_shipment` (used by checkout and retry).
Verified: 8 unit tests in `tests/test_fulfilment.py` (Shiprocket mocked) + screenshot.
Not verified: real Shiprocket label/pickup responses (needs credentials) — the code accepts `pickup_status == 1` or an
"already scheduled" message; check the first live pickup in the server log.
Failed delivery: sync flags UNDELIVERED/NDR/RTO statuses as `delivery_issue` (red on the desk); `RTO DELIVERED` moves the
order to `rto_received` (Returns tab) so it can be restocked, and refunded from /admin if prepaid.
