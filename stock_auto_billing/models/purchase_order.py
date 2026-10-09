# -*- coding: utf-8 -*-
"""Optional vendor bill creation when a purchase order is confirmed."""

from odoo import Command, models


class PurchaseOrder(models.Model):
    _inherit = 'purchase.order'

    def button_confirm(self):
        result = super().button_confirm()
        self._auto_create_bill_on_confirmation()
        return result

    def _auto_create_bill_on_confirmation(self):
        """Create one draft vendor bill per eligible fully confirmed order."""
        for order in self.filtered(
            lambda purchase: purchase.state == 'purchase'
            and purchase.company_id.sab_auto_bill_on_purchase_confirmation
        ):
            order._create_confirmation_bill()

    def _create_confirmation_bill(self):
        self.ensure_one()
        bill_lines = self.order_line.filtered(
            lambda line: line.product_id and not line.display_type and not line.is_downpayment
        )
        if not bill_lines:
            return self.env['account.move']

        existing = self.env['account.move.line'].sudo().search([
            ('purchase_line_id', 'in', bill_lines.ids),
            ('move_id.move_type', '=', 'in_invoice'),
            ('move_id.state', '!=', 'cancel'),
        ], limit=1).mapped('move_id')
        if existing:
            return existing[:1]

        values = self._prepare_invoice()
        values.update({
            'sab_order_confirmation': True,
            'invoice_line_ids': [],
        })
        for line in bill_lines:
            line_values = line._prepare_account_move_line()
            line_values['quantity'] = line.product_qty
            values['invoice_line_ids'].append(Command.create(line_values))

        bill = self.env['account.move'].sudo().with_company(self.company_id).with_context(
            default_move_type='in_invoice'
        ).create(values)
        if self.company_id.sab_auto_post_bill:
            bill.action_post()
        return bill
