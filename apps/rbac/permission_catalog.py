"""
İzin kataloğu ve varsayılan rol şablonları (canlı kaynak).

Kod biçimi: `<kaynak>.<işlem>` (docs/AUTHORIZATION.md bölüm 4).

NOT: Veri migration'ı (`rbac/migrations/0003_seed_permissions.py`) bu listenin
DONMUŞ bir kopyasını taşır — uygulanmış migration'lar sonradan değişmez. Buraya
yeni izin eklenirse, mevcut veritabanları için YENİ bir migration yazılmalıdır.
"""

from apps.rbac.models import ORG_SCOPED_ROLE_SCOPE_TYPES, RoleScopeType  # noqa: F401

#: `ORG_SCOPED_ROLE_SCOPE_TYPES` / `RoleScopeType`: models.py tek kaynak; burada
#: yalnızca katalog modülünden erişilebilsin diye yeniden dışa aktarılır.

# (kod, ad, modül)
PERMISSIONS: list[tuple[str, str, str]] = [
    # Arayüz başlıkları ve alt başlıkları (işlem izinlerinden bağımsız görünürlük).
    ("ui.panel.view", "Panel başlığını görme", "ui"),
    ("ui.departments.view", "Departmanlar başlığını görme", "ui"),
    ("ui.employees.view", "Çalışanlar başlığını görme", "ui"),
    ("ui.employees.list", "Çalışan Ara / Liste alt başlığını görme", "ui"),
    ("ui.employees.new", "Yeni Çalışan alt başlığını görme", "ui"),
    ("ui.employees.form", "Kişisel Bilgi Formu alt başlığını görme", "ui"),
    ("ui.employees.exit", "İşten Çıkış / Çıkış Süreci alt başlığını görme", "ui"),
    ("ui.employees.suspended", "Askıya Alınanlar alt başlığını görme", "ui"),
    ("ui.leave.view", "İzin Talebi başlığını görme", "ui"),
    ("ui.leave.request", "İzin talebi alt bölümünü görme", "ui"),
    ("ui.leave.policy", "İzin politikası alt bölümünü görme", "ui"),
    ("ui.leave.types", "İzin türleri alt bölümünü görme", "ui"),
    ("ui.leave.entitlements", "İzin hakları alt bölümünü görme", "ui"),
    ("ui.overtime.view", "Fazla Mesai başlığını görme", "ui"),
    ("ui.overtime.requests", "Fazla mesai bildirimleri alt bölümünü görme", "ui"),
    ("ui.overtime.rules", "Fazla mesai kuralları alt bölümünü görme", "ui"),
    ("ui.shift.view", "Vardiya başlığını görme", "ui"),
    ("ui.shift.definitions", "Vardiya tanımları alt bölümünü görme", "ui"),
    ("ui.shift.assignments", "Vardiya planı alt bölümünü görme", "ui"),
    ("ui.approvals.view", "Onaylar başlığını görme", "ui"),
    ("ui.roles.view", "Yetki Yönetimi başlığını görme", "ui"),
    # Kiracı ve kullanıcı yönetimi
    ("tenant.read", "Kiracı bilgilerini görme", "tenant"),
    ("tenant.manage", "Kiracı ayarlarını yönetme", "tenant"),
    ("user.read", "Kullanıcıları görme", "user"),
    ("user.manage", "Kullanıcı oluşturma/güncelleme", "user"),
    ("role.read", "Rolleri görme", "role"),
    ("role.manage", "Rol ve izin yönetimi", "role"),
    # Organizasyon
    ("org.department.read", "Departmanları görme", "org"),
    ("org.department.manage", "Departman yönetimi", "org"),
    ("org.position.manage", "Pozisyon yönetimi", "org"),
    ("org.workplace.read", "İşyerlerini görme", "org"),
    ("org.workplace.manage", "İşyeri oluşturma/güncelleme", "org"),
    # Çalışan
    ("employee.read", "Çalışanları görme", "employee"),
    ("employee.create", "Çalışan ekleme", "employee"),
    ("employee.update", "Çalışan güncelleme", "employee"),
    ("employee.photo.upload", "Çalışan fotoğrafı yükleme/değiştirme", "employee"),
    ("employee.delete", "Çalışan arşivleme (fiziksel silme DEĞİL — OD6)", "employee"),
    ("employee.sensitive.read", "Hassas çalışan verisini görme (maskesiz)", "employee"),
    ("employee.sensitive.write", "Hassas çalışan verisini yazma (K2/K3)", "employee"),
    ("employee.suspend", "Çalışanı askıya alma ve geri alma", "employee"),
    ("employee.terminate", "İşten çıkış verme", "employee"),
    ("employee.restore", "Askı/çıkış kaydını gerekçeli geri alma", "employee"),
    ("employee.history.read", "İstihdam ve atama geçmişini görme", "employee"),
    ("employee.form.manage", "Onboarding form yapılandırması", "employee"),
    # İzin
    ("leave.request.read", "İzin taleplerini görme", "leave"),
    ("leave.request.create", "İzin talebi oluşturma", "leave"),
    ("leave.request.approve", "İzin talebi onaylama/reddetme", "leave"),
    ("leave.request.approve.override", "İzin onay adımlarını kayıtlı gerekçeyle atlama", "leave"),
    ("leave.request.cancel", "İzin talebi iptali", "leave"),
    ("leave.balance.read", "İzin bakiyelerini görme", "leave"),
    ("leave.balance.manage", "İzin bakiyesi düzeltme", "leave"),
    ("leave.type.manage", "İzin türü ve politikası yönetimi", "leave"),
    # Fazla mesai (P5a V1: talep + onay temeli; kural/ücret motoru ileriki faz)
    ("overtime.request.create", "Fazla mesai bildirimi oluşturma", "overtime"),
    ("overtime.request.read", "Fazla mesai taleplerini görme", "overtime"),
    (
        "overtime.request.approve",
        "Fazla mesai talebini onaylama/reddetme",
        "overtime",
    ),
    ("overtime.request.cancel", "Fazla mesai bildirimi iptali", "overtime"),
    ("overtime.rule.read", "Fazla mesai kurallarını görme", "overtime"),
    ("overtime.rule.manage", "Fazla mesai kuralı yönetimi", "overtime"),
    # Vardiya ve devam
    ("shift.read", "Vardiyaları görme", "shift"),
    ("shift.assign", "Vardiya atama", "shift"),
    ("shift.schedule.manage", "Vardiya planı yönetimi", "shift"),
    ("attendance.read", "Devam kayıtlarını görme", "attendance"),
    ("attendance.manage", "Devam kaydı düzeltme", "attendance"),
    # Evrak
    ("document.read", "Belgeleri görme", "document"),
    ("document.upload", "Belge yükleme", "document"),
    ("document.delete", "Belge silme", "document"),
    ("document.confidential.read", "Gizli belge görme", "document"),
    # Denetim ve faturalama
    ("audit.read", "Denetim kayıtlarını görme", "audit"),
    ("billing.manage", "Abonelik ve faturalama yönetimi", "billing"),
]

