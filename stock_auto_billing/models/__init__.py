# -*- coding: utf-8 -*-
# --- استيراد تمديدات النماذج ---
from . import res_company  # إعدادات الشركة الخاصة بالفوترة التلقائية
from . import res_config_settings  # شاشة الإعدادات
from . import stock_picking  # حقول ومنطق الفوترة التلقائية على الحركات المخزنية
from . import sale_order_line  # منع كمية الفوترة الوهمية بعد المرتجع
