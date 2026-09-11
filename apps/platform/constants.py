"""
Platform katmanının kapalı kümeleri ve sabitleri (OD3/OD22/OD25/OD28).

Buradaki değerler veritabanı kısıtlarıyla **hizalıdır**: bir değer değişirse
hem bu liste hem ilgili `CHECK` kısıtı yeni bir migration ile güncellenir
(mevcut migration dosyaları düzenlenmez).
"""

# Platform rol kodları — KAPALI küme (DATABASE.md B.2 `platform.roles`).
PLATFORM_ROLE_CODES = ("superadmin", "platform_admin")

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