UI_PERMISSION_CODES: list[str] = [kod for kod, _, _ in PERMISSIONS if kod.startswith("ui.")]
# İşlem izinlerinin geriye dönük sabit kümesi. `ui.*` kodları arayüz görünürlüğü
# için ayrı yönetilir; rol şablonlarının ve mevcut sayımların anlamı değişmez.
ALL_PERMISSION_CODES: list[str] = [kod for kod, _, _ in PERMISSIONS if not kod.startswith("ui.")]

UI_PERMISSION_REQUIREMENTS: dict[str, frozenset[str]] = {
    "ui.panel.view": frozenset({
        "employee.read", "leave.request.read", "leave.request.create",
        "overtime.request.read", "role.manage",
    }),
    "ui.departments.view": frozenset({"org.department.read"}),
    "ui.employees.view": frozenset({"employee.read"}),
    "ui.employees.list": frozenset({"employee.read"}),
    "ui.employees.new": frozenset({"employee.create"}),
    "ui.employees.form": frozenset({"employee.form.manage"}),
    "ui.employees.exit": frozenset({"employee.terminate", "employee.restore"}),
    "ui.employees.suspended": frozenset({"employee.suspend", "employee.restore"}),
    "ui.leave.view": frozenset({"leave.request.read", "leave.request.create"}),
    "ui.leave.request": frozenset({"leave.request.read", "leave.request.create"}),
    "ui.leave.policy": frozenset({"leave.type.manage"}),
    "ui.leave.types": frozenset({"leave.type.manage"}),
    "ui.leave.entitlements": frozenset({"leave.balance.read", "leave.balance.manage"}),
    "ui.overtime.view": frozenset({"overtime.request.read", "overtime.request.create"}),
    "ui.overtime.requests": frozenset({"overtime.request.read", "overtime.request.create"}),
    "ui.overtime.rules": frozenset({"overtime.rule.read", "overtime.rule.manage"}),
    "ui.shift.view": frozenset({"shift.read", "shift.assign", "shift.schedule.manage"}),
    "ui.shift.definitions": frozenset({"shift.schedule.manage"}),
    "ui.shift.assignments": frozenset({"shift.read", "shift.assign"}),
    "ui.approvals.view": frozenset({"leave.request.approve", "overtime.request.approve"}),
    "ui.roles.view": frozenset({"role.read", "role.manage"}),
}


