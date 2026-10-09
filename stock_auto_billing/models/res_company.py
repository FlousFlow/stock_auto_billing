# -*- coding: utf-8 -*-
# --- تمديد نموذج الشركة لإضافة إعدادات الفوترة التلقائية الخاصة بكل شركة ---
from odoo import fields, models


class ResCompany(models.Model):
    # --- توريث نموذج الشركة بدون نموذج جديد ---
    _inherit = 'res.company'

    # --- تفعيل إنشاء فاتورة عميل تلقائياً بعد اعتماد التسليم المرتبط بأمر بيع ---
    sab_auto_invoice_delivery = fields.Boolean(
        string='Automatic Customer Invoices',
        help='Create a customer invoice automatically when an outgoing delivery '
             'linked to a confirmed sale order is validated.',
        default=True,
    )
    # --- ترحيل فاتورة العميل تلقائياً بعد إنشائها ---
    sab_auto_post_invoice = fields.Boolean(
        string='Post Customer Invoices Automatically',
        help='Post the automatically created customer invoice.',
        default=False,
    )
    # --- تفعيل إنشاء إشعار دائن للعميل بعد اعتماد مرتجع ---
    sab_auto_credit_customer_return = fields.Boolean(
        string='Automatic Customer Credit Notes',
        help='Create a customer credit note automatically when a customer return '
             'picking is validated.',
        default=True,
    )
    # --- ترحيل إشعار الدائن الخاص بالعميل تلقائياً ---
    sab_auto_post_credit_customer = fields.Boolean(
        string='Post Customer Credit Notes Automatically',
        help='Post the automatically created customer credit note.',
        default=False,
    )
    # --- تفعيل إنشاء فاتورة مورد تلقائياً بعد اعتماد الاستلام المرتبط بأمر شراء ---
    sab_auto_bill_receipt = fields.Boolean(
        string='Automatic Vendor Bills',
        help='Create a vendor bill automatically when an incoming receipt linked '
             'to a confirmed purchase order is validated.',
        default=True,
    )
    # --- ترحيل فاتورة المورد تلقائياً بعد إنشائها ---
    sab_auto_post_bill = fields.Boolean(
        string='Post Vendor Bills Automatically',
        help='Post the automatically created vendor bill.',
        default=False,
    )
    # --- تفعيل إنشاء إشعار دائن للمورد بعد اعتماد مرتجع للمورد ---
    sab_auto_credit_vendor_return = fields.Boolean(
        string='Automatic Vendor Credit Notes',
        help='Create a vendor credit note automatically when a return to vendor '
             'picking is validated.',
        default=True,
    )
    # --- ترحيل إشعار الدائن الخاص بالمورد تلقائياً ---
    sab_auto_post_credit_vendor = fields.Boolean(
        string='Post Vendor Credit Notes Automatically',
        help='Post the automatically created vendor credit note.',
        default=False,
    )
    # --- تقليل كمية الطلب في أمر البيع بمقدار الكمية المرتجعة بعد المرتجع ---
    sab_reduce_ordered_qty_on_return = fields.Boolean(
        string='Reduce Order Quantity on Customer Return',
        help='Reduce the sale order line ordered quantity by the returned quantity '
             'when a customer return is credited. Keeps the order net quantity '
             'consistent with the delivered/invoiced net quantities.',
        default=True,
    )
