# -*- coding: utf-8 -*-
# --- تمديد شاشة الإعدادات لعرض إعدادات الفوترة التلقائية الخاصة بكل شركة ---
from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    # --- توريث شاشة الإعدادات ---
    _inherit = 'res.config.settings'

    # --- حقول مرتبطة بإعدادات الشركة (كل شركة لها إعداداتها المستقلة) ---
    sab_auto_invoice_delivery = fields.Boolean(
        related='company_id.sab_auto_invoice_delivery', readonly=False,
    )
    sab_auto_post_invoice = fields.Boolean(
        related='company_id.sab_auto_post_invoice', readonly=False,
    )
    sab_auto_credit_customer_return = fields.Boolean(
        related='company_id.sab_auto_credit_customer_return', readonly=False,
    )
    sab_auto_post_credit_customer = fields.Boolean(
        related='company_id.sab_auto_post_credit_customer', readonly=False,
    )
    sab_auto_bill_receipt = fields.Boolean(
        related='company_id.sab_auto_bill_receipt', readonly=False,
    )
    sab_auto_post_bill = fields.Boolean(
        related='company_id.sab_auto_post_bill', readonly=False,
    )
    sab_auto_credit_vendor_return = fields.Boolean(
        related='company_id.sab_auto_credit_vendor_return', readonly=False,
    )
    sab_auto_post_credit_vendor = fields.Boolean(
        related='company_id.sab_auto_post_credit_vendor', readonly=False,
    )
    sab_reduce_ordered_qty_on_return = fields.Boolean(
        related='company_id.sab_reduce_ordered_qty_on_return', readonly=False,
    )