def ui_permission_codes_for(permission_codes) -> set[str]:  # noqa: ANN001
    """Mevcut işlem izinlerinden geriye dönük uyumlu arayüz izinlerini üretir."""
    sahip_olunan = set(permission_codes)
    return {
        ui_kodu
        for ui_kodu, gerekliler in UI_PERMISSION_REQUIREMENTS.items()
        if sahip_olunan.intersection(gerekliler)
    }

# --- Platform izinleri (M6) --------------------------------------------------
# `platform.*` kodları **aynı katalogda** (`hr.permissions`) yaşar — ikinci bir
# katalog yoktur (OD3; docs/AUTHORIZATION.md §5.2 "Platform" satırı).
#
# ÖNEMLİ: Bu kodlar `ALL_PERMISSION_CODES`'a **girmez**. `tenant_admin` gibi kiracı
# şablonları tüm katalogu değil, yalnızca kiracı izinlerini alır; aksi halde her
# yeni kiracının yöneticisi platform izinlerine sahip olurdu (yetki yükseltme).
PLATFORM_PERMISSIONS: list[tuple[str, str, str]] = [
    ("platform.tenant.read", "Platform: kiracı bilgilerini görme (PII maskeli)", "platform"),
    ("platform.tenant.pii.read", "Platform: kiracı kişisel verilerini görme (SUPERADMIN)", "platform"),
    ("platform.tenant.create", "Platform: şirket ve şirket yöneticisi oluşturma (SUPERADMIN)", "platform"),
    ("platform.tenant.manage", "Platform: kiracı oluşturma/durum/limit yönetimi", "platform"),
    ("platform.settings.manage", "Platform: sistem ayarlarını yönetme", "platform"),
    ("platform.role.assign", "Platform: platform rolü atama (platform admini atama)", "platform"),
    ("platform.audit.read", "Platform: platform denetim kayıtlarını görme", "platform"),
    (
        "platform.support.manage",
        "Platform: destek oturumu yönetimi (onaylı/süreli/kayıtlı)",
        "platform",
    ),
]

PLATFORM_PERMISSION_CODES: list[str] = [kod for kod, _, _ in PLATFORM_PERMISSIONS]

