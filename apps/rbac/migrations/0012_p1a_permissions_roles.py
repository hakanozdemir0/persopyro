# P1-A — People lifecycle izin kodları + sistem rol şablonlarının senkronizasyonu.
#
# KAYNAK KARARLAR (kapalı): OD6, OD9, OD16, OD30, OD61 (`docs/AUTHORIZATION.md` §16).
#   * OD16: katalog evriminde **sistem rol şablonları data migration ile senkronize
#     edilir**; **özel roller otomatik güncellenmez** (kullanıcı yetkisi kendiliğinden
#     büyümez) ve rol kaydı silinmez.
#   * OD9: **devredilemez** kodlar (role.manage, tenant.manage, user.manage,
#     employee.delete, employee.suspend/terminate/restore, employee.sensitive.read/write,
#     employee.form.manage, leave.balance.manage, audit.read) yalnız sistem rollerinde
#     bulunur; özel rollere servis katmanı ekletmez.
#   * OD30: kişisel veri yazma için ayrı kod (`employee.sensitive.write`); okuma izni
#     yazma izni **vermez** ("write ⇒ read" kuralı servis katmanında uygulanır).
#   * OD61: 8 yeni kodun rol dağılımı: tenant_admin 41, hr_manager 30, tenant_officer 23,
#     dept_manager 17, employee 8 (kiracı kodu 41, genel 47).
#
# MİGRASYON NE YAPAR?
#   1. 8 kiracı izin kodunu `hr.permissions`'a ekler (idempotent).
#   2. **Her kiracı için** (RLS: `set_config('app.tenant_id', …)`) sistem rollerinin izin
#      kümesini **şablona tam olarak eşitler**: eksik bağ eklenir, şablonda olmayan bağ
#      kaldırılır. Böylece rol izin sayıları her ortamda deterministik olur ve geçmişte
#      demo/seed kaynaklı **fazla yetkiler** temizlenir (kayıt: aşağıda listelenir).
#   3. Özel roller (`role_type != 'default'`) **hiç değiştirilmez** (OD16).
#
# GERİ ALMA: bu migration'ın eklediği izin kodları ve bağlar silinir. Şablon dışı fazla
# bağların kaldırılması geri alınmaz (katalog dışı drift; migration çıktısında listelenir).

from django.db import migrations

YENI_IZINLER: tuple[tuple[str, str, str], ...] = (
    ("employee.sensitive.write", "Hassas çalışan verisini yazma (K2/K3)", "employee"),
    ("employee.suspend", "Çalışanı askıya alma ve geri alma", "employee"),
    ("employee.terminate", "İşten çıkış verme", "employee"),
    ("employee.restore", "Askı/çıkış kaydını gerekçeli geri alma", "employee"),
    ("employee.history.read", "İstihdam ve atama geçmişini görme", "employee"),
    ("employee.form.manage", "Onboarding form yapılandırması", "employee"),
    ("org.workplace.read", "İşyerlerini görme", "org"),
    ("org.workplace.manage", "İşyeri oluşturma/güncelleme", "org"),
)

#: Sistem rol şablonları — **bu revizyonda donmuş** (kaynak: `permission_catalog.py`).
#: Migration dosyaları app koduna bağımlı olmamalıdır; değişiklik yeni migration ile gelir.
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
    ),
    "hr_manager": (
        "tenant.read", "user.read", "role.read",
        "org.department.read", "org.department.manage", "org.position.manage",
        "employee.read", "employee.create", "employee.update",
        "employee.sensitive.read", "employee.sensitive.write", "employee.suspend",
        "employee.terminate", "employee.restore", "employee.history.read",
        "employee.form.manage", "org.workplace.read", "org.workplace.manage",
        "leave.request.read", "leave.request.approve", "leave.type.manage",
        "shift.read", "shift.assign", "shift.schedule.manage",
        "attendance.read", "attendance.manage", "document.read",
        "document.upload", "document.confidential.read", "audit.read",
    ),
    "dept_manager": (
        "user.read", "org.department.read", "employee.read",
        "employee.update", "leave.request.read", "leave.request.create",
        "leave.request.approve", "leave.request.cancel", "leave.balance.read",
        "shift.read", "shift.assign", "shift.schedule.manage",
        "attendance.read", "attendance.manage", "document.read",
        "document.upload", "employee.history.read",
    ),
    "employee": (
        "org.department.read", "employee.read", "leave.request.read",
        "leave.request.create", "leave.request.cancel", "leave.balance.read",
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
                        "  P1-A rol senkronu: '%s/%s' şablon dışı %d bağ kaldırıldı: %s"
                        % (slug, rol_kodu, len(fazlaliklar), ", ".join(fazlaliklar))
                    )
            print("  P1-A izin/rol senkronu tamamlandı: kiracı '%s'" % slug)


def yeni_izinleri_geri_al(apps, schema_editor) -> None:  # noqa: ANN001, ARG001
    """
    Geri alma: bu migration'ın eklediği izin kodları ve bağları silinir.

    DİKKAT: `hr.role_permissions` **FORCE RLS**'tir; kiracı bağlamı kurulmazsa DELETE
    hiçbir satırı görmez ve `hr.permissions` silme adımı FK ihlaliyle başarısız olur
    (ölçüldü). Bu yüzden kiracı bağlamı kurularak silinir.
    """
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
        ("rbac", "0011_m14_leave_permissions"),
    ]

    operations = [
        migrations.RunPython(izinleri_ve_rolleri_senkronla, yeni_izinleri_geri_al),
    ]
