# -*- coding: utf-8 -*-
# --- تمديد نموذج الحركة المخزنية (stock.picking) بحقول الفوترة التلقائية ومنطق المعالجة ---
import logging

from odoo import _, Command, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class StockPicking(models.Model):
    # --- توريث نموذج التسليم/الاستلام بدون نموذج جديد ---
    _inherit = 'stock.picking'

    # --- مجموعة صلاحيات قراءة المستند المحاسبي (فقط من يملك صلاحيات محاسبية للقراءة) ---
    _ACCOUNT_READ_GROUPS = (
        'account.group_account_readonly,'
        'account.group_account_invoice,'
        'account.group_account_manager'
    )
    # --- مجموعة المديرين المسموح لهم بإعادة محاولة الفوترة التلقائية ---
    _RETRY_GROUPS = (
        'sales_team.group_sale_manager,'
        'purchase.group_purchase_manager,'
        'account.group_account_manager,'
        'base.group_system'
    )

    # --- المستند المحاسبي المُنشأ تلقائياً والمرتبط بهذا التسليم ---
    auto_account_move_id = fields.Many2one(
        'account.move',
        string='Generated Accounting Document',
        copy=False,
        readonly=True,
        # --- مقيدة بصلاحيات المحاسبة حتى لا تنكسر شاشة التسليم لمستخدمي المخزون ---
        groups=_ACCOUNT_READ_GROUPS,
    )
    sab_credit_note_id = fields.Many2one(
        'account.move',
        string='Credit Note Driving This Return',
        copy=False,
        readonly=True,
        index=True,
        groups=_ACCOUNT_READ_GROUPS,
    )
    sab_replacement_invoice_id = fields.Many2one(
        'account.move',
        string='Replacement Invoice or Bill',
        copy=False,
        readonly=True,
        index=True,
        groups=_ACCOUNT_READ_GROUPS,
    )
    # --- حالة الفوترة التلقائية لهذا التسليم ---
    auto_billing_state = fields.Selection(
        [
            ('not_processed', 'Not Processed'),
            ('created', 'Created'),
            ('posted', 'Posted'),
            ('failed', 'Failed'),
            ('not_applicable', 'Not Applicable'),
        ],
        string='Automatic Billing Status',
        default='not_processed',
        copy=False,
        index=True,
    )
    # --- نص آخر خطأ حدث أثناء المعالجة التلقائية ---
    auto_billing_error = fields.Text(
        string='Automatic Billing Error',
        copy=False,
        readonly=True,
    )

    # ------------------------------------------------------------------
    # نقطة الدخول: التشغيل بعد اكتمال اعتماد الحركة
    # ------------------------------------------------------------------
    def _action_done(self):
        """تشغيل الفوترة التلقائية بعد اكتمال اعتماد الحركة بنجاح.

        نختار هذه النقطة لأن button_validate تستدعي _action_done في كل المسارات
        (الاعتماد المباشر، أو بعد معالجة نافذة الطلب المؤجل backorder)،
        أي أن المعالجة لا تبدأ إلا بعد اكتمال الاعتماد فعلياً.
        """
        # --- نستدعي الاعتماد الأصلي أولاً لإتمام الحركة المخزنية ---
        res = super()._action_done()
        # --- نشغل الفوترة التلقائية بعد اكتمال الاعتماد فقط ---
        self._auto_billing_process()
        # --- نرجع نتيجة الاعتماد الأصلية ---
        return res

    def _auto_validate_from_order_confirmation(self):
        """Validate eligible order pickings without creating partial stock moves.

        This is intentionally opt-in per company. Outgoing deliveries are only
        validated when all moves are reserved; incoming receipts can be completed
        directly because their source is a supplier location. Any missing stock,
        lot/serial data or other standard validation error is reported in the
        picking chatter and does not block order confirmation.
        """
        for picking in self.filtered(lambda record: record.state not in ('done', 'cancel')):
            company = picking.company_id
            enabled = (
                picking.picking_type_code == 'outgoing'
                and company.sab_auto_validate_delivery_on_sale_confirmation
            ) or (
                picking.picking_type_code == 'incoming'
                and company.sab_auto_validate_receipt_on_purchase_confirmation
            )
            if not enabled:
                continue

            try:
                with self.env.cr.savepoint():
                    picking = picking.sudo()
                    if picking.state == 'draft':
                        picking.with_context(skip_zero_demand_check=True).action_confirm()
                    picking.action_assign()
                    active_moves = picking.move_ids.filtered(
                        lambda move: move.state not in ('done', 'cancel')
                    )
                    if not active_moves:
                        continue
                    if (
                        picking.picking_type_code == 'outgoing'
                        and any(move.state != 'assigned' for move in active_moves)
                    ):
                        picking.message_post(body=_(
                            'Automatic validation was skipped because the full '
                            'delivery quantity is not available.'
                        ))
                        continue
                    for move in active_moves:
                        move.quantity = move.product_uom_qty
                    result = picking.with_context(skip_backorder=True).button_validate()
                    if isinstance(result, dict):
                        raise UserError(_(
                            'Automatic validation requires a manual stock operation '
                            'for this picking.'
                        ))
            except Exception as error:
                _logger.warning(
                    'Automatic stock validation failed for picking %s: %s',
                    picking.name, error, exc_info=True,
                )
                picking.sudo().message_post(body=_(
                    'Automatic stock validation was skipped: %s', error
                ))

    def _auto_billing_process(self):
        """معالجة الفوترة التلقائية للتسليمات المكتملة. لا تمنع اعتماد التسليم أبداً."""
        # --- نختار فقط التسليمات المكتملة وغير المعالجة ---
        pickings = self.filtered(
            lambda p: p.state == 'done' and p.auto_billing_state == 'not_processed'
        )
        # --- نلف على كل تسليم ونعالجه بشكل منعزل ---
        for picking in pickings:
            try:
                # --- نعزل فشل الفوترة في savepoint حتى لا يفشل اعتماد التسليم ---
                with self.env.cr.savepoint():
                    # --- ننفذ الإنشاء بصلاحيات مرتفعة محسوبة داخل حدود الشركة ---
                    picking.sudo()._auto_billing_process_document()
            except Exception as error:
                # --- نسجل الخطأ ولا نمنع اعتماد التسليم ---
                _logger.warning(
                    'Stock Auto Billing failed for picking %s: %s',
                    picking.name, error, exc_info=True,
                )
                try:
                    # --- نخزن حالة فاشلة مع رسالة واضحة ---
                    picking.sudo()._set_auto_billing_failed(error)
                except Exception as record_error:
                    # --- لو فشل تسجيل الخطأ نفسه نكتفي بالتسجيل في السجل ولا نمنع الاعتماد ---
                    _logger.exception(
                        'Stock Auto Billing could not record the failure for picking %s: %s',
                        picking.name, record_error,
                    )

    def _auto_billing_process_document(self):
        """تصنيف التسليم وإنشاء المستند المحاسبي المناسب (يُستدعى بصلاحيات مرتفعة)."""
        # --- نتأكد أننا نتعامل مع تسليم واحد ---
        self.ensure_one()
        if self.sab_credit_note_id:
            accounting_document = (
                self.sab_replacement_invoice_id or self.sab_credit_note_id
            )
            self.write({
                'auto_account_move_id': accounting_document.id,
                'auto_billing_state': 'posted'
                if accounting_document.state == 'posted' else 'created',
                'auto_billing_error': False,
            })
            return
        # --- نتجاهل العمليات الداخلية والتصنيع والجرد والخردة ---
        if self.picking_type_code not in ('incoming', 'outgoing'):
            # --- عملية غير مؤهلة للفوترة التلقائية ---
            self._mark_not_applicable()
            return

        # --- حركات مكتملة في هذا التسليم ---
        done_moves = self.move_ids.filtered(lambda m: m.state == 'done')
        # --- حركات مرتجع عميل (مرتجع تسليم مرتبط بأمر بيع) ---
        customer_return_moves = done_moves.filtered(
            lambda m: m.origin_returned_move_id and m.origin_returned_move_id.sale_line_id
        )
        # --- حركات مرتجع مورد (مرتجع استلام مرتبط بأمر شراء) ---
        vendor_return_moves = done_moves.filtered(
            lambda m: m.origin_returned_move_id and m.origin_returned_move_id.purchase_line_id
        )

        if self.picking_type_code == 'outgoing':
            # --- تسليم للعميل أو مرتجع لمورد ---
            if vendor_return_moves:
                # --- مرتجع لمورد: ننشئ إشعار دائن للمورد ---
                if self.company_id.sab_auto_credit_vendor_return:
                    self._create_vendor_credit_note_from_return()
                else:
                    # --- الإعداد معطل لهذه الشركة ---
                    self._mark_not_applicable(
                        _('Automatic vendor credit notes are disabled for this company.')
                    )
            elif self.sale_id:
                # --- تسليم مرتبط بأمر بيع: ننشئ فاتورة عميل ---
                existing_invoices = self._search_customer_invoices_for_lines(
                    self.sale_id.order_line
                ).filtered('sab_order_confirmation')
                if existing_invoices:
                    self._link_existing_account_move(existing_invoices.sorted('id')[-1])
                elif self.company_id.sab_auto_invoice_delivery:
                    self._create_customer_invoice_from_delivery()
                else:
                    # --- الإعداد معطل لهذه الشركة ---
                    self._mark_not_applicable(
                        _('Automatic customer invoices are disabled for this company.')
                    )
            else:
                # --- تسليم غير مرتبط بأمر بيع أو شراء ---
                self._mark_not_applicable()
        elif self.picking_type_code == 'incoming':
            # --- استلام من مورد أو مرتجع من عميل ---
            if customer_return_moves:
                # --- مرتجع من عميل: ننشئ إشعار دائن للعميل ---
                if self.company_id.sab_auto_credit_customer_return:
                    self._create_customer_credit_note_from_return()
                else:
                    # --- الإعداد معطل لهذه الشركة ---
                    self._mark_not_applicable(
                        _('Automatic customer credit notes are disabled for this company.')
                    )
            elif self.purchase_id:
                # --- استلام مرتبط بأمر شراء: ننشئ فاتورة مورد ---
                existing_bills = self._search_vendor_bills_for_lines(
                    self.purchase_id.order_line
                ).filtered('sab_order_confirmation')
                if existing_bills:
                    self._link_existing_account_move(existing_bills.sorted('id')[-1])
                elif self.company_id.sab_auto_bill_receipt:
                    self._create_vendor_bill_from_receipt()
                else:
                    # --- الإعداد معطل لهذه الشركة ---
                    self._mark_not_applicable(
                        _('Automatic vendor bills are disabled for this company.')
                    )
            else:
                # --- استلام غير مرتبط بأمر بيع أو شراء ---
                self._mark_not_applicable()

    # ------------------------------------------------------------------
    # فاتورة العميل من تسليم البيع
    # ------------------------------------------------------------------
    def _create_customer_invoice_from_delivery(self):
        """إنشاء فاتورة عميل للكميات المسلّمة في هذا التسليم فقط."""
        # --- الحركات المكتملة المرتبطة ببنود أمر البيع ---
        done_moves = self.move_ids.filtered(
            lambda m: m.state == 'done' and m.sale_line_id
        )
        if not done_moves:
            # --- لا توجد حركات فوترة في هذا التسليم ---
            self._mark_not_applicable(
                _('No delivered sale order line found in this picking.')
            )
            return None

        order = self.sale_id
        if not order or order.state not in ('sale', 'done'):
            # --- أمر البيع غير مؤكد أو ملغي ---
            self._mark_not_applicable(
                _('The related sale order is not confirmed.')
            )
            return None

        # --- نجمع الكميات المسلّمة في هذا التسليم حسب بند أمر البيع ---
        qty_by_line = {}
        for move in done_moves:
            line = move.sale_line_id
            # --- نحول الكمية لوحدة قياس بند أمر البيع ---
            qty = move.uom_id._compute_quantity(
                move.quantity, line.product_uom_id, round=False
            )
            # --- نجمع الكميات لنفس البند ---
            qty_by_line[line] = qty_by_line.get(line, 0.0) + qty

        # --- نبني بنود الفاتورة بالكميات المتبقية للفوترة فقط ---
        invoice_line_vals = []
        for line, qty_done in qty_by_line.items():
            # --- الكمية المتبقية = المسلّم إجمالاً - المفوتر إجمالاً + المخصوم من
            #     الإشعارات (الكمية المرتجعة لم تعد مفوتَرة صافياً فيعاد فوترتها
            #     عند إعادة تسليمها). هذا يمنع نقص الفوترة بعد مرتجع ثم تسليم جديد. ---
            qty_to_invoice = min(
                qty_done,
                max(
                    0.0,
                    line.qty_delivered
                    - self._sale_line_invoiced_qty(line)
                    + self._sale_line_refunded_qty(line),
                ),
            )
            if qty_to_invoice <= 0:
                continue
            # --- نحضّر بند الفاتورة من بند أمر البيع (سعر، خصم، ضرائب، تحليلي، عملة) ---
            line_vals = line._prepare_invoice_line()
            # --- نتجاهل بنود الأقسام (مثل منتجات الـ combo) ---
            if line_vals.get('display_type', 'product') != 'product':
                continue
            # --- نضبط الكمية على ما تم تسليمه في هذا التسليم فقط ---
            line_vals['quantity'] = qty_to_invoice
            invoice_line_vals.append(Command.create(line_vals))

        if not invoice_line_vals:
            # --- لا توجد كمية متبقية للفوترة (منع الفواتير المكررة) ---
            self._mark_not_applicable(
                _('All delivered quantities are already invoiced.')
            )
            return None

        # --- نحضّر رأس الفاتورة من أمر البيع (عميل، عملة، شروط دفع، مركز ضريبي) ---
        invoice_vals = order._prepare_invoice()
        invoice_vals['invoice_line_ids'] = invoice_line_vals

        # --- ننشئ الفاتورة بصلاحيات مرتفعة داخل شركة الأمر فقط ---
        invoice = (
            self.env['account.move']
            .sudo()
            .with_company(order.company_id)
            .with_context(default_move_type='out_invoice')
            .create(invoice_vals)
        )

        # --- نرحّل الفاتورة تلقائياً لو كان الإعداد مفعلاً ---
        posted = False
        if order.company_id.sab_auto_post_invoice:
            invoice.action_post()
            posted = True

        # --- نربط الفاتورة بالتسليم ونحدّث الحالة ---
        self.write({
            'auto_account_move_id': invoice.id,
            'auto_billing_state': 'posted' if posted else 'created',
            'auto_billing_error': False,
        })
        # --- نرجع الفاتورة المنشأة ---
        return invoice

    # ------------------------------------------------------------------
    # إشعار دائن للعميل من مرتجع
    # ------------------------------------------------------------------
    def _create_customer_credit_note_from_return(self):
        """إنشاء إشعار دائن للعميل للكميات المرتجعة في هذا المرتجع فقط."""
        # --- الحركات المكتملة المرتجعة المرتبطة بأمر بيع ---
        returned_moves = self.move_ids.filtered(
            lambda m: (
                m.state == 'done'
                and m.origin_returned_move_id
                and m.origin_returned_move_id.sale_line_id
            )
        )
        if not returned_moves:
            # --- لا توجد حركات مرتجعة قابلة للفوترة ---
            self._mark_not_applicable(
                _('No returned sale order line found in this picking.')
            )
            return None

        # --- نجمع الكميات المرتجعة حسب بند أمر البيع الأصلي ---
        qty_by_line = {}
        for move in returned_moves:
            line = move.origin_returned_move_id.sale_line_id
            # --- نحول الكمية لوحدة قياس بند أمر البيع ---
            qty = move.uom_id._compute_quantity(
                move.quantity, line.product_uom_id, round=False
            )
            # --- نجمع الكميات لنفس البند ---
            qty_by_line[line] = qty_by_line.get(line, 0.0) + qty

        # --- نبحث عن الفواتير الأصلية المرتبطة بالتسليمات الأصلية ---
        original_pickings = returned_moves.origin_returned_move_id.picking_id
        invoices = original_pickings.mapped('auto_account_move_id').filtered(
            lambda inv: inv.move_type == 'out_invoice' and inv.state != 'cancel'
        )
        # --- لو لم نجد فاتورة مرتبطة مباشرة نبحث عبر بنود أمر البيع ---
        if not invoices:
            # --- نجمع بنود أمر البيع في مجموعة سجلات صحيحة ---
            sale_lines = self.env['sale.order.line'].concat(*list(qty_by_line))
            invoices = self._search_customer_invoices_for_lines(sale_lines)

        if not invoices:
            # --- لا توجد فاتورة أصلية: مشكلة بيانات يجب أن يطلع عليها المدير ---
            raise UserError(
                _('No customer invoice found for the original delivery of picking %s.',
                  self.name)
            )

        # --- نبني بنود إشعار الدائن بالكميات القابلة للخصم فقط ---
        credit_line_vals = []
        for line, qty_returned in qty_by_line.items():
            # --- الكمية القابلة للخصم = المفوتر إجمالاً - المخصوم مسبقاً ---
            refundable = (
                self._sale_line_invoiced_qty(line) - self._sale_line_refunded_qty(line)
            )
            # --- لا نخصم أكثر مما تم إرجاعه أو أكثر من المتبقي للخصم ---
            qty_to_refund = min(qty_returned, max(0.0, refundable))
            if qty_to_refund <= 0:
                continue
            # --- نأخذ سطر الفاتورة الأصلي لننسخ قيمه (سعر، خصم، ضرائب، تحليلي) ---
            origin_line = self._get_invoice_line_for_sale_line(line, invoices)
            if not origin_line:
                # --- لا يوجد سطر فاتورة أصلي لهذا البند ---
                raise UserError(
                    _('No invoice line found for sale order line %s.',
                      line.display_name)
                )
            credit_line_vals.append(Command.create({
                'name': origin_line.name,
                'product_id': origin_line.product_id.id,
                'product_uom_id': origin_line.product_uom_id.id,
                'quantity': qty_to_refund,
                'price_unit': origin_line.price_unit,
                'discount': origin_line.discount,
                'tax_ids': [Command.set(origin_line.tax_ids.ids)],
                'analytic_distribution': origin_line.analytic_distribution,
                # --- نربط البند ببند أمر البيع لتتبع الكميات المخصومة ---
                'sale_line_ids': [Command.link(line.id)],
            }))

        if not credit_line_vals:
            # --- كل الكميات مخصومة مسبقاً: منع الإشعارات المكررة ---
            self._mark_not_applicable(
                _('All returned quantities are already refunded.')
            )
            return None

        # --- نأخذ أول فاتورة أصلية كمرجع لرأس الإشعار ---
        invoice = invoices[0]
        credit_vals = {
            'move_type': 'out_refund',
            'sab_created_from_stock_return': True,
            'partner_id': invoice.partner_id.id,
            'partner_shipping_id': invoice.partner_shipping_id.id,
            'currency_id': invoice.currency_id.id,
            'fiscal_position_id': invoice.fiscal_position_id.id,
            'invoice_payment_term_id': invoice.invoice_payment_term_id.id,
            # --- نربط الإشعار بأسماء الفواتير الأصلية (نتجاهل الأسماء الفارغة) ---
            'invoice_origin': ', '.join(
                n for n in invoices.mapped('name') if n
            ),
            'journal_id': invoice.journal_id.id,
            'company_id': invoice.company_id.id,
            # --- نربط الإشعار بالفاتورة الأصلية (العلاقة القياسية في أودو) ---
            'reversed_entry_id': invoice.id,
            'invoice_line_ids': credit_line_vals,
        }

        # --- ننشئ إشعار الدائن بصلاحيات مرتفعة داخل شركة الفاتورة ---
        credit = (
            self.env['account.move']
            .sudo()
            .with_company(invoice.company_id)
            .with_context(default_move_type='out_refund')
            .create(credit_vals)
        )

        # --- نرحّل الإشعار تلقائياً لو كان الإعداد مفعلاً ---
        posted = False
        if invoice.company_id.sab_auto_post_credit_customer:
            credit.action_post()
            posted = True

        # --- نربط الإشعار بالمرتجع ونحدّث الحالة ---
        self.write({
            'auto_account_move_id': credit.id,
            'auto_billing_state': 'posted' if posted else 'created',
            'auto_billing_error': False,
        })
        # --- ننقص كمية الطلب في بنود أمر البيع بمقدار الكمية المرتجعة (حسب إعداد الشركة) ---
        if self.company_id.sab_reduce_ordered_qty_on_return:
            self._reduce_ordered_qty_on_return(qty_by_line)
        # --- نرجع الإشعار المنشأ ---
        return credit

    def _reduce_ordered_qty_on_return(self, qty_by_line):
        """تقليل كمية الطلب في بنود أمر البيع بمقدار الكمية المرتجعة بعد المرتجع."""
        # --- نلف على كل بند أمر بيع وكميته المرتجعة ---
        for line, qty_returned in qty_by_line.items():
            # --- الكمية الجديدة لا تنزل تحت الصفر ---
            new_qty = max(0.0, line.product_uom_qty - qty_returned)
            # --- نكتب الكمية المحدثة في بند أمر البيع ---
            line.write({'product_uom_qty': new_qty})

    # ------------------------------------------------------------------
    # فاتورة المورد من استلام المشتريات
    # ------------------------------------------------------------------
    def _create_vendor_bill_from_receipt(self):
        """إنشاء فاتورة مورد للكميات المستلمة في هذا الاستلام فقط."""
        # --- الحركات المكتملة المرتبطة ببنود أمر الشراء ---
        done_moves = self.move_ids.filtered(
            lambda m: m.state == 'done' and m.purchase_line_id
        )
        if not done_moves:
            # --- لا توجد حركات فوترة في هذا الاستلام ---
            self._mark_not_applicable(
                _('No received purchase order line found in this picking.')
            )
            return None

        order = self.purchase_id
        if not order or order.state != 'purchase':
            # --- أمر الشراء غير مؤكد أو ملغي ---
            self._mark_not_applicable(
                _('The related purchase order is not confirmed.')
            )
            return None

        # --- نجمع الكميات المستلمة في هذا الاستلام حسب بند أمر الشراء ---
        qty_by_line = {}
        for move in done_moves:
            line = move.purchase_line_id
            # --- نحول الكمية لوحدة قياس بند أمر الشراء ---
            qty = move.uom_id._compute_quantity(
                move.quantity, line.uom_id, round=False
            )
            # --- نجمع الكميات لنفس البند ---
            qty_by_line[line] = qty_by_line.get(line, 0.0) + qty

        # --- نبني بنود فاتورة المورد بالكميات المتبقية للفوترة فقط ---
        bill_line_vals = []
        for line, qty_received in qty_by_line.items():
            # --- الكمية المتبقية = المستلم إجمالاً - المفوتر إجمالاً + المخصوم من
            #     الإشعارات (الكمية المرتجعة لم تعد مفوتَرة صافياً فيعاد فوترتها
            #     عند إعادة استلامها). هذا يمنع نقص الفوترة بعد مرتجع ثم استلام جديد. ---
            qty_to_bill = min(
                qty_received,
                max(
                    0.0,
                    line.qty_received
                    - self._po_line_billed_qty(line)
                    + self._po_line_refunded_qty(line),
                ),
            )
            if qty_to_bill <= 0:
                continue
            # --- نحضّر بند الفاتورة من بند أمر الشراء (سعر، خصم، ضرائب، تحليلي) ---
            line_vals = line._prepare_account_move_line()
            # --- نتجاهل بنود الأقسام ---
            if line_vals.get('display_type', 'product') != 'product':
                continue
            # --- نضبط الكمية على ما تم استلامه في هذا الاستلام فقط ---
            line_vals['quantity'] = qty_to_bill
            bill_line_vals.append(Command.create(line_vals))

        if not bill_line_vals:
            # --- لا توجد كمية متبقية للفوترة (منع الفواتير المكررة) ---
            self._mark_not_applicable(
                _('All received quantities are already billed.')
            )
            return None

        # --- نحضّر رأس فاتورة المورد من أمر الشراء (مورد، عملة، شروط دفع، مركز ضريبي) ---
        bill_vals = order._prepare_invoice()
        # --- تاريخ الفاتورة مطلوب لترحيل فواتير الموردين ---
        bill_vals['invoice_date'] = fields.Date.today()
        bill_vals['invoice_line_ids'] = bill_line_vals

        # --- ننشئ فاتورة المورد بصلاحيات مرتفعة داخل شركة الأمر ---
        bill = (
            self.env['account.move']
            .sudo()
            .with_company(order.company_id)
            .with_context(default_move_type='in_invoice')
            .create(bill_vals)
        )

        # --- نرحّل فاتورة المورد تلقائياً لو كان الإعداد مفعلاً ---
        posted = False
        if order.company_id.sab_auto_post_bill:
            bill.action_post()
            posted = True

        # --- نربط فاتورة المورد بالاستلام ونحدّث الحالة ---
        self.write({
            'auto_account_move_id': bill.id,
            'auto_billing_state': 'posted' if posted else 'created',
            'auto_billing_error': False,
        })
        # --- نرجع الفاتورة المنشأة ---
        return bill

    # ------------------------------------------------------------------
    # إشعار دائن للمورد من مرتجع للمورد
    # ------------------------------------------------------------------
    def _create_vendor_credit_note_from_return(self):
        """إنشاء إشعار دائن للمورد للكميات المرتجعة في هذا المرتجع فقط."""
        # --- الحركات المكتملة المرتجعة المرتبطة بأمر شراء ---
        returned_moves = self.move_ids.filtered(
            lambda m: (
                m.state == 'done'
                and m.origin_returned_move_id
                and m.origin_returned_move_id.purchase_line_id
            )
        )
        if not returned_moves:
            # --- لا توجد حركات مرتجعة قابلة للفوترة ---
            self._mark_not_applicable(
                _('No returned purchase order line found in this picking.')
            )
            return None

        # --- نجمع الكميات المرتجعة حسب بند أمر الشراء الأصلي ---
        qty_by_line = {}
        for move in returned_moves:
            line = move.origin_returned_move_id.purchase_line_id
            # --- نحول الكمية لوحدة قياس بند أمر الشراء ---
            qty = move.uom_id._compute_quantity(
                move.quantity, line.uom_id, round=False
            )
            # --- نجمع الكميات لنفس البند ---
            qty_by_line[line] = qty_by_line.get(line, 0.0) + qty

        # --- نبحث عن فواتير المورد الأصلية المرتبطة بالاستلامات الأصلية ---
        original_pickings = returned_moves.origin_returned_move_id.picking_id
        bills = original_pickings.mapped('auto_account_move_id').filtered(
            lambda inv: inv.move_type == 'in_invoice' and inv.state != 'cancel'
        )
        # --- لو لم نجد فاتورة مرتبطة مباشرة نبحث عبر بنود أمر الشراء ---
        if not bills:
            # --- نجمع بنود أمر الشراء في مجموعة سجلات صحيحة ---
            po_lines = self.env['purchase.order.line'].concat(*list(qty_by_line))
            bills = self._search_vendor_bills_for_lines(po_lines)

        if not bills:
            # --- لا توجد فاتورة مورد أصلية: مشكلة بيانات ---
            raise UserError(
                _('No vendor bill found for the original receipt of picking %s.',
                  self.name)
            )

        # --- نبني بنود إشعار الدائن بالكميات القابلة للخصم فقط ---
        credit_line_vals = []
        for line, qty_returned in qty_by_line.items():
            # --- الكمية القابلة للخصم = المفوتر إجمالاً - المخصوم مسبقاً ---
            refundable = (
                self._po_line_billed_qty(line) - self._po_line_refunded_qty(line)
            )
            # --- لا نخصم أكثر مما تم إرجاعه أو أكثر من المتبقي للخصم ---
            qty_to_refund = min(qty_returned, max(0.0, refundable))
            if qty_to_refund <= 0:
                continue
            # --- نأخذ سطر فاتورة المورد الأصلية لننسخ قيمه ---
            origin_line = self._get_bill_line_for_purchase_line(line, bills)
            if not origin_line:
                # --- لا يوجد سطر فاتورة مورد أصلي لهذا البند ---
                raise UserError(
                    _('No bill line found for purchase order line %s.',
                      line.display_name)
                )
            # --- بنود إشعار الدائن للمورد بكمية سالبة (الاتجاه القياسي في أودو) ---
            credit_line_vals.append(Command.create({
                'name': origin_line.name,
                'product_id': origin_line.product_id.id,
                'product_uom_id': origin_line.product_uom_id.id,
                'quantity': -qty_to_refund,
                'price_unit': origin_line.price_unit,
                'discount': origin_line.discount,
                'tax_ids': [Command.set(origin_line.tax_ids.ids)],
                'analytic_distribution': origin_line.analytic_distribution,
                # --- نربط البند ببند أمر الشراء لتتبع الكميات المخصومة ---
                'purchase_line_id': line.id,
            }))

        if not credit_line_vals:
            # --- كل الكميات مخصومة مسبقاً: منع الإشعارات المكررة ---
            self._mark_not_applicable(
                _('All returned quantities are already refunded.')
            )
            return None

        # --- نأخذ أول فاتورة مورد أصلية كمرجع لرأس الإشعار ---
        bill = bills[0]
        credit_vals = {
            'move_type': 'in_refund',
            'sab_created_from_stock_return': True,
            'partner_id': bill.partner_id.id,
            'currency_id': bill.currency_id.id,
            'fiscal_position_id': bill.fiscal_position_id.id,
            'invoice_payment_term_id': bill.invoice_payment_term_id.id,
            # --- نربط الإشعار بأسماء الفواتير الأصلية (نتجاهل الأسماء الفارغة) ---
            'invoice_origin': ', '.join(
                n for n in bills.mapped('name') if n
            ),
            'journal_id': bill.journal_id.id,
            'company_id': bill.company_id.id,
            # --- تاريخ الإشعار مطلوب لترحيل إشعارات الدائن للموردين ---
            'invoice_date': fields.Date.today(),
            # --- نربط الإشعار بالفاتورة الأصلية ---
            'reversed_entry_id': bill.id,
            'invoice_line_ids': credit_line_vals,
        }

        # --- ننشئ إشعار الدائن بصلاحيات مرتفعة داخل شركة الفاتورة ---
        credit = (
            self.env['account.move']
            .sudo()
            .with_company(bill.company_id)
            .with_context(default_move_type='in_refund')
            .create(credit_vals)
        )

        # --- نرحّل الإشعار تلقائياً لو كان الإعداد مفعلاً ---
        posted = False
        if bill.company_id.sab_auto_post_credit_vendor:
            credit.action_post()
            posted = True

        # --- نربط الإشعار بالمرتجع ونحدّث الحالة ---
        self.write({
            'auto_account_move_id': credit.id,
            'auto_billing_state': 'posted' if posted else 'created',
            'auto_billing_error': False,
        })
        # --- نرجع الإشعار المنشأ ---
        return credit

    # ------------------------------------------------------------------
    # أدوات حساب الكميات (منع إعادة الفوترة والخصم المزدوج)
    # ------------------------------------------------------------------
    def _sale_line_invoiced_qty(self, sale_line):
        """الكمية الإجمالية المفوتَرة (فواتير عملاء) لبند أمر بيع دون احتساب الإشعارات."""
        # --- نستدعي الميثود الموحد على بند أمر البيع ---
        return sale_line._sab_invoiced_qty()

    def _sale_line_refunded_qty(self, sale_line):
        """الكمية الإجمالية المخصومة (إشعارات دائن عملاء) لبند أمر بيع."""
        # --- نجمع كميات إشعارات الدائن المرتبطة ببند أمر البيع ---
        qty = 0.0
        for inv_line in sale_line.invoice_lines:
            # --- نتجاهل السطور الملغاة وخطوط الضرائب والأقسام (سطر المنتج فقط) ---
            if (
                inv_line.move_id.state == 'cancel'
                or inv_line.move_id.move_type != 'out_refund'
                or inv_line.display_type not in (False, 'product')
            ):
                continue
            # --- نحول الكمية ونتجاهل الإشارة السالبة ---
            qty += abs(
                inv_line.product_uom_id._compute_quantity(
                    inv_line.quantity, sale_line.product_uom_id, round=False
                )
            )
        # --- نرجع إجمالي الكمية المخصومة ---
        return qty

    def _po_line_billed_qty(self, po_line):
        """الكمية الإجمالية المفوتَرة (فواتير مورد) لبند أمر شراء دون احتساب الإشعارات."""
        # --- نجمع كميات فواتير المورد المرتبطة ببند أمر الشراء ---
        qty = 0.0
        for inv_line in po_line.invoice_lines:
            # --- نتجاهل السطور الملغاة وخطوط الضرائب والأقسام (سطر المنتج فقط) ---
            if (
                inv_line.move_id.state == 'cancel'
                or inv_line.move_id.move_type != 'in_invoice'
                or inv_line.display_type not in (False, 'product')
            ):
                continue
            # --- نحول الكمية لوحدة قياس بند أمر الشراء ---
            qty += inv_line.product_uom_id._compute_quantity(
                inv_line.quantity, po_line.uom_id, round=False
            )
        # --- نرجع إجمالي الكمية المفوتَرة ---
        return qty

    def _po_line_refunded_qty(self, po_line):
        """الكمية الإجمالية المخصومة (إشعارات دائن مورد) لبند أمر شراء."""
        # --- نجمع كميات إشعارات الدائن المرتبطة ببند أمر الشراء ---
        qty = 0.0
        for inv_line in po_line.invoice_lines:
            # --- نتجاهل السطور الملغاة وخطوط الضرائب والأقسام (سطر المنتج فقط) ---
            if (
                inv_line.move_id.state == 'cancel'
                or inv_line.move_id.move_type != 'in_refund'
                or inv_line.display_type not in (False, 'product')
            ):
                continue
            # --- نحول الكمية ونتجاهل الإشارة السالبة ---
            qty += abs(
                inv_line.product_uom_id._compute_quantity(
                inv_line.quantity, po_line.uom_id, round=False
                )
            )
        # --- نرجع إجمالي الكمية المخصومة ---
        return qty

    def _get_invoice_line_for_sale_line(self, sale_line, invoices):
        """إيجاد أول سطر فاتورة عميل أصلي مرتبط ببند أمر بيع."""
        # --- نلف على سطور الفواتير المحددة ---
        for inv_line in invoices.line_ids:
            # --- نتأكد أنه سطر منتج غير ملغي ومرتبط بهذا البند ---
            if (
                inv_line.display_type in (False, 'product')
                and inv_line.move_id.state != 'cancel'
                and inv_line.move_id.move_type == 'out_invoice'
                and sale_line in inv_line.sale_line_ids
            ):
                # --- نرجع أول سطر مطابق ---
                return inv_line
        # --- لا يوجد سطر مطابق ---
        return self.env['account.move.line']

    def _get_bill_line_for_purchase_line(self, po_line, bills):
        """إيجاد أول سطر فاتورة مورد أصلي مرتبط ببند أمر شراء."""
        # --- نلف على سطور فواتير المورد المحددة ---
        for inv_line in bills.line_ids:
            # --- نتأكد أنه سطر منتج غير ملغي ومرتبط بهذا البند ---
            if (
                inv_line.display_type in (False, 'product')
                and inv_line.move_id.state != 'cancel'
                and inv_line.move_id.move_type == 'in_invoice'
                and inv_line.purchase_line_id == po_line
            ):
                # --- نرجع أول سطر مطابق ---
                return inv_line
        # --- لا يوجد سطر مطابق ---
        return self.env['account.move.line']

    def _search_customer_invoices_for_lines(self, sale_lines):
        """بحث احتياطي عن فواتير العملاء المرتبطة ببنود أمر البيع المحددة."""
        # --- نبحث عن سطور فواتير عملاء مرتبطة بالبنود ---
        lines = self.env['account.move.line'].sudo().search([
            ('sale_line_ids', 'in', sale_lines.ids),
            ('move_id.move_type', '=', 'out_invoice'),
            ('move_id.state', '!=', 'cancel'),
            ('display_type', 'in', [False, 'product']),
        ])
        # --- نرجع الفواتير المرتبطة ---
        return lines.mapped('move_id')

    def _search_vendor_bills_for_lines(self, po_lines):
        """بحث احتياطي عن فواتير المورد المرتبطة ببنود أمر الشراء المحددة."""
        # --- نبحث عن سطور فواتير مورد مرتبطة بالبنود ---
        lines = self.env['account.move.line'].sudo().search([
            ('purchase_line_id', 'in', po_lines.ids),
            ('move_id.move_type', '=', 'in_invoice'),
            ('move_id.state', '!=', 'cancel'),
            ('display_type', 'in', [False, 'product']),
        ])
        # --- نرجع الفواتير المرتبطة ---
        return lines.mapped('move_id')

    # ------------------------------------------------------------------
    # إدارة الحالة والخطأ
    # ------------------------------------------------------------------
    def _link_existing_account_move(self, move):
        """Link an invoice/bill created at order confirmation to this picking."""
        self.ensure_one()
        self.write({
            'auto_account_move_id': move.id,
            'auto_billing_state': 'posted' if move.state == 'posted' else 'created',
            'auto_billing_error': False,
        })

    def _mark_not_applicable(self, message=''):
        """تحديد أن الفوترة التلقائية غير قابلة للتطبيق على هذا التسليم."""
        # --- نكتب الحالة ورسالة توضيحية (اختيارية) ---
        self.write({
            'auto_billing_state': 'not_applicable',
            'auto_billing_error': message or False,
        })

    def _set_auto_billing_failed(self, error):
        """تخزين حالة فاشلة مع رسالة واضحة وإشعار في محادثة التسليم."""
        # --- نخزن رسالة الخطأ والحالة الفاشلة ---
        self.write({
            'auto_billing_state': 'failed',
            'auto_billing_error': _('Automatic billing failed: %s', error),
        })
        # --- ننشر رسالة واضحة في محادثة التسليم لتظهر للمستخدم (إشعار واضح) ---
        self.message_post(
            body=_(
                'Automatic billing failed while creating the accounting document. '
                'The picking has been validated, but no accounting document was '
                'created. Error: %s',
                error,
            ),
            subject=_('Automatic Billing Failed'),
        )

    # ------------------------------------------------------------------
    # أزرار الواجهة
    # ------------------------------------------------------------------
    def action_retry_auto_billing(self):
        """إعادة محاولة الفوترة التلقائية للتسليمات الفاشلة (للمديرين فقط)."""
        # --- نتحقق أن المستخدم من المديرين المسموح لهم بإعادة المحاولة ---
        user = self.env.user
        # --- نفحص كل مجموعة مدير من مجموعة _RETRY_GROUPS ---
        if not any(user.has_group(group.strip()) for group in self._RETRY_GROUPS.split(',')):
            # --- نرفض إعادة المحاولة لغير المديرين ---
            raise UserError(
                _('Only Sales, Purchase, Accounting or System managers can retry '
                  'automatic billing.')
            )

        # --- نمنع إعادة المعالجة على مستندات تم إنشاؤها أو ترحيلها (منع التكرار) ---
        already_processed = self.filtered(
            lambda p: p.auto_billing_state in ('created', 'posted')
        )
        if already_processed:
            # --- نرفض إعادة المعالجة لأن المستند موجود بالفعل ---
            raise UserError(
                _('Automatic billing was already processed for %s. Retry is only '
                  'possible for failed or unprocessed pickings.',
                  ', '.join(already_processed.mapped('name')))
            )

        # --- نعيد تعيين الحالة للمعالجة من جديد (الفاشل وغير المعالج فقط) ---
        self.filtered(
            lambda p: p.auto_billing_state == 'failed'
        ).write({
            'auto_billing_state': 'not_processed',
            'auto_billing_error': False,
        })
        # --- نعيد تشغيل المعالجة (عملية آمنة لا تنشئ مكررات) ---
        self._auto_billing_process()
        # --- نرجع نجاح العملية ---
        return True

    def action_view_auto_account_move(self):
        """فتح المستند المحاسبي المُنشأ لهذا التسليم (الزر الذكي)."""
        # --- نتأكد أننا نتعامل مع تسليم واحد ---
        self.ensure_one()
        if not self.auto_account_move_id:
            # --- لا يوجد مستند محاسبي بعد ---
            return {'type': 'ir.actions.act_window_close'}
        # --- نفتح المستند المحاسبي في شاشة العرض ---
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'account.move',
            'view_mode': 'form',
            'res_id': self.auto_account_move_id.id,
            'view_id': self.env.ref('account.view_move_form').id,
            'target': 'current',
        }
