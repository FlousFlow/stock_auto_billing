# Stock Auto Billing — Odoo 20

Flous Flow Odoo 20 addon that creates draft customer invoices, vendor bills,
and related credit notes after completed stock pickings, with idempotent retry
handling and a smart button linking the picking to its accounting document.

The addon is located in [`stock_auto_billing/`](stock_auto_billing/).

## Odoo Apps registration

Use the `20.0` branch of this repository:

```text
ssh://git@github.com/FlousFlow/stock_auto_billing.git#20.0
```

Dependencies: `sale_stock`, `purchase_stock`, and `account`.

## License

LGPL-3.0-or-later. See [`stock_auto_billing/LICENSE`](stock_auto_billing/LICENSE).
