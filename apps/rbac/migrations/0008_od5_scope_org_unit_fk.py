# M9 tamamlama (OD5 kapalı) — `hr.user_roles.scope_org_unit_id` artık **gerçek FK**.
#
# `0007_od5_user_role_scope` (M9 kısmi) kolonu ekledi ama organizasyon tablosu
# (M10) henüz yoktu; FK, "aktif birim" guard'ı ve alt ağaç çözümlemesi ertelenmişti.
#
# İSTENEN: "Eski geçici UUID kolonunu yeniden yaratma; mevcut alanı gerçek
# FK/model ilişkisine güvenli şekilde bağla."
#
# NEDEN ELLE (ve neden `SeparateDatabaseAndState`): Django'nun otomatik ürettiği
# migration bu değişikliği **`RemoveField` + `AddField`** olarak ifade eder → mevcut
# KOLON DÜŞÜRÜLÜR ve içindeki veri kaybolur. Oysa sütun adı ve tipi AYNI kalır
# (`scope_org_unit_id`, `uuid`); yalnızca alanın niteliği değişir
# (`UUIDField` → `ForeignKey`). Bu yüzden **state** değişimi ile **veritabanı**
# değişimi ayrıştırılır: veritabanında yalnızca FK kısıtı eklenir
# (`ALTER TABLE ... ADD CONSTRAINT ... FOREIGN KEY`), kısıtın SQL'i Django'nun
# üreteceği DDL ile birebir aynıdır (ad dahil).
#
# FK davranışı: `ON DELETE` verilmez (NO ACTION). Birim kayıtları **fiziksel olarak
# silinmez** (OD21 soft-delete; runtime rolünde `DELETE` yetkisi de yoktur) —
# silinmeye çalışılırsa atamalar yüzünden işlem **reddedilir** (sessiz atama kaybı yok).
#
# Kiracı eşitliği: `trg_user_roles_scope_org_unit_id_same_tenant`
# (`organization/0007_org_same_tenant_guards`).
# "Aktif birim" + kapsam zorunluluğu: `0009_od5_scope_guard_department`.

import django.db.models.deletion
from django.db import migrations, models

FK_NAME = "user_roles_scope_org_unit_id_d87bcc0d_fk_departments_id"

ADD_FK = f"""
ALTER TABLE hr.user_roles
    ADD CONSTRAINT {FK_NAME}
    FOREIGN KEY (scope_org_unit_id) REFERENCES hr.departments (id)
    DEFERRABLE INITIALLY DEFERRED;
"""

DROP_FK = f"ALTER TABLE hr.user_roles DROP CONSTRAINT IF EXISTS {FK_NAME};"


class Migration(migrations.Migration):

    dependencies = [
        ("rbac", "0007_od5_user_role_scope"),
        # Hedef tablo (hr.departments) M10 ile oluşur.
        ("organization", "0001_od24_departments_positions"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.RemoveConstraint(
                    model_name="userrole", name="user_roles_user_role_scope_uniq"
                ),
                migrations.RemoveField(model_name="userrole", name="scope_org_unit_id"),
                migrations.AddField(
                    model_name="userrole",
                    name="scope_org_unit",
                    field=models.ForeignKey(
                        blank=True,
                        help_text=(
                            "OD5: UNIT/UNIT_DIRECT tipli rollerde ZORUNLU (DB tetikleyicisi "
                            "dayatır); diğer tiplerde NULL olmalıdır. Yetki kapsamı bu birim "
                            "+ alt ağacıdır (OD24: yalnızca AKTİF birim; silinmiş birim "
                            "kapsam üretmez). M9 tamamlandı: gerçek FK (`hr.departments.id`), "
                            "ON DELETE RESTRICT."
                        ),
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="user_role_scopes",
                        to="organization.department",
                        verbose_name="organizasyon birimi kapsamı",
                    ),
                ),
                migrations.AddConstraint(
                    model_name="userrole",
                    constraint=models.UniqueConstraint(
                        fields=("user", "role", "scope_org_unit"),
                        name="user_roles_user_role_scope_uniq",
                        nulls_distinct=False,
                    ),
                ),
            ],
            database_operations=[
                migrations.RunSQL(ADD_FK, DROP_FK),
            ],
        ),
    ]
