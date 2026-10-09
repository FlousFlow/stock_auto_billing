# -*- coding: utf-8 -*-
# --- تمديد بند أمر البيع لمنع ظهور كمية فوترة وهمية بعد المرتجع أو الإشعار الدائن ---
from odoo import api, models
from odoo.tools import float_compare, float_is_zero


class SaleOrderLine(models.Model):
    # --- توريث بند أمر البيع بدون نموذج جديد ---
    _inherit = 'sale.order.line'

    def _sab_invoiced_qty(self):
        """الكمية الإجمالية المفوتَرة (فواتير عملاء فقط) لهذا البند دون احتساب إشعارات الدائن.

        نعتمد الإجمالي (وليس الصافي) حتى لا يخلق المرتجع/الإشعار الدائن
        كميةَ فوترة وهمية في حساب "الكمية المتبقية للفوترة".
        """
        # --- نجمع كميات فواتير العملاء المرتبطة بهذا البند ---
        qty = 0.0
        for inv_line in self.invoice_lines:
            # --- نتجاهل السطور الملغاة وإشعارات الدائن وخطوط الضرائب والأقسام ---
            if (
                inv_line.move_id.state == 'cancel'
                or inv_line.move_id.move_type != 'out_invoice'
                or inv_line.display_type not in (False, 'product')
            ):
                continue
            # --- نحول الكمية لوحدة قياس بند أمر البيع ---
            qty += inv_line.product_uom_id._compute_quantity(
                inv_line.quantity, self.product_uom_id, round=False
            )
        # --- نرجع إجمالي الكمية المفوتَرة ---
        return qty

    @api.depends('qty_invoiced', 'qty_delivered', 'product_uom_qty', 'state')
    def _compute_qty_to_invoice(self):
        """حساب الكمية المتبقية للفوترة دون احتساب المرتجعات ككمية مستحقة.

        السلوك القياسي لأودو يستخدم الكمية المفوترة الصافية (بعد خصم الإشعارات)،
        فيظهر للمنتجات المفوترة على الكمية المطلوبة "كمية للفوترة" وهمية بعد
        مرتجع + إشعار دائن. نستخدم هنا الكمية الإجمالية المفوتَرة بدلاً من الصافية.
        """
        # --- مجموعة بنود الـ combo لمعالجتها في النهاية كما يفعل أودو ---
        combo_lines = set()
        # --- نلف على البنود ---
        for line in self:
            # --- نعالج فقط البنود المؤكدة غير العرضية ---
            if line.state == 'sale' and not line.display_type:
                if line.product_id.type == 'combo':
                    # --- بنود الـ combo تُعالج لاحقاً ---
                    combo_lines.add(line)
                elif line.product_id.invoice_policy == 'order':
                    # --- الفوترة على الكمية المطلوبة: المطلوب - الإجمالي المفوتَر ---
                    line.qty_to_invoice = max(
                        0.0, line.product_uom_qty - line._sab_invoiced_qty()
                    )
                else:
                    # --- الفوترة على الكمية المسلّمة: المسلّم - الإجمالي المفوتَر ---
                    line.qty_to_invoice = max(
                        0.0, line.qty_delivered - line._sab_invoiced_qty()
                    )
                # --- نضيف البند المرتبط ببند الـ combo إن وجد ---
                if line.combo_item_id and line.linked_line_id:
                    combo_lines.add(line.linked_line_id)
            else:
                # --- البنود غير المؤكدة أو العرضية: لا كمية للفوترة ---
                line.qty_to_invoice = 0
        # --- نعالج بنود الـ combo بنفس منطق أودو الأصلي ---
        for combo_line in combo_lines:
            if any(
                line.combo_item_id and line.qty_to_invoice
                for line in combo_line.linked_line_ids
            ):
                combo_line.qty_to_invoice = (
                    combo_line.product_uom_qty - combo_line.qty_invoiced
                )
            else:
                combo_line.qty_to_invoice = 0

    @api.depends('state', 'product_uom_qty', 'qty_delivered', 'qty_to_invoice', 'qty_invoiced')
    def _compute_invoice_status(self):
        """حساب حالة فوترة البند باعتماد الكمية الإجمالية المفوتَرة.

        السلوك القياسي لأودو يقارن الكمية المفوترة الصافية (بعد خصم الإشعارات)
        بالكمية المطلوبة، فيظهر البند "لا شيء للفوترة" بعد مرتجع + إشعار دائن
        رغم أن الكمية الصافية مفوتَرة بالكامل. نستخدم الإجمالي هنا ليكون الحكم صحيحاً.
        """
        # --- دقة الكمية من إعدادات أودو ---
        precision = self.env['decimal.precision'].precision_get('Product Unit')
        # --- نلف على البنود ---
        for line in self:
            if line.state != 'sale':
                # --- خارج حالة البيع: لا شيء للفوترة ---
                line.invoice_status = 'no'
            elif line.is_downpayment and line.untaxed_amount_to_invoice == 0:
                # --- دفعات مقدمة مسددة بالكامل ---
                line.invoice_status = 'invoiced'
            elif not float_is_zero(line.qty_to_invoice, precision_digits=precision):
                # --- توجد كمية متبقية للفوترة ---
                line.invoice_status = 'to invoice'
            elif (
                line.state == 'sale'
                and line.product_id.invoice_policy == 'order'
                and line.product_uom_qty >= 0.0
                and float_compare(
                    line.qty_delivered, line.product_uom_qty,
                    precision_digits=precision,
                ) == 1
            ):
                # --- تسليم أكثر من المطلوب (فرصة بيع إضافية) ---
                line.invoice_status = 'upselling'
            elif float_compare(
                line._sab_invoiced_qty(), line.product_uom_qty,
                precision_digits=precision,
            ) >= 0:
                # --- الكمية الإجمالية المفوتَرة تكفي للكمية المطلوبة ---
                line.invoice_status = 'invoiced'
            else:
                # --- لا شيء للفوترة ---
                line.invoice_status = 'no'
