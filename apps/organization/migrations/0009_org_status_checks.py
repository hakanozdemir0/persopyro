# M10–M13 — yaşam döngüsü **kapalı kümelerinin veritabanı düzeyinde** dayatılması.
#
# Django modelindeki `TextChoices` seçimleri yalnızca uygulama katmanında doğrulanır;
# ham SQL/yönetim yolları bunları atlayabilir. Projedeki mevcut desen
# (`roles_scope_type_valid`) izlenerek kapalı kümeler DB `CHECK` kısıtıyla da
# güvenceye alınır:
#
#   * `departments.status`            ∈ {active, inactive, archived}
#   * `employees.status`              ∈ {active, suspended, terminated, archived}
#     (docs/ORGANIZATION.md §7 — çalışan yaşam döngüsü)
#   * `employee_manager.relation_type` ∈ {primary, secondary, delegate}
#     (OD24 §4)
#
# Ayrı migration olarak eklenir çünkü `0001`/`0003`/`0004` bu turda uygulandı ve
# tablo oluşturma adımlarıyla karıştırılmaz.

from django.db import migrations, models

DEPARTMENTS_STATUS = ["active", "inactive", "archived"]
EMPLOYEES_STATUS = ["active", "suspended", "terminated", "archived"]
RELATION_TYPES = ["primary", "secondary", "delegate"]


class Migration(migrations.Migration):

    dependencies = [
        ("organization", "0008_employee_manager_active_guard"),
    ]

    operations = [
        migrations.AddConstraint(
            model_name="department",
            constraint=models.CheckConstraint(
                condition=models.Q(status__in=DEPARTMENTS_STATUS),
                name="departments_status_valid",
            ),
        ),
        migrations.AddConstraint(
            model_name="employee",
            constraint=models.CheckConstraint(
                condition=models.Q(status__in=EMPLOYEES_STATUS),
                name="employees_status_valid",
            ),
        ),
        migrations.AddConstraint(
            model_name="employeemanager",
            constraint=models.CheckConstraint(
                condition=models.Q(relation_type__in=RELATION_TYPES),
                name="employee_manager_relation_type_valid",
            ),
        ),
    ]
