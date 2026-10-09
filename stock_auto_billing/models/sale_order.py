# -*- coding: utf-8 -*-
"""Optional invoice creation when a sales order is confirmed."""

from odoo import Command, models


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    def action_confirm(self):
        result = super().action_confirm()
        self._auto_create_invoice_on_confirmation()
        return result

    def _auto_create_invoice_on_confirmation(self):
        """Create one draft customer invoice per eligible confirmed order."""
        for order in self.filtered(
            lambda sale: sale.state in ('sale', 'done')
            and sale.company_id.sab_auto_invoice_on_sale_confirmation
        ):
            order._create_confirmation_invoice()

    def _create_confirmation_invoice(self):
        self.ensure_one()
        invoice_lines = self.order_line.filtered(
            lambda line: line.product_id and not line.display_type and not line.is_downpayment
        )
        if not invoice_lines:
            return self.env['account.move']

        existing = self.env['account.move.line'].sudo().search([
            ('sale_line_ids', 'in', invoice_lines.ids),
            ('move_id.move_type', '=', 'out_invoice'),
            ('move_id.state', '!=', 'cancel'),
        ], limit=1).mapped('move_id')
        if existing:
            return existing[:1]

        values = self._prepare_invoice()
        values.update({
            'sab_order_confirmation': True,
            'invoice_line_ids': [
                Command.create(line._prepare_invoice_line(quantity=line.product_uom_qty))
                for line in invoice_lines
            ],
        })
        invoice = self.env['account.move'].sudo().with_company(self.company_id).create(values)
        if self.company_id.sab_auto_post_invoice:
            invoice.action_post()
        return invoice
