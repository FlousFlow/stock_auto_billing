# Stock Auto Billing — Server Deployment Guide

Odoo 20 Community · Dependencies: `sale_stock`, `purchase_stock`, `account`

## 1. Copy the module to the server

Copy the whole `stock_auto_billing/` folder to your custom addons path on the
server (the folder mounted / configured as addons in Odoo):

```bash
scp -r stock_auto_billing user@server:/opt/odoo/custom-addons/
```

Verify the addons path is in your Odoo config (`/etc/odoo/odoo.conf` or your
systemd/Docker config):

```ini
addons_path = /opt/odoo/addons,/opt/odoo/custom-addons
```

Make sure the folder is readable by the Odoo system user:

```bash
sudo chown -R odoo:odoo /opt/odoo/custom-addons/stock_auto_billing
```

Restart the Odoo service so the module is discovered:

```bash
sudo systemctl restart odoo   # systemd
# or
docker compose restart web    # Docker
```

## 2. Install the module

From the Odoo Apps screen (developer mode → Apps → Update Apps List → search
"Stock Auto Billing" → Install), or via CLI:

```bash
# systemd
sudo -u odoo odoo -d PRODUCTION_DB -i stock_auto_billing --stop-after-init

# Docker
docker exec <web-container> odoo --db_host=db --db_user=odoo --db_password=odoo \
    -i stock_auto_billing -d PRODUCTION_DB --stop-after-init
```

The module will install together with its dependencies (`sale_stock`,
`purchase_stock`, `account`).

## 3. Configure (Settings → Inventory → Automatic Billing)

Per-company settings (defaults in parentheses):

| Setting | Default | Meaning |
|---|---|---|
| Automatic Customer Invoices | ✅ On | Invoice after delivery validation |
| Post Customer Invoices Automatically | Off | Auto-post instead of draft |
| Automatic Customer Credit Notes | ✅ On | Credit note after customer return |
| Post Customer Credit Notes Automatically | Off | |
| Automatic Vendor Bills | ✅ On | Bill after receipt validation |
| Post Vendor Bills Automatically | Off | |
| Automatic Vendor Credit Notes | ✅ On | Credit note after vendor return |
| Post Vendor Credit Notes Automatically | Off | |
| Reduce Order Quantity on Customer Return | ✅ On | SO ordered qty reduced by returned qty |

> ⚠️ Recommendation: keep "Post ... Automatically" **Off** in production so a
> human reviews every accounting document before posting.

## 4. Post-install verification checklist

- [ ] Settings tab "Automatic Billing" visible (Settings → Inventory) with the icon.
- [ ] Validate a delivery linked to a confirmed SO → a **draft** Customer
      Invoice is created, smart button "Billing" appears on the picking.
- [ ] Validate a receipt linked to a confirmed PO → a **draft** Vendor Bill.
- [ ] Return a delivery → a **draft** Customer Credit Note linked to the
      original invoice (`reversed_entry_id`).
- [ ] Return a receipt to vendor → a **draft** Vendor Credit Note.
- [ ] Stock Picking → Additional Info page shows `Automatic Billing Status`.
- [ ] A failed run keeps the picking `done` and stores the error (Retry button
      is manager-only).

## 5. Run the automated tests (optional, on a test DB)

```bash
docker exec <web-container> odoo --db_host=db --db_user=odoo --db_password=odoo \
    -d test_db --test-enable -u stock_auto_billing --stop-after-init
```

Expected: `0 failed, 0 error(s) of 19 tests`.

## 6. Update the module after a code change

```bash
sudo -u odoo odoo -d PRODUCTION_DB -u stock_auto_billing --stop-after-init
```

## 7. Rollback / uninstall

- **Uninstall**: Apps → Stock Auto Billing → Uninstall. This removes the added
  fields from `res.company`, `res.config.settings`, `sale.order.line` and
  `stock.picking`. Existing generated invoices / bills are **not** deleted.
- **Disable without uninstalling**: turn off the per-company settings; pickings
  are then marked `Not Applicable` and nothing is generated.

## 8. Known behavior notes

- Only **delivered / received / returned** quantities are billed. Nothing is
  generated for internal transfers, manufacturing, inventory adjustments, scrap
  or pickings not linked to a Sale/Purchase order.
- A failure never blocks the picking validation; it is recorded and retryable
  by managers.
- For products invoiced on ordered quantities, returns + credit notes do **not**
  leave a phantom "to invoice" quantity.
- A delivery / receipt that happens **after** a return is still billed in full
  (no under-invoicing).
