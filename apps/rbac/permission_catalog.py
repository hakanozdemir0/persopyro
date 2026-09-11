"""
İzin kataloğu ve varsayılan rol şablonları (canlı kaynak).

Kod biçimi: `<kaynak>.<işlem>` (docs/AUTHORIZATION.md bölüm 4).

NOT: Veri migration'ı (`rbac/migrations/0003_seed_permissions.py`) bu listenin
DONMUŞ bir kopyasını taşır — uygulanmış migration'lar sonradan değişmez. Buraya
yeni izin eklenirse, mevcut veritabanları için YENİ bir migration yazılmalıdır.
"""

# (kod, ad, modül)
PERMISSIONS: list[tuple[str, str, str]] = [
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
    # Çalışan
    ("employee.read", "Çalışanları görme", "employee"),
    ("employee.create", "Çalışan ekleme", "employee"),
    ("employee.update", "Çalışan güncelleme", "employee"),
    ("employee.delete", "Çalışan silme/arşivleme", "employee"),
    ("employee.sensitive.read", "Hassas çalışan verisini görme (maskesiz)", "employee"),
    # İzin
    ("leave.request.read", "İzin taleplerini görme", "leave"),
    ("leave.request.create", "İzin talebi oluşturma", "leave"),
    ("leave.request.approve", "İzin talebi onaylama/reddetme", "leave"),
    ("leave.type.manage", "İzin türü yönetimi", "leave"),
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

ALL_PERMISSION_CODES: list[str] = [kod for kod, _, _ in PERMISSIONS]

# --- Platform izinleri (M6) --------------------------------------------------
# `platform.*` kodları **aynı katalogda** (`hr.permissions`) yaşar — ikinci bir
# katalog yoktur (OD3; docs/AUTHORIZATION.md §5.2 "Platform" satırı).
#
# ÖNEMLİ: Bu kodlar `ALL_PERMISSION_CODES`'a **girmez**. `tenant_admin` gibi kiracı
# şablonları tüm katalogu değil, yalnızca kiracı izinlerini alır; aksi halde her
# yeni kiracının yöneticisi platform izinlerine sahip olurdu (yetki yükseltme).
PLATFORM_PERMISSIONS: list[tuple[str, str, str]] = [
    ("platform.tenant.read", "Platform: kiracı bilgilerini görme (PII maskeli)", "platform"),
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
DEFAULT_ROLE_TEMPLATES: dict[str, dict] = {
    "tenant_admin": {
        "name": "Kiracı Yöneticisi",
        "role_type": "default",
        "permissions": ALL_PERMISSION_CODES,
    },
    "hr_manager": {
        "name": "İK Yöneticisi",
        "role_type": "default",
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
            "employee.sensitive.read",
            "leave.request.read",
            "leave.request.approve",
            "leave.type.manage",
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
    "manager": {
        "name": "Yönetici",
        "role_type": "default",
        "permissions": [
            "org.department.read",
            "employee.read",
            "leave.request.read",
            "leave.request.approve",
            "shift.read",
            "attendance.read",
            "document.read",
        ],
    },
    "employee": {
        "name": "Çalışan",
        "role_type": "default",
        "permissions": [
            "org.department.read",
            "employee.read",
            "leave.request.read",
            "leave.request.create",
            "shift.read",
            "document.read",
        ],
    },
}
