# P5a V1 — fazla mesai izin kodları + sistem rol şablonlarının senkronizasyonu.
#
# KAYNAK KARARLAR (kapalı): **OD39** (fazla mesai onay sırası leave ile aynı altyapı:
# önce birincil yönetici, sonra İK), **OD16** (katalog evrimi sistem rol şablonlarıyla
# senkronize edilir; özel roller otomatik değişmez), **OD9** (devredilemez kod listesi;
# `overtime.request.approve` bu listede **değildir** — izin onayıyla aynı sınıfta,
# özel role verilebilir), **OD5** (izin ∧ kapsam).
#
# Matris kaynağı: docs/HR_ROADMAP.md §11 (P5a izinleri), §18 (ESS: `overtime.request.create`),
# §19 (MSS: `overtime.request.approve`). Kodlar uydurulmadı: yol haritasının P5a satırındaki
# adlar kullanıldı (`overtime.request.create|read|approve|cancel`).
#   * `overtime.rule.manage` ve `overtime.balance.read` **bu dilimde eklenmez**:
#     kural motoru ve bakiye/karşılık hesabı P5a'nın ileriki dilimindedir; karşılıksız
#     yetki kodu üretmek ölü yetki olurdu (raporlandı).
#
# MİĞRASYON NE YAPAR?
#   1. 4 kiracı izin kodunu `hr.permissions`'a ekler (idempotent).
#   2. **Her kiracı için** (RLS: `set_config('app.tenant_id', …)`) sistem rollerinin izin
#      kümesini şablona tam olarak eşitler (eksik bağ eklenir, şablon dışı fazla bağ
#      kaldırılır) — mevcut desen: `0012_p1a_permissions_roles`.
#   3. Özel roller (`role_type != 'default'`) **hiç değiştirilmez** (OD16).
#
# GERİ ALMA: bu migration'ın eklediği izin kodları ve bağları silinir (kiracı bağlamı
# kurularak; `hr.role_permissions` FORCE RLS'tir).

from django.db import migrations

YENI_IZINLER: tuple[tuple[str, str, str], ...] = (
    ("overtime.request.create", "Fazla mesai bildirimi oluşturma", "overtime"),
    ("overtime.request.read", "Fazla mesai taleplerini görme", "overtime"),
    ("overtime.request.approve", "Fazla mesai talebini onaylama/reddetme", "overtime"),
    ("overtime.request.cancel", "Fazla mesai bildirimi iptali", "overtime"),
)

#: Sistem rol şablonları — **bu revizyonda donmuş** (kaynak: `permission_catalog.py`,
#: P5a deltası uygulanmış hali). Migration dosyaları app koduna bağımlı olmamalıdır.
#: Sayımlar: tenant_admin 45, tenant_officer 27, hr_manager 33, dept_manager 21,
#: employee 11 (kiracı kodu toplamı 45).
SISTEM_ROLLERI: dict[str, tuple[str, ...]] = {
    "tenant_admin": (
        "tenant.read", "tenant.manage", "user.read",
        "user.manage", "role.read", "role.manage",
        "org.department.read", "org.department.manage", "org.position.manage",
        "org.workplace.read", "org.workplace.manage", "employee.read",
        "employee.create", "employee.update", "employee.delete",
        "employee.sensitive.read", "employee.sensitive.write", "employee.suspend",
        "employee.terminate", "employee.restore", "employee.history.read",
        "employee.form.manage", "leave.request.read", "leave.request.create",
        "leave.request.approve", "leave.request.approve.override", "leave.request.cancel",
        "leave.balance.read", "leave.balance.manage", "leave.type.manage",
        "overtime.request.create", "overtime.request.read", "overtime.request.approve",
        "overtime.request.cancel",
        "shift.read", "shift.assign", "shift.schedule.manage",
        "attendance.read", "attendance.manage", "document.read",
        "document.upload", "document.delete", "document.confidential.read",
        "audit.read", "billing.manage",
    ),
    "tenant_officer": (
        "user.read", "role.read", "org.department.read",
        "org.department.manage", "org.position.manage", "employee.read",
        "employee.create", "employee.update", "leave.request.read",
        "leave.request.create", "leave.request.approve", "leave.request.cancel",
        "leave.balance.read", "shift.read", "shift.assign",
        "shift.schedule.manage", "attendance.read", "attendance.manage",
        "document.read", "document.upload", "document.delete",
        "employee.history.read", "org.workplace.read",
        "overtime.request.create", "overtime.request.read", "overtime.request.approve",
        "overtime.request.cancel",
    ),
    "hr_manager": (
        "tenant.read", "user.read", "role.read",
        "org.department.read", "org.department.manage", "org.position.manage",
        "employee.read", "employee.create", "employee.update",
        "employee.sensitive.read", "employee.sensitive.write", "employee.suspend",
        "employee.terminate", "employee.restore", "employee.history.read",
        "employee.form.manage", "org.workplace.read", "org.workplace.manage",
        "leave.request.read", "leave.request.approve", "leave.type.manage",
        "overtime.request.read", "overtime.request.approve", "overtime.request.cancel",
        "shift.read", "shift.assign", "shift.schedule.manage",
        "attendance.read", "attendance.manage", "document.read",
        "document.upload", "document.confidential.read", "audit.read",
    ),
    "dept_manager": (
        "user.read", "org.department.read", "employee.read",
        "employee.update", "leave.request.read", "leave.request.create",
        "leave.request.approve", "leave.request.cancel", "leave.balance.read",
        "overtime.request.create", "overtime.request.read", "overtime.request.approve",
        "overtime.request.cancel",
        "shift.read", "shift.assign", "shift.schedule.manage",
        "attendance.read", "attendance.manage", "document.read",
        "document.upload", "employee.history.read",
    ),
    "employee": (
        "org.department.read", "employee.read", "leave.request.read",
        "leave.request.create", "leave.request.cancel", "leave.balance.read",
        "overtime.request.create", "overtime.request.read", "overtime.request.cancel",
        "shift.read", "document.read",
    ),
}


