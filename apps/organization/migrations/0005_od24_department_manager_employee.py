# M10 tamamlama — `hr.departments.manager_employee_id` (birim sorumlusu).
#
# Döngüsel bağımlılık nedeniyle ayrı migration: `departments` → `employees`
# (birim sorumlusu) ve `employees` → `departments` (çalışanın birimi). Tablolar
# `0001`/`0003` ile oluşturulur, bu alan en son eklenir.
#
# ANLAM (OD5 §5.5): `manager_employee_id` **birim sorumlusu**dur (onay
# yönlendirmesi). `employee_manager` (kim kime raporluyor) ile aynı şey
# **değildir** ve **yetki üretmez** — yetki yalnızca `user_roles` + kapsamdan gelir.
#
# Kiracı eşitliği `0007_org_same_tenant_guards` içindeki guard ile zorlanır.

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("organization", "0004_od24_employee_manager"),
    ]

    operations = [
        migrations.AddField(
            model_name="department",
            name="manager_employee",
            field=models.ForeignKey(
                blank=True,
                help_text=(
                    "Birim sorumlusu (onay yönlendirmesi). **Yetki üretmez**: bu bilgi "
                    "`employee_manager` ilişkisinden ve RBAC kapsamından ayrıdır (OD5 §5.5)."
                ),
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="managed_departments",
                to="organization.employee",
                verbose_name="birim sorumlusu",
            ),
        ),
    ]
