# Stock Auto Billing

Automatically create the appropriate accounting document when a stock picking is
validated, based **only** on the quantity actually delivered, received or
returned. It can also create the customer invoice or vendor bill immediately
when the related order is confirmed.

Odoo 20 Community | Dependencies: `sale_stock`, `purchase_stock`, `account`

## What it does

| Validated picking | Generated document | State |
|---|---|---|
| Customer delivery linked to a confirmed Sale Order | Customer Invoice (delivered qty only) | Draft |
| Customer return (return of a delivery) | Customer Credit Note (returned qty only) | Draft |
| Vendor receipt linked to a confirmed Purchase Order | Vendor Bill (received qty only) | Draft |
| Return to vendor (return of a receipt) | Vendor Credit Note (returned qty only) | Draft |

When order-confirmation billing is enabled, the full ordered quantity is billed
at confirmation. Later delivery/receipt validation links the existing document
to the picking and does not create a duplicate.

* Documents keep the source order line **price, discount, taxes, currency,
  payment terms, fiscal position and analytic distribution**.
* Partial deliveries / receipts and **multiple pickings** per order are
  supported; only quantities that have not already been invoiced / billed are
  processed. Quantities returned and credited are treated as not (net) invoiced,
  so a delivery / receipt that happens **after a return** is still billed in
  full — no under-invoicing.
* **No duplicate documents**: the picking tracks its billing state and the
  generated document. Re-processing or retrying never creates a second
  document.
* The generated document is linked to the originating picking (`auto_account_move_id`),
  and the standard Odoo links (sale order ↔ invoice, purchase order ↔ bill,
  credit note ↔ original invoice via `reversed_entry_id`) are preserved.
* Vendor Bills and Credit Notes stay in **draft** by default.
* Automatic **posting is optional**, controlled per company in the settings.

## Configuration (Settings → Inventory → Automatic Billing)

All settings are **per-company** (each company can enable/disable independently).

* **Automatic Customer Invoices** — create an invoice after delivery validation (default on).
* **Post Customer Invoices Automatically** (default off).
* **Invoice Sales Orders on Confirmation** — create the customer invoice as soon
  as a sales order is confirmed (default off).
* **Validate Deliveries on Sales Confirmation** — validate a delivery automatically
  when the complete requested quantity is available (default off).
* **Automatic Customer Credit Notes** — after customer return validation (default on).
* **Post Customer Credit Notes Automatically** (default off).
* **Automatic Vendor Bills** — after receipt validation (default on).
* **Post Vendor Bills Automatically** (default off).
* **Bill Purchase Orders on Confirmation** — create the vendor bill as soon as a
  purchase order is confirmed (default off).
* **Validate Receipts on Purchase Confirmation** — validate the incoming receipt
  automatically after purchase confirmation (default off).
* **Automatic Vendor Credit Notes** — after vendor return validation (default on).
* **Post Vendor Credit Notes Automatically** (default off).
* **Reduce Order Quantity on Customer Return** — after a customer return is
  credited, the sale order line ordered quantity is reduced by the returned
  quantity (default on). Keeps the order net quantity consistent with the
  delivered / invoiced net quantities.

## Stock Picking fields

A small **"Automatic Billing"** section is added to the Stock Picking form
(Additional Info page):

* `auto_account_move_id` — the generated accounting document (visible to users
  with accounting read access; smart button opens it).
* `auto_billing_state` — `not_processed`, `created`, `posted`, `failed`,
  `not_applicable`.
* `auto_billing_error` — the last processing error.

A **Retry Automatic Billing** button is available for **Sales, Purchase,
Accounting or System managers** on failed / unprocessed pickings. Retry is
idempotent and never creates duplicates.

## How it works / design notes

* The automation runs inside `stock.picking._action_done()` — the single point
  where a picking actually completes validation (called by `button_validate()`
  in every path, including the backorder wizard).
* Order-confirmation billing runs after standard `sale.order.action_confirm()` or
  `purchase.order.button_confirm()`. Standard Odoo still creates the delivery or
  receipt; the accounting document is created separately and remains linked to
  the order.
* Automatic stock validation is opt-in and conservative: outgoing deliveries are
  validated only when all requested quantities are reserved. Missing stock,
  lot/serial data or another standard validation requirement leaves the picking
  ready and records the reason in its chatter; order confirmation is not blocked.
* It runs **after** the inventory operation is complete and never blocks it: if
  document creation fails, the error is caught, stored in
  `auto_billing_error`, the state is set to `failed`, a clear message is posted
  in the picking chatter, and the picking stays validated.
* A DB savepoint isolates the accounting work: a failure rolls back only the
  partial accounting changes.
* Invoice / bill creation uses the **standard Odoo preparation methods**
  (`sale.order._prepare_invoice`, `sale.order.line._prepare_invoice_line`,
  `purchase.order._prepare_invoice`, `purchase.order.line._prepare_account_move_line`)
  and the standard links (`sale_line_ids`, `purchase_line_id`, `reversed_entry_id`).
  No journal entry lines are hand-written.
* Elevated permissions are **scoped**: document creation runs through `sudo()`
  inside the automation only, so an Inventory user can validate stock
  operations without any Accounting access. Inventory users cannot change
  accounting configuration, journals, accounts, payments, or cancel/delete
  posted entries.
* Internal transfers, manufacturing, inventory adjustments, scrap and pickings
  not linked to a Sale/Purchase order are ignored (`not_applicable`).
* Multi-company and multi-currency are respected: every document is created
  inside the picking's company, and the order currency is preserved.
* **No phantom "to invoice" quantity after returns**: for products invoiced on
  ordered quantities, Odoo's standard formula (`ordered − net invoiced`) would
  show returned+credited quantities as still invoiceable. This module computes
  `qty_to_invoice` and `invoice_status` from the **gross invoiced quantity**
  (out invoices only, refunds excluded), so a return + credit note leaves the
  order correctly marked `invoiced` with nothing left to invoice.

## Tests

Run (inside the Odoo container):

```bash
odoo --db_host=db --db_user=odoo --db_password=odoo \
     -d test --test-enable -u stock_auto_billing --stop-after-init --http-port=8071
```

Covered scenarios: deliveries, receipts, partial quantities, backorders,
customer returns, vendor returns, duplicate prevention, failed processing,
retry processing (incl. permission check), multi-company isolation,
configuration settings and internal transfers.
