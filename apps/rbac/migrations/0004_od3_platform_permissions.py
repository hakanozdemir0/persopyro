# M6 (OD3 kapalı) — platform izin kataloğu.
#
# `platform.*` izin kodları **mevcut katalogda** (`hr.permissions`) tanımlanır;
# ikinci bir katalog AÇILMAZ (docs/AUTHORIZATION.md §5.2 "Platform" satırı ve
# docs/DATABASE.md M6).
#
# Bu migration iki iş yapar:
#   1. Altı `platform.*` kodunu **frozen** (donmuş) listeyle ekler: sabit, benzersiz,
#      denetlenebilir, joker içermez. Kodlar `hr.permissions.code` UNIQUE kısıtına
#      tabidir → çift kayıt üretilemez.
#   2. `hr.permissions` üzerinde uygulama rolünün (`persopyro_app`) yazma yetkilerini
#      geri alır: global katalog **salt okunur** olmalıdır. ÖLÇÜM (migration öncesi):
#      `persopyro_app` bu tabloda SELECT/INSERT/UPDATE/DELETE yetkilerine sahipti
#      (`ALTER DEFAULT PRIVILEGES` yüzünden); kiracı runtime yolunun katalog
#      satırlarını değiştirebilmesi yetki yükseltme yüzeyi demektir.
#
# `platform.*` kodları kiracı şablon rollerine **girmez** (`apps/rbac/permission_catalog.py`
# içindeki `PLATFORM_PERMISSIONS` ayrı tutulur; `ALL_PERMISSION_CODES` değişmez).
#
# Geri alma: kodlar silinebilir (yeni uygulanmış bağ yoksa); yetki geri verilebilir
# (istenmez).

from django.db import migrations

# DONMUŞ kopya — `apps/rbac/permission_catalog.py` canlı kaynaktır, buradaki liste
# uygulanmış migration'ın tarihsel kaydıdır (kural: uygulanmış migration değişmez).
PLATFORM_PERMISSIONS_FROZEN = (
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
)

PERMISSION_WRITE_REVOKE = """
DO $$
BEGIN
    IF to_regclass('hr.permissions') IS NULL THEN
        RETURN;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'persopyro_app') THEN
        -- Global izin kataloğu SALT OKUNUR: kiracı runtime yolu satır ekleyemez,
        -- güncelleyemez, silemez (aksi halde izin kataloğu yetki yükseltme yüzeyi olur).
        REVOKE INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER
            ON hr.permissions FROM persopyro_app;
    END IF;
END
$$;
"""

PERMISSION_WRITE_RESTORE = """
DO $$
BEGIN
    IF to_regclass('hr.permissions') IS NULL THEN
        RETURN;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'persopyro_app') THEN
        GRANT SELECT, INSERT, UPDATE, DELETE ON hr.permissions TO persopyro_app;
    END IF;
END
$$;
"""


def platform_izinlerini_ekle(apps, schema_editor):
    """
    `platform.*` kodlarını ekler (idempotent).

    Var olan kod **atlanır** (çift kayıt yok). Silinmiş bir katalog satırı kavramı
    yoktur: `hr.permissions` soft-delete taşımaz (kod kalıcıdır).
    """
    Permission = apps.get_model("rbac", "Permission")
    eklenen = []
    for kod, ad, modul in PLATFORM_PERMISSIONS_FROZEN:
        _, olustu = Permission.objects.get_or_create(
            code=kod,
            defaults={"name": ad, "module": modul, "description": ""},
        )
        if olustu:
            eklenen.append(kod)
    if eklenen:
        print(f"    platform izin kodları eklendi: {', '.join(eklenen)}")


def platform_izinlerini_sil(apps, schema_editor):
    """Geri alma: yalnızca bu migration'ın eklediği kodlar silinir."""
    Permission = apps.get_model("rbac", "Permission")
    Permission.objects.filter(code__in=[kod for kod, _, _ in PLATFORM_PERMISSIONS_FROZEN]).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("rbac", "0003_seed_permissions"),
    ]

    operations = [
        migrations.RunPython(platform_izinlerini_ekle, platform_izinlerini_sil),
        migrations.RunSQL(PERMISSION_WRITE_REVOKE, PERMISSION_WRITE_RESTORE),
    ]
