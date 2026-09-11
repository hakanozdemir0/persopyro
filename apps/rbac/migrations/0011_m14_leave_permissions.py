# M14 (izin modülü) — matrisin **E. İzin** bölümündeki eksik izin kodları.
#
# NEDEN: `docs/AUTHORIZATION.md` §6.2 E bölümü sekiz izin kodu tanımlar; bunlardan
# dördü katalogda eksikti (`permission_catalog.py` bu eksikleri açıkça not ediyordu).
# M14 (izin modülü) bu kodlar olmadan çalışamaz:
#
#   * `leave.request.cancel`            — iptal (ŞY ●, ŞYT ◐, DY ◐ UNIT, ÇALIŞAN ○ SELF/onay öncesi)
#   * `leave.request.approve.override`  — kayıtlı gerekçeyle adım atlama (yalnız ŞY ● TENANT)
#   * `leave.balance.read`              — bakiye görme (ŞY ●, ŞYT ●, DY ● UNIT, ÇALIŞAN ○ SELF)
#   * `leave.balance.manage`            — bakiye düzeltme (yalnız ŞY ● TENANT)
#
# Uygulama: (1) izin kayıtları eklenir (idempotent), (2) mevcut kiracıların
# **varsayılan** rolleri matristeki bağlara göre tamamlanır. Roller/izin bağları
# silinmez, yalnızca eksik bağlar eklenir (yeniden çalıştırma güvenli).
# `hr_manager` şablonu matriste sütun olarak yer almadığı için **değiştirilmez**.
#
# `hr.role_permissions`/`hr.roles` FORCE RLS'tir → her kiracı için bağlam kurulur
# (M7 göçündeki `set_config('app.tenant_id', ...)` deseni).

from django.db import migrations

YENI_IZINLER: tuple[tuple[str, str, str], ...] = (
    ("leave.request.cancel", "İzin talebi iptali", "leave"),
    ("leave.request.approve.override", "İzin onay adımlarını kayıtlı gerekçeyle atlama", "leave"),
    ("leave.balance.read", "İzin bakiyelerini görme", "leave"),
    ("leave.balance.manage", "İzin bakiyesi düzeltme", "leave"),
)

#: Varsayılan rol → eklenecek yeni izin kodları (§6.2 E matrisi).
ROL_BAGLARI: dict[str, tuple[str, ...]] = {
    "tenant_admin": tuple(kod for kod, _ad, _mod in YENI_IZINLER),
    "tenant_officer": ("leave.request.cancel", "leave.balance.read"),
    "dept_manager": ("leave.request.cancel", "leave.balance.read"),
    "employee": ("leave.request.cancel", "leave.balance.read"),
}


def izinleri_ekle(apps, schema_editor) -> None:  # noqa: ANN001, ARG001
    with schema_editor.connection.cursor() as cur:
        for kod, ad, modul in YENI_IZINLER:
            cur.execute(
                "INSERT INTO hr.permissions "
                "(id, created_at, updated_at, code, name, module, description) "
                "VALUES (gen_random_uuid(), now(), now(), %s, %s, %s, '') "
                "ON CONFLICT (code) DO NOTHING",
                [kod, ad, modul],
            )

        cur.execute("SELECT tenant_id, slug FROM hr.tenant_directory ORDER BY slug")
        kiracilar = cur.fetchall()

        for tenant_id, slug in kiracilar:
            cur.execute("SELECT set_config('app.tenant_id', %s, true)", [str(tenant_id)])
            for rol_kodu, kodlar in ROL_BAGLARI.items():
                cur.execute(
                    "SELECT id FROM hr.roles WHERE code = %s AND deleted_at IS NULL",
                    [rol_kodu],
                )
                satir = cur.fetchone()
                if satir is None:
                    # Rol yoksa şablon zaten `create_default_roles` ile kurulacaktır.
                    continue
                rol_id = satir[0]
                for kod in kodlar:
                    cur.execute(
                        "INSERT INTO hr.role_permissions "
                        "(id, created_at, updated_at, tenant_id, role_id, permission_id) "
                        "SELECT gen_random_uuid(), now(), now(), %s, %s, p.id "
                        "  FROM hr.permissions p WHERE p.code = %s "
                        "ON CONFLICT (role_id, permission_id) DO NOTHING",
                        [str(tenant_id), str(rol_id), kod],
                    )
            print(f"  M14 izin bağları tamamlandı: kiracı '{slug}'")


def izinleri_geri_al(apps, schema_editor) -> None:  # noqa: ANN001, ARG001
    """Geri alma: yalnızca bu migration'ın eklediği bağlar ve izin kayıtları silinir."""
    kodlar = [kod for kod, _ad, _mod in YENI_IZINLER]
    with schema_editor.connection.cursor() as cur:
        for kod in kodlar:
            cur.execute(
                "DELETE FROM hr.role_permissions rp USING hr.permissions p "
                "WHERE rp.permission_id = p.id AND p.code = %s",
                [kod],
            )
            cur.execute("DELETE FROM hr.permissions WHERE code = %s", [kod])


class Migration(migrations.Migration):
    dependencies = [
        ("rbac", "0010_od5_scope_guard_deactivate_fix"),
    ]

    operations = [
        migrations.RunPython(izinleri_ekle, izinleri_geri_al),
    ]
