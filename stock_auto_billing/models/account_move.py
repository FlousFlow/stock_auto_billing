# -*- coding: utf-8 -*-
"""Metadata used to distinguish order-confirmation documents from picking bills."""

from odoo import fields, models


class AccountMove(models.Model):
    _inherit = 'account.move'

    sab_order_confirmation = fields.Boolean(
        string='Created on Order Confirmation',
        copy=False,
        readonly=True,
        help='Technical marker for invoices and bills created when an order was confirmed.',
    )
