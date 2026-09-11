# M7 (OD5 kapalı ✅) — tenant default rol setinin hedef kataloğa göçü.
#
# Yapılanlar (her kiracı için, kiracı bağlamı kurularak — `hr.*` tabloları
# FORCE RLS'tir):
#   1. **`manager` → `dept_manager`**: rolün **kimliği, atamaları ve izin bağları
#      korunur**; yalnızca `code`/`name` güncellenir (yeni rol üretilmez → çift
#      kayıt ve atama kaybı olmaz). İzin seti hedef `dept_manager` setine
#      **explicit** getirilir (eksikler eklenir, hedefte olmayanlar çıkarılır).
#   2. **`tenant_officer`** hedef rolü, §6.2 ŞYT sütunundaki izin setiyle
#      oluşturulur (idempotent: rol varsa yeniden üretilmez).
#   3. Soft-delete kuralları: silinmiş `manager` **sessizce canlandırılmaz**,
#      silinmiş `tenant_officer` yeniden yaratılmaz (uyarı basılır).
#   4. **Conflict fail-closed:** aynı kiracıda hem `manager` hem `dept_manager`
#      varsa migration **açık hata** verir (hangi rolün kazandığına dair karar
#      insan girdisi gerektirir; sessiz birleştirme yapılmaz).
#   5. İzin kodu katalogda yoksa **sessiz geçilmez** (eksik izin = "verildi sanılan
#      ama verilmeyen" yetki).
#
# `hr_manager` **silinmez** (§4.2: şablon rol olarak korunur). `employee`,
# `tenant_admin` izin setleri bu turda değiştirilmez.
#
# Kapsam (`scope_type`) bu migration'da **yoktur** (kolon M8/0006 ile gelir);
# M8 backfill'i `dept_manager`ı `UNIT`, diğerlerini dokümandaki değerlere çeker.
#
# Geri alma: `dept_manager` → `manager` adı/isim seti geri alınır; `tenant_officer`
# **ataması varsa** silinmez (veri kaybı yaratmamak için açık hata verilir).

from django.db import migrations

# DONMUŞ kopyalar (uygulanmış migration değişmez); canlı kaynak:
# `apps/rbac/permission_catalog.py`.
LEGACY_ROLE_CODE = "manager"
TARGET_ROLE_CODE = "dept_manager"
TARGET_ROLE_NAME = "Departman Yöneticisi"

# Eski `manager` şablonunun izin seti (geri alma için).
LEGACY_MANAGER_PERMISSIONS = (
    "org.department.read",
    "employee.read",
    "leave.request.read",
    "leave.request.approve",
    "shift.read",
    "attendance.read",
    "document.read",
)

# §6.2 DY (departman yöneticisi) sütunu — yalnız MEVCUT katalog kodları.
DEPT_MANAGER_PERMISSIONS = (
    "user.read",
    "org.department.read",
    "employee.read",
    "employee.update",
    "leave.request.read",
    "leave.request.create",
    "leave.request.approve",
    "shift.read",
    "shift.assign",
    "shift.schedule.manage",
    "attendance.read",
    "attendance.manage",
    "document.read",
    "document.upload",
)

# §6.2 ŞYT (şirket yetkilisi) sütunu — yalnız MEVCUT katalog kodları.
# NOT: matriste ŞYT için `◐` işaretli `document.share` bu katalogda **yoktur**
# (izin kataloğu genişletmesi ayrı iş kalemi); kod olmadığı için sete alınmadı.
TENANT_OFFICER_ROLE_NAME = "Şirket Yetkilisi"
TENANT_OFFICER_PERMISSIONS = (
    "user.read",
    "role.read",
    "org.department.read",
    "org.department.manage",
    "org.position.manage",
    "employee.read",
    "employee.create",
    "employee.update",
    "leave.request.read",
    "leave.request.create",
    "leave.request.approve",
    "shift.read",
    "shift.assign",
    "shift.schedule.manage",
    "attendance.read",
    "attendance.manage",
    "document.read",
    "document.upload",
    "document.delete",
)


def _izin_id(cur, izin_kodu: str):
    cur.execute("SELECT id FROM hr.permissions WHERE code = %s", [izin_kodu])
    satir = cur.fetchone()
    if satir is None:
        raise RuntimeError(
            f"M7: izin kodu katalogda yok: {izin_kodu}. Eksik izin sessizce geçilemez "
            "(önce ilgili izin migration'ı uygulanmalı)."
        )
    return satir[0]


