# M8 (OD5 kapalı ✅) — `hr.roles.scope_type`: rolün azami organizasyon kapsamı.
#
# `scope_type` **izin üretmez**; izin ∧ kapsam kararının "kapsam" eksenini taşır
# (docs/AUTHORIZATION.md §4.4 + OD5 §5.1). `role_type` (system/default/custom) ile
# karıştırılmaz.
#
# Kapalı küme: `TENANT`, `UNIT`, `UNIT_DIRECT`, `ASSIGNED`, `SELF` — §4.4'teki
# kodlardan gelir. **`SYS` hariçtir**: §4.4 `SYS`i yalnızca **platform** rollerine
# verir; platform rolleri `platform.roles`'ta yaşar (OD3) ve `hr.roles`'a taşınmaz.
#
# Backfill (dokümandan, tahmin edilmeden):
#   * `tenant_admin`   → `TENANT`  (§6.1 ŞY sınırı TENANT)
#   * `tenant_officer` → `TENANT`  (§6.1 ŞYT sınırı TENANT, modül kısıtlı)
#   * `hr_manager`     → `TENANT`  (§4.2: şablon rol; tüm izinleri matriste TENANT)
#   * `dept_manager`   → `UNIT`    (§6.1 DY sınırı UNIT — alt ağaç, OD5)
#   * `employee`       → `SELF`    (§6.1 ÇALIŞAN sınırı SELF)
#   * diğer kodlar (kiracının oluşturduğu **custom** roller) → `TENANT`
#     (§4.1: custom rolün kapsamı "Kiracı"; kiracı kendi izin kümesinin dışına
#     çıkamaz. Bireysel daraltma atama düzeyinde yapılır.)
#   Eşlenen custom roller **raporlanır** (sessiz toplu karar verilmez).
#
# Geri alma: kolon ve kısıt düşürülür (veri kaybı: yalnızca kapsam etiketi).

from django.db import migrations, models

SCOPE_BY_CODE = {
    "tenant_admin": "TENANT",
    "tenant_officer": "TENANT",
    "hr_manager": "TENANT",
    "dept_manager": "UNIT",
    "employee": "SELF",
}

#: Bilinen kod dışındaki (custom) rollerin varsayılan kapsamı — §4.1.
CUSTOM_DEFAULT_SCOPE = "TENANT"


def kapsamlari_doldur(apps, schema_editor):  # noqa: ARG001
    with schema_editor.connection.cursor() as cur:
        cur.execute("SELECT tenant_id, slug FROM hr.tenant_directory ORDER BY slug")
        for tenant_id, slug in cur.fetchall():
            cur.execute("SELECT set_config('app.tenant_id', %s, true)", [str(tenant_id)])

            for kod, kapsam in SCOPE_BY_CODE.items():
                cur.execute(
                    "UPDATE hr.roles SET scope_type = %s, updated_at = now()"
                    " WHERE code = %s AND scope_type IS DISTINCT FROM %s",
                    [kapsam, kod, kapsam],
                )

            # Custom / bilinmeyen kodlar: varsayılan kapsam + **rapor**.
            bilinen = tuple(SCOPE_BY_CODE)
            cur.execute(
                "SELECT code FROM hr.roles WHERE code <> ALL(%s) ORDER BY code", [list(bilinen)]
            )
            digerleri = [satir[0] for satir in cur.fetchall()]
            if digerleri:
                cur.execute(
                    "UPDATE hr.roles SET scope_type = %s, updated_at = now()"
                    " WHERE code <> ALL(%s) AND scope_type IS DISTINCT FROM %s",
                    [CUSTOM_DEFAULT_SCOPE, list(bilinen), CUSTOM_DEFAULT_SCOPE],
                )
                print(
                    f"    {slug}: bilinen kod dışı roller → {CUSTOM_DEFAULT_SCOPE} "
                    f"({', '.join(digerleri)})"
                )


class Migration(migrations.Migration):
    dependencies = [
        ("rbac", "0005_od5_rol_katalogu_gocu"),
    ]

    operations = [
        # 1) Kolon (mevcut satırlar varsayılanla dolar, sonra backfill düzeltir).
        migrations.AddField(
            model_name="role",
            name="scope_type",
            field=models.CharField(
                choices=[
                    ("TENANT", "Kiracı geneli"),
                    ("UNIT", "Birim + alt ağaç"),
                    ("UNIT_DIRECT", "Yalnızca birim (alt ağaç hariç)"),
                    ("ASSIGNED", "Yalnızca atanmış kayıtlar"),
                    ("SELF", "Yalnızca kendi kaydı"),
                ],
                default="TENANT",
                help_text=(
                    "OD5: rolün izinlerinin ulaşabileceği azami kapsam. İzin ÜRETMEZ; "
                    "izin ∧ kapsam birlikte değerlendirilir. UNIT/UNIT_DIRECT tiplerinde "
                    "atama (user_roles.scope_org_unit_id) zorunludur ve çözülemeyen "
                    "kapsam yetki vermez (fail-closed)."
                ),
                max_length=20,
                verbose_name="azami organizasyon kapsamı",
            ),
        ),
        # 2) Dokümandaki eşlemeye göre doldur (custom roller raporlanır).
        migrations.RunPython(kapsamlari_doldur, migrations.RunPython.noop),
        # 3) Kapalı küme DB düzeyinde (kısıt, backfill'den SONRA).
        migrations.AddConstraint(
            model_name="role",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    scope_type__in=["TENANT", "UNIT", "UNIT_DIRECT", "ASSIGNED", "SELF"]
                ),
                name="roles_scope_type_valid",
            ),
        ),
    ]
