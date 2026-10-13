# -*- coding: utf-8 -*-
# --- استيراد تمديدات النماذج ---
from . import res_company  # إعدادات الشركة الخاصة بالفوترة التلقائية
from . import account_move  # وسم المستندات المنشأة عند تأكيد الطلب
from . import account_move_reversal  # ربط الفاتورة البديلة بإشعار الدائن القياسي
from . import res_config_settings  # شاشة الإعدادات
from . import stock_picking  # حقول ومنطق الفوترة التلقائية على الحركات المخزنية
from . import sale_order_line  # منع كمية الفوترة الوهمية بعد المرتجع
from . import sale_order  # إنشاء فاتورة البيع عند تأكيد أمر البيع اختيارياً
from . import purchase_order  # إنشاء فاتورة المورد عند تأكيد أمر الشراء اختيارياً