def _rol_izinlerini_esitle(cur, tenant_id, rol_id, hedef_kodlar: tuple[str, ...]) -> tuple[int, int]:
    """Rolün izin setini **tam olarak** hedefe getirir. Döner: (eklenen, çıkarılan)."""
    hedef_idler = {_izin_id(cur, kod) for kod in hedef_kodlar}
    cur.execute(
        "SELECT permission_id FROM hr.role_permissions WHERE role_id = %s", [rol_id]
    )
    mevcut_idler = {satir[0] for satir in cur.fetchall()}

    eklenecek = hedef_idler - mevcut_idler
    cikarilacak = mevcut_idler - hedef_idler

    for izin_id in sorted(eklenecek, key=str):
        # `hr.*` tablolarında PK için DB varsayılanı yoktur (Django UUIDModel uuid4'ü
        # Python tarafında üretir) → ham SQL'de `gen_random_uuid()` kullanılır.
        cur.execute(
            "INSERT INTO hr.role_permissions (id, tenant_id, role_id, permission_id,"
            " created_at, updated_at) VALUES (gen_random_uuid(), %s, %s, %s, now(), now())"
            " ON CONFLICT DO NOTHING",
            [str(tenant_id), rol_id, izin_id],
        )
    if cikarilacak:
        cur.execute(
            "DELETE FROM hr.role_permissions WHERE role_id = %s AND permission_id = ANY(%s)",
            [rol_id, list(cikarilacak)],
        )
    return len(eklenecek), len(cikarilacak)


def _scope_kolonu_var_mi(cur) -> bool:  # noqa: ANN001
    """
    `hr.roles.scope_type` kolonu var mı?

    Gerçek göç sırasında M8 (`0006`) henüz uygulanmadığı için kolon **yoktur**.
    Ancak şema ileride olduğunda (ör. testte bu göçün yeniden oynatılması) da
    doğru çalışmalıdır: kolon varsa M8'in eşlemesiyle aynı değer (`TENANT`)
    açıkça yazılır — sessizce NULL bırakılmaz (`NOT NULL` + fail-closed).
    """
    cur.execute(
        "SELECT 1 FROM information_schema.columns WHERE table_schema = 'hr'"
        " AND table_name = 'roles' AND column_name = 'scope_type'"
    )
    return cur.fetchone() is not None


def _tenant_officer_olustur(cur, tenant_id, mevcut: tuple | None) -> str:
    """
    `tenant_officer` rolünü oluşturur (idempotent). Döner: durum metni.

    `mevcut` = (id, deleted_at) veya None.
    """
    if mevcut is not None:
        if mevcut[1] is not None:
            return "ATLANDI: 'tenant_officer' soft-delete edilmiş (sessiz restore YOK)"
        return "mevcut (dokunulmadı)"

    kapsam_kolonu = _scope_kolonu_var_mi(cur)
    kolonlar = (
        "id, tenant_id, code, name, description, role_type, is_assignable, scope_type,"
        " created_at, updated_at"
        if kapsam_kolonu
        else "id, tenant_id, code, name, description, role_type, is_assignable,"
        " created_at, updated_at"
    )
    degerler = (
        "gen_random_uuid(), %s, %s, %s, '', 'default', true, 'TENANT', now(), now()"
        if kapsam_kolonu
        else "gen_random_uuid(), %s, %s, %s, '', 'default', true, now(), now()"
    )
    # Sütun/değer listesi SABİT metinlerden kurulur (dış girdi yok); değerler
    # parametreli olarak bağlanır. `scope_type` kolonu M8 öncesi yoktur.
    cur.execute(
        f"INSERT INTO hr.roles ({kolonlar}) VALUES ({degerler}) RETURNING id",  # noqa: S608  # nosec B608
        [str(tenant_id), "tenant_officer", TENANT_OFFICER_ROLE_NAME],
    )
    rol_id = cur.fetchone()[0]
    _rol_izinlerini_esitle(cur, tenant_id, rol_id, TENANT_OFFICER_PERMISSIONS)
    return f"oluşturuldu ({len(TENANT_OFFICER_PERMISSIONS)} izin)"