def izinleri_ve_rolleri_senkronla(apps, schema_editor) -> None:  # noqa: ANN001, ARG001
    with schema_editor.connection.cursor() as cur:
        for kod, ad, modul in YENI_IZINLER:
            cur.execute(
                "INSERT INTO hr.permissions "
                "(id, created_at, updated_at, code, name, module, description) "
                "VALUES (gen_random_uuid(), now(), now(), %s, %s, %s, '') "
                "ON CONFLICT (code) DO NOTHING",
                [kod, ad, modul],
            )

        # Kiracı listesi: `hr.tenant_directory` RLS dışıdır ve app rolüne SELECT verilir.
        cur.execute("SELECT tenant_id, slug FROM hr.tenant_directory ORDER BY slug")
        kiracilar = cur.fetchall()

        for tenant_id, slug in kiracilar:
            cur.execute("SELECT set_config('app.tenant_id', %s, true)", [str(tenant_id)])
            for rol_kodu, kodlar in SISTEM_ROLLERI.items():
                cur.execute(
                    "SELECT id FROM hr.roles WHERE code = %s AND deleted_at IS NULL "
                    "AND role_type = 'default'",
                    [rol_kodu],
                )
                satir = cur.fetchone()
                if satir is None:
                    # Rol yoksa şablonu `create_default_roles` kurar; burada üretilmez.
                    continue
                rol_id = satir[0]

                for kod in kodlar:  # (a) eksik bağları ekle (idempotent)
                    cur.execute(
                        "INSERT INTO hr.role_permissions "
                        "(id, created_at, updated_at, tenant_id, role_id, permission_id) "
                        "SELECT gen_random_uuid(), now(), now(), %s, %s, p.id "
                        "  FROM hr.permissions p WHERE p.code = %s "
                        "ON CONFLICT (role_id, permission_id) DO NOTHING",
                        [str(tenant_id), str(rol_id), kod],
                    )

                cur.execute(  # (b) şablon dışı fazla bağları kaldır (yalnız sistem rolleri)
                    "SELECT p.code FROM hr.role_permissions rp "
                    "JOIN hr.permissions p ON p.id = rp.permission_id "
                    "WHERE rp.role_id = %s AND p.code <> ALL(%s)",
                    [str(rol_id), list(kodlar)],
                )
                fazlaliklar = sorted(satir2[0] for satir2 in cur.fetchall())
                if fazlaliklar:
                    cur.execute(
                        "DELETE FROM hr.role_permissions "
                        "WHERE role_id = %s AND permission_id IN ("
                        "  SELECT id FROM hr.permissions WHERE code = ANY(%s))",
                        [str(rol_id), fazlaliklar],
                    )
                    print(
                        "  P5a rol senkronu: '%s/%s' şablon dışı %d bağ kaldırıldı: %s"
                        % (slug, rol_kodu, len(fazlaliklar), ", ".join(fazlaliklar))
                    )
            print("  P5a izin/rol senkronu tamamlandı: kiracı '%s'" % slug)


def yeni_izinleri_geri_al(apps, schema_editor) -> None:  # noqa: ANN001, ARG001
    """Geri alma: bu migration'ın eklediği izin kodları ve bağları silinir."""
    kodlar = [kod for kod, _ad, _mod in YENI_IZINLER]
    with schema_editor.connection.cursor() as cur:
        cur.execute("SELECT tenant_id, slug FROM hr.tenant_directory ORDER BY slug")
        for tenant_id, _slug in cur.fetchall():
            cur.execute("SELECT set_config('app.tenant_id', %s, true)", [str(tenant_id)])
            cur.execute(
                "DELETE FROM hr.role_permissions rp USING hr.permissions p "
                "WHERE rp.permission_id = p.id AND p.code = ANY(%s)",
                [kodlar],
            )
        cur.execute("SELECT set_config('app.tenant_id', '', true)")
        cur.execute("DELETE FROM hr.permissions WHERE code = ANY(%s)", [kodlar])


class Migration(migrations.Migration):
    dependencies = [
        ("rbac", "0012_p1a_permissions_roles"),
    ]

    operations = [
        migrations.RunPython(izinleri_ve_rolleri_senkronla, yeni_izinleri_geri_al),
    ]