# Kiracı oluşturulduğunda üretilen varsayılan roller.
# Nesne seviyesi kapsam (kendi kaydı / kendi ekibi) organizasyon modülüyle
# birlikte gelecek; buradaki izinler modül düzeyindedir.

# --- Kapsam (scope) — OD5 kapalı ✅ (M8) ------------------------------------
# `hr.roles.scope_type` = **rolün izinlerinin ulaşabileceği azami organizasyon
# kapsamı** (docs/AUTHORIZATION.md §4.4 + OD5 §5.1). Rol tipi (`role_type`:
# system/default/custom) ile **karıştırılmaz**: `role_type` rolün *nasıl
# oluştuğunu*, `scope_type` *neye kadar yetki verebileceğini* söyler.
#
# Kiracı rollerinde geçerli değerler §4.4'ten gelir; **`SYS` yalnızca platform
# rollerine** aittir (`platform.roles`, OD3) → `hr.roles` içinde kullanılamaz ve
# DB `CHECK` kısıtına da alınmaz:
#   * TENANT      → kiracı geneli (tenant_admin, tenant_officer, hr_manager, custom)
#   * UNIT        → atandığı birim + **tüm alt ağaç** (dept_manager; OD5)
#   * UNIT_DIRECT → yalnızca atandığı birim (alt ağaç hariç)
#   * ASSIGNED    → yalnızca açıkça atanmış kayıtlar
#   * SELF        → yalnızca kendi kaydı (employee)
# Kapalı küme ve kapsam-üreten davranışın kaynağı: `apps/rbac/models.py`
# (`RoleScopeType`, `TENANT_ROLE_SCOPE_TYPES`, `ORG_SCOPED_ROLE_SCOPE_TYPES`).

#: M7 göç haritası: eski rol kodu → hedef rol kodu (§4.2 geçiş planı).
LEGACY_ROLE_CODE_MAP: dict[str, str] = {"manager": "dept_manager"}

# --- Hedef izin setleri (§6.2 matrisinden; yalnız MEVCUT katalog kodları) ----
# Matriste bulunan ama katalogda henüz **olmayan** kodlar (ör. `approval.task.decide`,
# `audit.read.tenant`, `employee.personal_data.read`, `document.share`,
# `dataaccess.*`) ayrı iş kalemiyle eklenir (AUTHORIZATION §5.2 "eklenecek" + bölüm
# 13/6) — buraya **uydurulmaz**. Her kodun yanındaki bölüm/sütun etiketi matristeki
# kaynağıdır.
#
# M14 (izin modülü) ile matrisin **E. İzin** bölümünde tanımlı dört kod katalogda
# eksikti ve bu iş kalemiyle eklendi: `leave.request.cancel`,
# `leave.request.approve.override`, `leave.balance.read`, `leave.balance.manage`.
DEPT_MANAGER_PERMISSIONS: tuple[str, ...] = (
    "user.read",  # B: ◐ (UNIT)
    "org.department.read",  # C: ● (UNIT)
    "employee.read",  # D: ● (UNIT)
    "employee.update",  # D: ◐ (UNIT, sınırlı alanlar)
    "leave.request.read",  # E: ● (UNIT)
    "leave.request.create",  # E: ◐ (UNIT, kendi adına)
    "leave.request.approve",  # E: ◐ (UNIT + ASSIGNED + SEPARATE_ACTOR)
    "leave.request.cancel",  # E: ◐ (UNIT)
    "leave.balance.read",  # E: ● (UNIT)
    "shift.read",  # F: ● (UNIT)
    "shift.assign",  # F: ● (UNIT)
    "shift.schedule.manage",  # F: ◐ (UNIT)
    "attendance.read",  # F: ● (UNIT)
    "attendance.manage",  # F: ◐ (UNIT, gerekçeli)
    "document.read",  # G: ◐ (UNIT + POLICY_OK)
    "document.upload",  # G: ◐ (UNIT, çalışanı adına)
    "employee.history.read",  # D: ● (UNIT) — P1/OD61: atama geçmişi birim kapsamında
    # P5a/§11 + §19 (MSS): fazla mesai — onay birincil yöneticide, karar kayıtlıdır.
    "overtime.request.create",  # §18 ESS: kendi bildirimi
    "overtime.request.read",  # §19 MSS: ekip kayıtları (UNIT)
    "overtime.request.approve",  # §19 MSS: UNIT + ASSIGNED + SEPARATE_ACTOR
    "overtime.request.cancel",  # §11: kapsam içi iptal
)

