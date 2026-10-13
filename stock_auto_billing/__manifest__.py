# -*- coding: utf-8 -*-
# --- تعريف موديول الفوترة التلقائية على اعتماد الحركات المخزنية ---
{
    'name': 'Stock Auto Billing',
    'version': '20.0.3.0.0',
    'category': 'Inventory/Inventory',
    'summary': 'Automatically bill orders and stock pickings with duplicate-safe accounting links',
    'description': """
Stock Auto Billing
==================
Automatically creates the appropriate accounting document when a stock picking
is validated, based only on the quantity actually delivered, received or
returned:

* Customer delivery validated  -> Customer Invoice (delivered quantities only).
* Customer return validated    -> Customer Credit Note (returned quantities only).
* Vendor receipt validated     -> Vendor Bill (received quantities only).
* Return to vendor validated   -> Vendor Credit Note (returned quantities only).
* Sales / purchase order confirmed -> Customer invoice / vendor bill (optional).
* Sales / purchase order confirmed -> Delivery / receipt validation (optional).

All documents preserve the source order line price, discount, taxes, currency,
payment terms, fiscal position and analytic distribution. Partial deliveries /
receipts and multiple pickings per order are supported. Duplicates are never
created for the same picking or quantity.

Automatic posting is optional and controlled through per-company settings
(Settings > Inventory > Automatic Billing).
""",
    'author': 'Flous Flow / Mohamed Gamal',
    'website': 'https://flousflow.com',
    'license': 'LGPL-3',
    'depends': [
        'sale_stock',   # ربط التسليمات بأوامر البيع
        'purchase_stock',  # ربط الاستلامات بأوامر الشراء
        'account',  # إنشاء المستندات المحاسبية
    ],
    'data': [
        'views/stock_picking_views.xml',  # إضافة قسم الفوترة التلقائية للتسليم
        'views/res_config_settings_views.xml',  # إضافة إعدادات الفوترة التلقائية
    ],
    'installable': True,
    'application': False,
    'auto_install': False,
}