def rolleri_goc_et(apps, schema_editor):  # noqa: ARG001
    with schema_editor.connection.cursor() as cur:
        cur.execute("SELECT tenant_id, slug FROM hr.tenant_directory ORDER BY slug")
        kiraclar = cur.fetchall()

        for tenant_id, slug in kiraclar:
            cur.execute("SELECT set_config('app.tenant_id', %s, true)", [str(tenant_id)])

            cur.execute(
                "SELECT count(*) FROM hr.roles WHERE deleted_at IS NULL",
                [],
            )
            rol_oncesi = cur.fetchone()[0]
            cur.execute("SELECT count(*) FROM hr.role_permissions")
            izin_oncesi = cur.fetchone()[0]

            # --- 1) manager → dept_manager -----------------------------------
            # Karar matrisi (fail-closed, sessiz davranış YOK):
            #   * manager AKTİF + dept_manager (aktif veya silinmiş) var → ÇAKIŞMA:
            #     `(tenant_id, code)` benzersizliği nedeniyle göç teknik olarak da
            #     yapılamaz; hangi rolün kazandığına açık karar verilmelidir → hata.
            #   * manager SİLİNMİŞ → **sessiz restore YOK**: adı değiştirilmez,
            #     yeni rol üretilmez; uyarı basılır (OD21: geri alma açık işlem ister).
            #   * manager AKTİF + dept_manager yok → yerinde göç (id/atamalar korunur).
            cur.execute(
                "SELECT id, deleted_at FROM hr.roles WHERE code = %s", [LEGACY_ROLE_CODE]
            )
            eski = cur.fetchone()
            cur.execute(
                "SELECT id, deleted_at FROM hr.roles WHERE code = %s", [TARGET_ROLE_CODE]
            )
            hedef = cur.fetchone()
            durum = "yok (göç gerekmedi)"

            if eski is not None and hedef is not None and eski[1] is None:
                raise RuntimeError(
                    f"M7 conflict (fail-closed): kiracı '{slug}' içinde hem '{LEGACY_ROLE_CODE}' "
                    f"hem '{TARGET_ROLE_CODE}' kodu bulunuyor. Hangi rolün kazandığına açık "
                    "karar verilmelidir; otomatik birleştirme/yeniden adlandırma yapılmaz."
                )
            if eski is not None:
                if eski[1] is not None:
                    durum = "ATLANDI: 'manager' soft-delete edilmiş (sessiz restore YOK)"
                else:
                    cur.execute(
                        "UPDATE hr.roles SET code = %s, name = %s, updated_at = now()"
                        " WHERE id = %s",
                        [TARGET_ROLE_CODE, TARGET_ROLE_NAME, eski[0]],
                    )
                    eklenen, cikarilan = _rol_izinlerini_esitle(
                        cur, tenant_id, eski[0], DEPT_MANAGER_PERMISSIONS
                    )
                    durum = (
                        f"göç edildi (id korundu, izin +{eklenen}/-{cikarilan},"
                        f" hedef {len(DEPT_MANAGER_PERMISSIONS)})"
                    )

            # --- 2) tenant_officer -------------------------------------------
            cur.execute(
                "SELECT id, deleted_at FROM hr.roles WHERE code = 'tenant_officer'", []
            )
            officer_durum = _tenant_officer_olustur(cur, tenant_id, cur.fetchone())

            cur.execute("SELECT count(*) FROM hr.roles WHERE deleted_at IS NULL")
            rol_sonrasi = cur.fetchone()[0]
            cur.execute("SELECT count(*) FROM hr.role_permissions")
            izin_sonrasi = cur.fetchone()[0]

            print(
                f"    {slug}: roller {rol_oncesi}→{rol_sonrasi}, rol-izin {izin_oncesi}→{izin_sonrasi}"
                f" | dept_manager: {durum} | tenant_officer: {officer_durum}"
            )


def rolleri_geri_al(apps, schema_editor):  # noqa: ARG001
    with schema_editor.connection.cursor() as cur:
        cur.execute("SELECT tenant_id, slug FROM hr.tenant_directory ORDER BY slug")
        for tenant_id, slug in cur.fetchall():
            cur.execute("SELECT set_config('app.tenant_id', %s, true)", [str(tenant_id)])

            # tenant_officer: yalnızca ATAMASI YOKSA silinir (veri kaybı yaratmama).
            cur.execute("SELECT id FROM hr.roles WHERE code = 'tenant_officer'")
            officer = cur.fetchone()
            if officer is not None:
                cur.execute(
                    "SELECT count(*) FROM hr.user_roles WHERE role_id = %s", [officer[0]]
                )
                if cur.fetchone()[0]:
                    raise RuntimeError(
                        f"Geri alma güvenli değil: '{slug}' kiracısında 'tenant_officer' "
                        "rolünün atamaları var (veri kaybı olur)."
                    )
                cur.execute("DELETE FROM hr.role_permissions WHERE role_id = %s", [officer[0]])
                cur.execute("DELETE FROM hr.roles WHERE id = %s", [officer[0]])

            cur.execute("SELECT id FROM hr.roles WHERE code = 'manager'")
            if cur.fetchone() is not None:
                raise RuntimeError(
                    f"Geri alma güvenli değil: '{slug}' kiracısında 'manager' kodu yeniden "
                    "kullanımda."
                )
            cur.execute("SELECT id FROM hr.roles WHERE code = 'dept_manager'")
            satir = cur.fetchone()
            if satir is not None:
                cur.execute(
                    "UPDATE hr.roles SET code = %s, name = 'Yönetici', updated_at = now()"
                    " WHERE id = %s",
                    [LEGACY_ROLE_CODE, satir[0]],
                )
                _rol_izinlerini_esitle(cur, tenant_id, satir[0], LEGACY_MANAGER_PERMISSIONS)


class Migration(migrations.Migration):
    dependencies = [
        ("rbac", "0004_od3_platform_permissions"),
    ]

    operations = [
        migrations.RunPython(rolleri_goc_et, rolleri_geri_al),
    ]