#: ŞYT (şirket yetkilisi) sütunu — TENANT kapsamlı modül yetkileri.
#: Matriste `◐ ❓OD9` işaretli kodlar (`document.confidential.read`,
#: `user.permission_override.manage`) **alınmaz**: OD9 (devredilebilirlik sınırı)
#: açık olduğu için fail-closed davranılır. `document.share` bu katalogda yok.
TENANT_OFFICER_PERMISSIONS: tuple[str, ...] = (
    "user.read",  # B: ◐ (TENANT, yetkili modül)
    "role.read",  # B: ●
    "org.department.read",  # C: ●
    "org.department.manage",  # C: ◐
    "org.position.manage",  # C: ◐
    "employee.read",  # D: ● (TENANT)
    "employee.create",  # D: ◐
    "employee.update",  # D: ◐
    "leave.request.read",  # E: ● (TENANT)
    "leave.request.create",  # E: ●
    "leave.request.approve",  # E: ◐ (ASSIGNED)
    "leave.request.cancel",  # E: ◐
    "leave.balance.read",  # E: ● (TENANT)
    "shift.read",  # F: ● (TENANT)
    "shift.assign",  # F: ◐
    "shift.schedule.manage",  # F: ◐
    "attendance.read",  # F: ● (TENANT)
    "attendance.manage",  # F: ◐
    "document.read",  # G: ● (TENANT)
    "document.upload",  # G: ●
    "document.delete",  # G: ◐ (kendi yüklediği)
    "employee.history.read",  # D: ● — P1/OD61
    "org.workplace.read",  # C: ● — P1/OD61
    # P5a/§11: fazla mesai — şirket yetkilisi talep açar, karara bağlar, iptal eder.
    "overtime.request.create",
    "overtime.request.read",
    "overtime.request.approve",
    "overtime.request.cancel",
)

