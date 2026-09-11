"""
Platform katmanının kapalı kümeleri ve sabitleri (OD3/OD22/OD25/OD28).

Buradaki değerler veritabanı kısıtlarıyla **hizalıdır**: bir değer değişirse
hem bu liste hem ilgili `CHECK` kısıtı yeni bir migration ile güncellenir
(mevcut migration dosyaları düzenlenmez).
"""

# Platform rol kodları — KAPALI küme (DATABASE.md B.2 `platform.roles`).
PLATFORM_ROLE_CODES = ("superadmin", "platform_admin")

# --- Roller ve izin kümeleri (M7; docs/AUTHORIZATION.md §6.2A) ----------------
# İKİNCİ KATALOG DEĞİL: kodlar `hr.permissions` içindedir; burası yalnızca hangi
# platform rolünün hangi kodlara bağlandığını tanımlar. `superadmin ⊃ platform_admin`
# olsa bile **izin devralma (inheritance) YOKTUR** (AUTHORIZATION.md §4.3): her rolün
# kümesi açıkça yazılır ve çözümleyici yalnızca açık bağları okur.
PLATFORM_PERMISSION_PREFIX = "platform."

PLATFORM_ROLE_PERMISSIONS = {
    # SUPERADMIN: katalogdaki tüm platform izinleri (6/6).
    "superadmin": (
        "platform.tenant.read",
        "platform.tenant.manage",
        "platform.settings.manage",
        "platform.role.assign",
        "platform.audit.read",
        "platform.support.manage",
    ),
    # ADMIN: platform operasyonu; PII yok. `settings.manage` ve `role.assign` YOK
    # (platform ayarlarını değiştiremez, platform rolü dağıtamaz → kendini
    # superadmin yapamaz).
    "platform_admin": (
        "platform.tenant.read",
        "platform.tenant.manage",
        "platform.audit.read",
        "platform.support.manage",
    ),
}

# Yönetim hiyerarşisi (izin devralma DEĞİL): "üst rol, alt rolü atayabilir".
# Atama için ek koşullar `apps/platform/permissions.py` içinde uygulanır:
# aktör `platform.role.assign` yetkisine sahip olmalı VE atayacağı rolün tüm
# izinlerini kendisi taşımalıdır (sahip olmadığı yetkiyi dağıtamaz).
PLATFORM_ROLE_RANK = {
    "platform_admin": 1,
    "superadmin": 2,
}

# Self-elevation ve atama guard'ları için tek yetki kodu (AUTHORIZATION.md §6.2A).
PLATFORM_ROLE_ASSIGN_PERMISSION = "platform.role.assign"

# Platform kullanıcısı public ID ön eki (OD27/OD28): kiracı ad alanından ayrı.
PLATFORM_PUBLIC_ID_PREFIX = "plt"

# Platform token türleri — KAPALI küme (DATABASE.md M3 `token_kind` + CHECK).
# Tek kaynak aşağıdaki demettir; tekil adlar ondan türetilir. Değerler SIR DEĞİL,
# kısıtla hizalı enum değerleridir (bu yüzden "olası sabit parola" bulgusu
# üretmesinler diye türetme tercih edildi).
PLATFORM_TOKEN_KINDS = ("interactive", "service")
PLATFORM_TOKEN_KIND_INTERACTIVE = PLATFORM_TOKEN_KINDS[0]
PLATFORM_TOKEN_KIND_SERVICE = PLATFORM_TOKEN_KINDS[1]

# Platform token TTL tavanları (OD22, saat). Süresiz token YOKTUR.
PLATFORM_TOKEN_TTL_MAX_HOURS = {
    PLATFORM_TOKEN_KIND_INTERACTIVE: 24,  # insan oturumu: ≤ 24 saat
    PLATFORM_TOKEN_KIND_SERVICE: 2160,  # entegrasyon: ≤ 90 gün
}
# Varsayılan TTL (açıkça verilmezse): tavandan kısa, makul bir ömür.
PLATFORM_TOKEN_DEFAULT_TTL_HOURS = {
    PLATFORM_TOKEN_KIND_INTERACTIVE: 8,
    PLATFORM_TOKEN_KIND_SERVICE: 720,
}

# Platform token'ında yasak olan kapsam kalıpları (OD22: sınırsız token yok).
PLATFORM_TOKEN_FORBIDDEN_SCOPE_MARKERS = ("*",)

# Platform token iptal nedenleri — KAPALI küme (OD22 madde 7 + OD26 simetrisi).
PLATFORM_TOKEN_REVOKE_REASONS = (
    "logout",
    "owner_disabled",
    "admin_revoke",
    "security_incident",
    "policy_migration",
    "role_changed",
    "mfa_changed",
    "password_changed",
)

# Token sahibinin bu izin kümesiyle `service` token üretmesi yasaktır (OD22/6).
SUPERADMIN_ROLE_CODE = "superadmin"
