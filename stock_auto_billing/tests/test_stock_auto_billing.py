# -*- coding: utf-8 -*-
# --- اختبارات موديول الفوترة التلقائية على اعتماد الحركات المخزنية ---
from unittest.mock import patch

from odoo import Command, fields
from odoo.exceptions import UserError
from odoo.tests import Form, tagged

from odoo.addons.sale.tests.common import TestSaleCommon

from odoo.addons.stock_auto_billing.models.stock_picking import StockPicking


@tagged('post_install', '-at_install')
class TestStockAutoBilling(TestSaleCommon):
    """اختبارات شاملة للفوترة التلقائية على اعتماد الحركات المخزنية."""

    @classmethod
    def setUpClass(cls):
        # --- نجهز بيانات الاختبار الأساسية من الفئات الجاهزة ---
        super().setUpClass()
        # --- مورّد للاختبارات (له حساب دائن افتراضي) ---
        cls.vendor_a = cls.env['res.partner'].create({
            'name': 'SAB Vendor',
            'is_company': True,
            'supplier_rank': 1,
            'property_account_payable_id': cls.company_data['default_account_payable'].id,
            'company_id': False,
        })
        # --- منتج مخزني (مستهلك) يمر بالتسليم والاستلام مع ضريبة البيع والشراء ---
        cls.stock_product = cls._create_product(
            name='SAB Stock Product',
            type='consu',
            tracking=False,
            list_price=100.0,
            standard_price=60.0,
            invoice_policy='delivery',
            taxes_id=[Command.set(cls.tax_sale_a.ids)],
            supplier_taxes_id=[Command.set(cls.tax_purchase_a.ids)],
        )
        # --- منتج مخزني فوترته على الكمية المطلوبة (يختبر مشكلة الكمية الوهمية) ---
        cls.order_product = cls._create_product(
            name='SAB Order Product',
            type='consu',
            tracking=False,
            list_price=100.0,
            standard_price=60.0,
            invoice_policy='order',
            taxes_id=[Command.set(cls.tax_sale_a.ids)],
            supplier_taxes_id=[Command.set(cls.tax_purchase_a.ids)],
        )

    # ------------------------------------------------------------------
    # أدوات مساعدة
    # ------------------------------------------------------------------
    def _create_sale_order(self, product, qty, partner=None):
        """إنشاء وتأكيد أمر بيع بسطر واحد."""
        # --- نحدد العميل (افتراضياً العميل أ) ---
        partner = partner or self.partner_a
        # --- ننشئ أمر البيع ---
        so = self.env['sale.order'].create({
            'partner_id': partner.id,
            'partner_invoice_id': partner.id,
            'partner_shipping_id': partner.id,
            'order_line': [(0, 0, {
                'product_id': product.id,
                'product_uom_qty': qty,
                'price_unit': product.list_price,
            })],
        })
        # --- نؤكد أمر البيع لإنشاء التسليم ---
        so.action_confirm()
        # --- نرجع الأمر ---
        return so

    def _create_purchase_order(self, product, qty, partner=None):
        """إنشاء وتأكيد أمر شراء بسطر واحد."""
        # --- نحدد المورد (افتراضياً المورد أ) ---
        partner = partner or self.vendor_a
        # --- ننشئ أمر الشراء ---
        po = self.env['purchase.order'].create({
            'partner_id': partner.id,
            'order_line': [(0, 0, {
                'product_id': product.id,
                'product_qty': qty,
                'price_unit': product.standard_price,
            })],
        })
        # --- نؤكد أمر الشراء لإنشاء الاستلام ---
        po.button_confirm()
        # --- نرجع الأمر ---
        return po

    def _validate_picking(self, picking, qty=None):
        """ضبط الكمية المنجزة واعتماد التسليم (مع معالجة الطلبات المؤجلة)."""
        # --- نضبط الكمية المنجزة لو حُددت ---
        if qty is not None:
            picking.move_ids.quantity = qty
        # --- نستدعي الاعتماد ---
        res = picking.button_validate()
        # --- لو طُلب طلب مؤجل نعالجه وننشئ الطلب المؤجل ---
        if isinstance(res, dict):
            # --- نفتح نافذة الطلب المؤجل وننشئه ---
            wizard = Form.from_action(self.env, res).save()
            wizard.process()
        # --- نرجع التسليم بعد الاعتماد ---
        return picking

    def _create_return(self, picking, qty):
        """إنشاء مرتجع من التسليم المحدد بكمية معينة."""
        # Odoo 20 creates return pickings directly; the old
        # ``stock.return.picking`` wizard was removed.
        return_pick = picking._create_return()
        return_pick.move_ids.quantity = qty
        return return_pick

    @staticmethod
    def _product_line(invoice):
        """إرجاع سطر المنتج الوحيد في المستند المحاسبي."""
        # --- نرجع أول سطر منتج (display_type يساوي product أو فارغ) وليس خط ضريبة أو قسماً ---
        return invoice.invoice_line_ids.filtered(
            lambda l: l.display_type in (False, 'product')
        )

    # ------------------------------------------------------------------
    # اختبارات المبيعات والتسليمات
    # ------------------------------------------------------------------
    def test_sale_confirmation_creates_customer_invoice(self):
        """Optional order-confirmation invoicing creates the invoice immediately."""
        self.env.company.sab_auto_invoice_on_sale_confirmation = True
        so = self._create_sale_order(self.stock_product, 5)

        self.assertEqual(len(so.invoice_ids), 1)
        invoice = so.invoice_ids
        self.assertEqual(invoice.move_type, 'out_invoice')
        self.assertEqual(invoice.state, 'draft')
        self.assertEqual(self._product_line(invoice).quantity, 5)
        self.assertEqual(so.picking_ids.auto_billing_state, 'not_processed')

    def test_sale_confirmation_can_validate_delivery(self):
        """The opt-in setting validates a fully available delivery."""
        self.env.company.sab_auto_validate_delivery_on_sale_confirmation = True
        so = self._create_sale_order(self.stock_product, 5)

        self.assertEqual(so.picking_ids.state, 'done')

    def test_delivery_creates_customer_invoice(self):
        """تسليم كامل ينشئ فاتورة عميل بالكمية المسلّمة ويربطها بالتسليم وأمر البيع."""
        # --- ننشئ أمر بيع بكمية 5 ونؤكده ---
        so = self._create_sale_order(self.stock_product, 5)
        # --- نأخذ التسليم ونعتمده بالكمية الكاملة ---
        picking = self._validate_picking(so.picking_ids, 5)

        # --- التسليم مكتمل والفاتورة أُنشئت ---
        self.assertEqual(picking.state, 'done')
        self.assertEqual(picking.auto_billing_state, 'created')
        # --- الفاتورة موجودة ومسودة (لا ترحيل افتراضياً) ---
        self.assertTrue(picking.auto_account_move_id)
        invoice = picking.auto_account_move_id
        self.assertEqual(invoice.move_type, 'out_invoice')
        self.assertEqual(invoice.state, 'draft')
        # --- الكمية والمنتج والسعر ---
        line = self._product_line(invoice)
        self.assertEqual(line.product_id, self.stock_product)
        self.assertEqual(line.quantity, 5)
        self.assertEqual(line.price_unit, 100.0)
        # --- الضرائب محفوظة من بند أمر البيع ---
        self.assertIn(self.tax_sale_a, line.tax_ids)
        # --- الارتباط بأمر البيع ---
        self.assertEqual(invoice.invoice_origin, so.name)
        self.assertIn(invoice, so.invoice_ids)
        self.assertEqual(invoice.partner_id, self.partner_a)

    def test_partial_delivery_invoices_only_delivered(self):
        """تسليم جزئي + طلب مؤجل ينشئ فاتورتين بالكميات المسلّمة فقط دون تكرار."""
        # --- أمر بيع بكمية 10 ---
        so = self._create_sale_order(self.stock_product, 10)
        pick1 = so.picking_ids
        # --- نسلم 4 فقط وننشئ طلباً مؤجلاً للباقي ---
        self._validate_picking(pick1, 4)

        # --- الفاتورة الأولى بالكمية 4 ---
        self.assertEqual(pick1.auto_billing_state, 'created')
        self.assertEqual(self._product_line(pick1.auto_account_move_id).quantity, 4)
        # --- يوجد طلب مؤجل ---
        self.assertTrue(pick1.backorder_ids)
        backorder = pick1.backorder_ids

        # --- نعتمد الطلب المؤجل بالكمية المتبقية 6 ---
        self._validate_picking(backorder, 6)
        self.assertEqual(self._product_line(backorder.auto_account_move_id).quantity, 6)

        # --- فاتورتان فقط ولا تكرار ---
        self.assertEqual(len(so.invoice_ids), 2)
        total_qty = sum(
            l.quantity for inv in so.invoice_ids for l in self._product_line(inv)
        )
        self.assertEqual(total_qty, 10)

    def test_no_duplicate_invoice_for_same_picking(self):
        """إعادة المعالجة لا تنشئ فاتورة مكررة لنفس التسليم."""
        # --- أمر بيع وتسليم كامل ---
        so = self._create_sale_order(self.stock_product, 5)
        picking = self._validate_picking(so.picking_ids, 5)
        invoice = picking.auto_account_move_id

        # --- إعادة تشغيل المعالجة لا تنشئ شيئاً ---
        picking._auto_billing_process()
        self.assertEqual(picking.auto_account_move_id, invoice)
        self.assertEqual(len(so.invoice_ids), 1)

        # --- إعادة المحاولة على تسليم معالج ترفض ---
        with self.assertRaises(UserError):
            picking.action_retry_auto_billing()

    def test_disabled_auto_invoice_no_invoice(self):
        """تعطيل الإعداد يمنع إنشاء الفاتورة ويحدد الحالة غير قابلة للتطبيق."""
        # --- نعطل الفوترة التلقائية للعملاء ---
        self.env.company.sab_auto_invoice_delivery = False
        # --- أمر بيع وتسليم كامل ---
        so = self._create_sale_order(self.stock_product, 5)
        picking = self._validate_picking(so.picking_ids, 5)
        # --- لا فاتورة والحالة غير قابلة للتطبيق ---
        self.assertEqual(picking.auto_billing_state, 'not_applicable')
        self.assertFalse(picking.auto_account_move_id)
        self.assertEqual(len(so.invoice_ids), 0)

    def test_auto_post_invoice_setting_posts(self):
        """تفعيل الترحيل التلقائي يرحّل فاتورة العميل مباشرة."""
        # --- نفعّل الترحيل التلقائي للفواتير ---
        self.env.company.sab_auto_post_invoice = True
        # --- أمر بيع وتسليم كامل ---
        so = self._create_sale_order(self.stock_product, 5)
        picking = self._validate_picking(so.picking_ids, 5)
        # --- الحالة مرحلة والفاتورة مرحّلة ---
        self.assertEqual(picking.auto_billing_state, 'posted')
        self.assertEqual(picking.auto_account_move_id.state, 'posted')

    # ------------------------------------------------------------------
    # اختبارات المشتريات والاستلامات
    # ------------------------------------------------------------------
    def test_purchase_confirmation_creates_vendor_bill(self):
        """Optional order-confirmation billing creates the bill immediately."""
        self.env.company.sab_auto_bill_on_purchase_confirmation = True
        po = self._create_purchase_order(self.stock_product, 5)

        self.assertEqual(len(po.invoice_ids), 1)
        bill = po.invoice_ids
        self.assertEqual(bill.move_type, 'in_invoice')
        self.assertEqual(bill.state, 'draft')
        self.assertEqual(self._product_line(bill).quantity, 5)
        self.assertEqual(po.picking_ids.auto_billing_state, 'not_processed')

    def test_purchase_confirmation_auto_post_sets_invoice_date(self):
        """An automatically posted confirmation bill has the required invoice date."""
        self.env.company.sab_auto_bill_on_purchase_confirmation = True
        self.env.company.sab_auto_post_bill = True
        po = self._create_purchase_order(self.stock_product, 5)

        bill = po.invoice_ids
        self.assertEqual(bill.state, 'posted')
        self.assertTrue(bill.invoice_date)

    def test_purchase_confirmation_can_validate_receipt(self):
        """The opt-in setting validates the receipt after purchase confirmation."""
        self.env.company.sab_auto_validate_receipt_on_purchase_confirmation = True
        po = self._create_purchase_order(self.stock_product, 5)

        self.assertEqual(po.picking_ids.state, 'done')

    def test_receipt_creates_vendor_bill(self):
        """استلام كامل ينشئ فاتورة مورد بالكمية المستلمة ويربطها بالاستلام وأمر الشراء."""
        # --- أمر شراء بكمية 5 ---
        po = self._create_purchase_order(self.stock_product, 5)
        # --- نعتمد الاستلام بالكمية الكاملة ---
        receipt = self._validate_picking(po.picking_ids, 5)

        # --- الاستلام مكتمل والفاتورة أُنشئت ---
        self.assertEqual(receipt.state, 'done')
        self.assertEqual(receipt.auto_billing_state, 'created')
        # --- فاتورة المورد مسودة ---
        self.assertTrue(receipt.auto_account_move_id)
        bill = receipt.auto_account_move_id
        self.assertEqual(bill.move_type, 'in_invoice')
        self.assertEqual(bill.state, 'draft')
        # --- الكمية والمنتج والسعر ---
        line = self._product_line(bill)
        self.assertEqual(line.product_id, self.stock_product)
        self.assertEqual(line.quantity, 5)
        self.assertEqual(line.price_unit, 60.0)
        # --- الارتباط بأمر الشراء والمورد ---
        self.assertEqual(bill.invoice_origin, po.name)
        self.assertIn(bill, po.invoice_ids)
        self.assertEqual(bill.partner_id, self.vendor_a)

    def test_auto_post_bill_setting_posts(self):
        """تفعيل الترحيل التلقائي يرحّل فاتورة المورد مباشرة."""
        # --- نفعّل الترحيل التلقائي لفواتير الموردين ---
        self.env.company.sab_auto_post_bill = True
        # --- أمر شراء واستلام كامل ---
        po = self._create_purchase_order(self.stock_product, 5)
        receipt = self._validate_picking(po.picking_ids, 5)
        # --- الحالة مرحلة والفاتورة مرحّلة ---
        self.assertEqual(receipt.auto_billing_state, 'posted')
        self.assertEqual(receipt.auto_account_move_id.state, 'posted')

    # ------------------------------------------------------------------
    # اختبارات مرتجعات العملاء
    # ------------------------------------------------------------------
    def test_posted_customer_credit_note_validates_matching_return(self):
        """A posted customer credit note returns only its credited quantity."""
        self.env.company.sab_auto_validate_customer_return_on_credit_note = True
        sale = self._create_sale_order(self.stock_product, 5)
        delivery = self._validate_picking(sale.picking_ids, 5)
        invoice = delivery.auto_account_move_id
        invoice.invoice_date = fields.Date.today()
        invoice.action_post()

        credit = invoice._reverse_moves(default_values_list=[{
            'invoice_date': fields.Date.today(),
        }])
        self._product_line(credit).quantity = 2
        credit.action_post()

        return_picking = self.env['stock.picking'].search([
            ('sab_credit_note_id', '=', credit.id),
        ])
        self.assertEqual(len(return_picking), 1)
        self.assertEqual(return_picking.state, 'done')
        self.assertEqual(return_picking.move_ids.quantity, 2)
        self.assertEqual(return_picking.auto_account_move_id, credit)
        self.assertEqual(return_picking.auto_billing_state, 'posted')

    def test_credit_note_generated_from_return_does_not_create_second_return(self):
        """Posting a return-generated credit note must not return stock twice."""
        self.env.company.sab_auto_validate_customer_return_on_credit_note = True
        self.env.company.sab_auto_post_credit_customer = True
        sale = self._create_sale_order(self.stock_product, 5)
        delivery = self._validate_picking(sale.picking_ids, 5)
        return_picking = self._create_return(delivery, 2)
        self._validate_picking(return_picking, 2)

        credit = return_picking.auto_account_move_id
        self.assertEqual(credit.state, 'posted')
        self.assertTrue(credit.sab_created_from_stock_return)
        self.assertFalse(self.env['stock.picking'].search([
            ('sab_credit_note_id', '=', credit.id),
        ]))

    def test_customer_return_creates_credit_note(self):
        """مرتجع عميل ينشئ إشعار دائن بالكمية المرتجعة ويربطه بالفاتورة الأصلية."""
        # --- تسليم كامل وإنشاء فاتورة ---
        so = self._create_sale_order(self.stock_product, 5)
        picking = self._validate_picking(so.picking_ids, 5)
        invoice = picking.auto_account_move_id

        # --- ننشئ مرتجعاً بكمية 2 ونعتمده ---
        return_pick = self._create_return(picking, 2)
        self._validate_picking(return_pick, 2)

        # --- الإشعار أُنشئ بالكمية المرتجعة ---
        self.assertEqual(return_pick.auto_billing_state, 'created')
        credit = return_pick.auto_account_move_id
        self.assertEqual(credit.move_type, 'out_refund')
        self.assertEqual(credit.state, 'draft')
        # --- الكمية والمنتج والسعر من الفاتورة الأصلية ---
        line = self._product_line(credit)
        self.assertEqual(line.product_id, self.stock_product)
        self.assertEqual(line.quantity, 2)
        self.assertEqual(line.price_unit, 100.0)
        # --- الارتباط بالفاتورة الأصلية والتسليم وأمر البيع ---
        self.assertEqual(credit.reversed_entry_id, invoice)
        self.assertEqual(credit.partner_id, self.partner_a)
        self.assertIn(credit, so.invoice_ids)
        # --- كمية الطلب تُخفَّض إلى الصافي (5 - 2 = 3) بعد المرتجع ---
        so_line = so.order_line
        so_line.invalidate_recordset(['product_uom_qty'])
        self.assertEqual(so_line.product_uom_qty, 3)

    def test_customer_return_without_invoice_fails(self):
        """مرتجع بلا فاتورة أصلية يُخزَّن كفشل مع رسالة واضحة دون منع الاعتماد."""
        # --- نعطل الفوترة التلقائية ثم نسلم (لا فاتورة) ---
        self.env.company.sab_auto_invoice_delivery = False
        so = self._create_sale_order(self.stock_product, 5)
        picking = self._validate_picking(so.picking_ids, 5)
        self.assertFalse(picking.auto_account_move_id)

        # --- ننشئ مرتجعاً ونعتمده ---
        return_pick = self._create_return(picking, 2)
        self._validate_picking(return_pick, 2)

        # --- الحالة فاشلة مع رسالة واضحة والتسليم ما زال مكتملاً ---
        self.assertEqual(return_pick.state, 'done')
        self.assertEqual(return_pick.auto_billing_state, 'failed')
        self.assertIn('No customer invoice found', return_pick.auto_billing_error)

    def test_order_based_return_no_phantom_to_invoice(self):
        """مرتجع لمنتج فوترة على الكمية المطلوبة لا يُظهر كمية فوترة وهمية بعد الإشعار الدائن."""
        # --- أمر بيع بمنتج فوترة على الكمية المطلوبة ---
        so = self._create_sale_order(self.order_product, 5)
        picking = self._validate_picking(so.picking_ids, 5)
        invoice = picking.auto_account_move_id
        # --- الفاتورة أُنشئت للكمية كاملة ---
        self.assertEqual(self._product_line(invoice).quantity, 5)
        # --- لا كمية فوترة متبقية قبل المرتجع ---
        self.assertEqual(so.order_line.qty_to_invoice, 0)
        self.assertEqual(so.invoice_status, 'invoiced')

        # --- مرتجع 2 مع إنشاء إشعار دائن ---
        return_pick = self._create_return(picking, 2)
        self._validate_picking(return_pick, 2)
        credit = return_pick.auto_account_move_id
        self.assertEqual(credit.move_type, 'out_refund')
        self.assertEqual(self._product_line(credit).quantity, 2)

        # --- الكميات بعد المرتجع: مسلَّم 3 ومفوتَر صافي 3 ---
        line = so.order_line
        line.invalidate_recordset(['qty_delivered', 'qty_invoiced', 'qty_to_invoice', 'invoice_status'])
        self.assertEqual(line.qty_delivered, 3)
        self.assertEqual(line.qty_invoiced, 3)
        # --- لا كمية فوترة وهمية: المتبقي للفوترة صفر والحالة مفوتَرة بالكامل ---
        self.assertEqual(line.qty_to_invoice, 0)
        self.assertEqual(so.invoice_status, 'invoiced')
        # --- كمية الطلب تُخفَّض إلى الصافي بعد المرتجع ---
        self.assertEqual(line.product_uom_qty, 3)

    # ------------------------------------------------------------------
    # اختبارات مرتجعات الموردين
    # ------------------------------------------------------------------
    def test_posted_vendor_credit_note_validates_matching_return(self):
        """A posted vendor credit note returns only its credited quantity."""
        self.env.company.sab_auto_validate_vendor_return_on_credit_note = True
        purchase = self._create_purchase_order(self.stock_product, 5)
        receipt = self._validate_picking(purchase.picking_ids, 5)
        bill = receipt.auto_account_move_id
        bill.invoice_date = fields.Date.today()
        bill.action_post()

        credit = bill._reverse_moves(default_values_list=[{
            'invoice_date': fields.Date.today(),
        }])
        self._product_line(credit).quantity = 2
        credit.action_post()

        return_picking = self.env['stock.picking'].search([
            ('sab_credit_note_id', '=', credit.id),
        ])
        self.assertEqual(len(return_picking), 1)
        self.assertEqual(return_picking.state, 'done')
        self.assertEqual(return_picking.move_ids.quantity, 2)
        self.assertEqual(return_picking.auto_account_move_id, credit)
        self.assertEqual(return_picking.auto_billing_state, 'posted')

    def test_vendor_return_creates_credit_note(self):
        """مرتجع مورد ينشئ إشعار دائن للمورد بالكمية المرتجعة ويربطه بالفاتورة الأصلية."""
        # --- استلام كامل وإنشاء فاتورة مورد ---
        po = self._create_purchase_order(self.stock_product, 5)
        receipt = self._validate_picking(po.picking_ids, 5)
        bill = receipt.auto_account_move_id

        # --- ننشئ مرتجعاً للمورد بكمية 2 ونعتمده ---
        return_pick = self._create_return(receipt, 2)
        self._validate_picking(return_pick, 2)

        # --- الإشعار أُنشئ بكمية سالبة (اتجاه المورد القياسي) ---
        self.assertEqual(return_pick.auto_billing_state, 'created')
        credit = return_pick.auto_account_move_id
        self.assertEqual(credit.move_type, 'in_refund')
        self.assertEqual(credit.state, 'draft')
        # --- الكمية سالبة والمنتج والسعر من فاتورة المورد الأصلية ---
        line = self._product_line(credit)
        self.assertEqual(line.product_id, self.stock_product)
        self.assertEqual(line.quantity, -2)
        self.assertEqual(line.price_unit, 60.0)
        # --- الارتباط بالفاتورة الأصلية ---
        self.assertEqual(credit.reversed_entry_id, bill)
        self.assertEqual(credit.partner_id, self.vendor_a)

    def test_delivery_after_return_invoices_remaining_delivered(self):
        """تسليم جزئي ثم مرتجع ثم تسليم الباقي: تُفوتر كل كمية مسلّمة صافية (بلا نقص)."""
        # --- أمر بيع بكمية 10 (فوترة على التسليم) ---
        so = self._create_sale_order(self.stock_product, 10)
        pick1 = so.picking_ids
        # --- نسلم 8 فقط وننشئ طلباً مؤجلاً للباقي ---
        self._validate_picking(pick1, 8)
        self.assertEqual(self._product_line(pick1.auto_account_move_id).quantity, 8)
        backorder = pick1.backorder_ids

        # --- مرتجع 2 من التسليم الأول ينشئ إشعار دائن ---
        return_pick = self._create_return(pick1, 2)
        self._validate_picking(return_pick, 2)
        credit = return_pick.auto_account_move_id
        self.assertEqual(credit.move_type, 'out_refund')
        self.assertEqual(self._product_line(credit).quantity, 2)

        # --- تسليم الباقي (2) يُفوتر بالكامل رغم وجود مرتجع سابق (لا نقص فوترة) ---
        self._validate_picking(backorder, 2)
        self.assertEqual(backorder.auto_billing_state, 'created')
        self.assertEqual(
            self._product_line(backorder.auto_account_move_id).quantity, 2
        )

        # --- إجمالي الفواتير 10 والمرتجع 2: الصافي مفوتَر = المسلّم الصافي ---
        invoiced = sum(
            l.quantity
            for inv in so.invoice_ids
            if inv.move_type == 'out_invoice'
            for l in self._product_line(inv)
        )
        self.assertEqual(invoiced, 10)

    def test_receipt_after_return_bills_remaining_received(self):
        """استلام جزئي ثم مرتجع مورد ثم استلام الباقي: تُفوتر كل كمية مستلمة صافية."""
        # --- أمر شراء بكمية 10 ---
        po = self._create_purchase_order(self.stock_product, 10)
        rec1 = po.picking_ids
        # --- نستلم 8 فقط وننشئ طلباً مؤجلاً ---
        self._validate_picking(rec1, 8)
        self.assertEqual(self._product_line(rec1.auto_account_move_id).quantity, 8)
        backorder = rec1.backorder_ids

        # --- مرتجع مورد 2 ينشئ إشعار دائن ---
        return_pick = self._create_return(rec1, 2)
        self._validate_picking(return_pick, 2)
        credit = return_pick.auto_account_move_id
        self.assertEqual(credit.move_type, 'in_refund')
        self.assertEqual(self._product_line(credit).quantity, -2)

        # --- استلام الباقي (2) يُفوتر بالكامل رغم وجود مرتجع سابق ---
        self._validate_picking(backorder, 2)
        self.assertEqual(backorder.auto_billing_state, 'created')
        self.assertEqual(
            self._product_line(backorder.auto_account_move_id).quantity, 2
        )

        # --- إجمالي فواتير المورد 10 والمرتجع 2: الصافي مفوتَر = المستلَم الصافي ---
        billed = sum(
            l.quantity
            for inv in po.invoice_ids
            if inv.move_type == 'in_invoice'
            for l in self._product_line(inv)
        )
        self.assertEqual(billed, 10)

    # ------------------------------------------------------------------
    # اختبارات الفشل وإعادة المحاولة
    # ------------------------------------------------------------------
    def test_failed_processing_keeps_picking_done(self):
        """فشل إنشاء الفاتورة يخزّن الخطأ ولا يمنع اعتماد التسليم."""
        # --- أمر بيع وتسليم ---
        so = self._create_sale_order(self.stock_product, 5)
        picking = so.picking_ids
        # --- نحاكي فشلاً في إنشاء الفاتورة ---
        with patch.object(
            StockPicking, '_create_customer_invoice_from_delivery',
            side_effect=Exception('boom'),
        ):
            self._validate_picking(picking, 5)
        # --- التسليم مكتمل والحالة فاشلة مع الخطأ ---
        self.assertEqual(picking.state, 'done')
        self.assertEqual(picking.auto_billing_state, 'failed')
        self.assertIn('boom', picking.auto_billing_error)
        self.assertFalse(picking.auto_account_move_id)

    def test_retry_after_failure(self):
        """إعادة المحاولة من مدير تعالج الفشل وتنشئ الفاتورة دون تكرار."""
        # --- أمر بيع وتسليم مع فشل محاكى ---
        so = self._create_sale_order(self.stock_product, 5)
        picking = so.picking_ids
        with patch.object(
            StockPicking, '_create_customer_invoice_from_delivery',
            side_effect=Exception('boom'),
        ):
            self._validate_picking(picking, 5)
        self.assertEqual(picking.auto_billing_state, 'failed')

        # --- المدير يعيد المحاولة فتنجح المعالجة ---
        picking.action_retry_auto_billing()
        self.assertEqual(picking.auto_billing_state, 'created')
        self.assertTrue(picking.auto_account_move_id)
        self.assertEqual(picking.auto_account_move_id.move_type, 'out_invoice')
        self.assertEqual(len(so.invoice_ids), 1)

    def test_retry_requires_manager(self):
        """غير المدير لا يستطيع إعادة محاولة الفوترة التلقائية."""
        # --- أمر بيع وتسليم مع فشل محاكى ---
        so = self._create_sale_order(self.stock_product, 5)
        picking = so.picking_ids
        with patch.object(
            StockPicking, '_create_customer_invoice_from_delivery',
            side_effect=Exception('boom'),
        ):
            self._validate_picking(picking, 5)
        self.assertEqual(picking.auto_billing_state, 'failed')

        # --- مستخدم عادي بدون صلاحيات مدير ---
        user = self.env['res.users'].create({
            'name': 'SAB Non Manager',
            'login': 'sab_non_manager',
            'group_ids': [
                Command.link(self.env.ref('base.group_user').id),
                Command.link(self.env.ref('stock.group_stock_user').id),
            ],
        })
        # --- إعادة المحاولة مرفوضة برسالة صلاحيات ---
        with self.assertRaises(UserError):
            picking.with_user(user).action_retry_auto_billing()

    # ------------------------------------------------------------------
    # اختبارات العزل بين الشركات
    # ------------------------------------------------------------------
    def test_multi_company_isolation(self):
        """الفاتورة تُنشأ داخل شركة التسليم ولا تتسرب بين الشركات."""
        # --- ننشئ شركة ثانية بدليل محاسبي ومستودع خاص بها ---
        company_data_2 = self.setup_other_company()
        company_2 = company_data_2['company']
        # --- ننشئ مستودعاً للشركة الثانية (مطلوب لتوليد التسليم) ---
        self.env['stock.warehouse'].with_company(company_2).create({
            'name': 'SAB Warehouse 2',
            'code': 'SAB2',
        })
        # --- منتج خاص بالشركة الثانية ---
        product_2 = self.env['product.product'].with_company(company_2).create({
            'name': 'SAB Product Company 2',
            'type': 'consu',
            'list_price': 100.0,
            'standard_price': 60.0,
            'categ_id': self.product_category.id,
            'company_id': company_2.id,
        })
        # --- عميل خاص بالشركة الثانية ---
        partner_2 = self.env['res.partner'].with_company(company_2).create({
            'name': 'SAB Customer Company 2',
            'company_id': False,
        })

        # --- أمر بيع داخل الشركة الثانية ---
        so2 = self.env['sale.order'].with_company(company_2).create({
            'company_id': company_2.id,
            'partner_id': partner_2.id,
            'partner_invoice_id': partner_2.id,
            'partner_shipping_id': partner_2.id,
            'order_line': [(0, 0, {
                'product_id': product_2.id,
                'product_uom_qty': 5,
                'price_unit': 100.0,
            })],
        })
        so2.action_confirm()

        # --- نعتمد التسليم داخل الشركة الثانية ---
        picking2 = self._validate_picking(
            so2.picking_ids.with_company(company_2), 5
        )
        # --- الفاتورة أُنشئت داخل الشركة الثانية ---
        self.assertEqual(picking2.auto_billing_state, 'created')
        self.assertEqual(picking2.auto_account_move_id.company_id, company_2)
        self.assertEqual(picking2.auto_account_move_id.partner_id, partner_2)
        # --- لا فاتورة في الشركة الأولى ---
        self.assertFalse(
            picking2.auto_account_move_id.company_id == self.env.company
        )

    # ------------------------------------------------------------------
    # اختبارات التجاهل والإعدادات
    # ------------------------------------------------------------------
    def test_internal_transfer_ignored(self):
        """التحويلات الداخلية تُتجاهل ولا تنشئ أي مستند محاسبي."""
        # --- ننشئ أمر بيع للحصول على مستودع الشركة المتسق معها ---
        so = self._create_sale_order(self.stock_product, 5)
        delivery = so.picking_ids
        # --- نأخذ مستودع التسليم وشركته لضمان الاتساق ---
        warehouse = delivery.picking_type_id.warehouse_id
        company = warehouse.company_id or self.env.company
        # --- ننشئ وجهة داخلية ضمن نفس مستودع الشركة ---
        dest = self.env['stock.location'].with_company(company).create({
            'name': 'SAB Test Destination',
            'usage': 'internal',
            'location_id': warehouse.view_location_id.id,
            'company_id': company.id,
        })
        # --- ننشئ تحويلاً داخلياً من نفس المستودع ---
        picking = self.env['stock.picking'].with_company(company).create({
            'company_id': company.id,
            'picking_type_id': warehouse.int_type_id.id,
            'location_id': warehouse.lot_stock_id.id,
            'location_dest_id': dest.id,
            'move_ids': [(0, 0, {
                'product_id': self.stock_product.id,
                'product_uom_qty': 5,
                'uom_id': self.stock_product.uom_id.id,
                'location_id': warehouse.lot_stock_id.id,
                'location_dest_id': dest.id,
            })],
        })
        picking.action_confirm()
        # --- نعتمد التحويل ---
        self._validate_picking(picking, 5)
        # --- الحالة غير قابلة للتطبيق ولا مستند محاسبي ---
        self.assertEqual(picking.state, 'done')
        self.assertEqual(picking.auto_billing_state, 'not_applicable')
        self.assertFalse(picking.auto_account_move_id)

    def test_settings_defaults(self):
        """شاشة الإعدادات تعرض القيم الافتراضية المرتبطة بالشركة."""
        # --- ننشئ سجل إعدادات للشركة الحالية ---
        settings = self.env['res.config.settings'].create({
            'company_id': self.env.company.id,
        })
        # --- القيم الافتراضية للإعدادات الثمانية ---
        self.assertTrue(settings.sab_auto_invoice_delivery)
        self.assertFalse(settings.sab_auto_post_invoice)
        self.assertFalse(settings.sab_auto_validate_delivery_on_sale_confirmation)
        self.assertTrue(settings.sab_auto_credit_customer_return)
        self.assertFalse(settings.sab_auto_post_credit_customer)
        self.assertFalse(settings.sab_auto_validate_customer_return_on_credit_note)
        self.assertTrue(settings.sab_auto_bill_receipt)
        self.assertFalse(settings.sab_auto_post_bill)
        self.assertFalse(settings.sab_auto_validate_receipt_on_purchase_confirmation)
        self.assertTrue(settings.sab_auto_credit_vendor_return)
        self.assertFalse(settings.sab_auto_post_credit_vendor)
        self.assertFalse(settings.sab_auto_validate_vendor_return_on_credit_note)
        self.assertTrue(settings.sab_reduce_ordered_qty_on_return)