DEFAULT_ROLE_TEMPLATES: dict[str, dict] = {
    "tenant_admin": {
        "name": "Sistem Yöneticisi",
        "description": "Şirket genelinde tüm uygulama modüllerinde tam yetkilidir. Platform yönetim yetkisi vermez.",
        "role_type": "default",
        "scope_type": "TENANT",  # §6.1: ŞY sınırı TENANT
        "permissions": ALL_PERMISSION_CODES,
    },
    "tenant_officer": {
        "name": "Şirket Operasyon Yetkilisi",
        "description": "Şirket operasyonlarında tanımlı izinleri kullanır.",
        "role_type": "default",
        "scope_type": "TENANT",  # §6.1: ŞYT sınırı TENANT (modül kısıtlı)
        "permissions": list(TENANT_OFFICER_PERMISSIONS),
    },
    "hr_manager": {
        "name": "İnsan Kaynakları Yöneticisi",
        "description": "İnsan kaynakları işlemlerini şirket genelinde yönetir.",
        "role_type": "default",
        # Şablon rol olarak korunur (§4.2); tüm izinleri matriste TENANT kapsamlı.
        "scope_type": "TENANT",
        "permissions": [
            "tenant.read",
            "user.read",
            "role.read",
            "org.department.read",
            "org.department.manage",
            "org.position.manage",
            "employee.read",
            "employee.create",
            "employee.update",
            "employee.photo.upload",
            "employee.sensitive.read",
            "employee.sensitive.write",
            "employee.suspend",
            "employee.terminate",
            "employee.restore",
            "employee.history.read",
            "employee.form.manage",
            "org.workplace.read",
            "org.workplace.manage",
            "leave.request.read",
            "leave.request.approve",
            "leave.type.manage",
            # P5a/§11: İK, yönetici adımından sonraki onay adımının sahibidir (OD39).
            "overtime.request.read",
            "overtime.request.approve",
            "overtime.request.cancel",
            "overtime.rule.read",
            "overtime.rule.manage",
            "shift.read",
            "shift.assign",
            "shift.schedule.manage",
            "attendance.read",
            "attendance.manage",
            "document.read",
            "document.upload",
            "document.confidential.read",
            "audit.read",
        ],
    },
    "dept_manager": {
        "name": "Departman Sorumlusu",
        "description": "Atandığı departmanda ve alt departmanlarında yetkilidir.",
        "role_type": "default",
        "scope_type": "UNIT",  # OD5: atandığı birim + tüm alt ağaç
        "permissions": list(DEPT_MANAGER_PERMISSIONS),
    },
    "shift_manager": {
        "name": "Vardiya Sorumlusu",
        "description": "Atandığı departman ve alt departmanlarının vardiya planını yönetir.",
        "role_type": "default",
        "scope_type": "UNIT",
        "permissions": [
            "org.department.read",
            "employee.read",
            "shift.read",
            "shift.assign",
            "shift.schedule.manage",
        ],
    },
    "employee": {
        "name": "Çalışan",
        "description": "Çalışan kendi hesabına ve kendisine açık işlemlere erişir.",
        "role_type": "default",
        "scope_type": "SELF",  # §6.1: ÇALIŞAN sınırı SELF
        "permissions": [
            "org.department.read",
            "employee.read",
            "leave.request.read",
            "leave.request.create",
            "leave.request.cancel",
            "leave.balance.read",
            # P5a/§18 ESS: çalışan kendi fazla mesaisini bildirir/görür/iptal eder.
            "overtime.request.create",
            "overtime.request.read",
            "overtime.request.cancel",
            "shift.read",
            "document.read",
        ],
    },
}

# --- Devredilebilirlik (OD9 — kapalı 2026-09-13) ------------------------------
#: Kiracı içinde **özel role verilemeyen** izin kodları.
#:
#: Gerekçe (OD9): yüksek etkili işlemler (arşivleme, işten çıkış, kişisel veri,
#: form konfigürasyonu, bakiye, rol/kiracı/kullanıcı yönetimi, denetim) yalnızca
#: **sistem rol şablonlarında** bulunur; özel rollerle yetki büyümesi engellenir.
#: Servis katmanı bu kodları özel role eklemeye çalışırsa **reddeder** (fail-closed).
NON_DELEGABLE_PERMISSION_CODES: frozenset[str] = frozenset(
    {
        "role.manage",
        "tenant.manage",
        "user.manage",
        "employee.delete",
        "employee.suspend",
        "employee.terminate",
        "employee.restore",
        "employee.sensitive.read",
        "employee.sensitive.write",
        "employee.form.manage",
        "leave.balance.manage",
        "audit.read",
    }
)

#: Özel role verilebilen kodlar (tümü eksi devredilemezler) — UI/matris için.
DELEGABLE_PERMISSION_CODES: tuple[str, ...] = tuple(
    kod for kod in ALL_PERMISSION_CODES if kod not in NON_DELEGABLE_PERMISSION_CODES
)


def is_delegable(permission_code: str) -> bool:
    """Kod özel role verilebilir mi? (OD9 — bilinmeyen kod fail-closed: hayır.)"""
    return permission_code in DELEGABLE_PERMISSION_CODES
