# M7 (OD3 kapalı) — platform default rolleri ve izin bağları.
#
# `platform.roles` satırları M2'de oluşturulmuştu (`superadmin`, `platform_admin`);
# bu migration **izin bağlarını** kurar (docs/AUTHORIZATION.md §6.2A matrisi) ve
# rollerin varlığını idempotent biçimde garanti eder.
#
# Kurallar:
#   * **İkinci katalog yok:** izinler `hr.permissions` içindeki `platform.*` kodlarıdır
#     (M6). Kod eksikse **sessizce geçilmez** → "izin verildi sanılan ama verilmeyen"
#     güvenlik açığı oluşur (kiracı tarafındaki `MissingPermissionError` ile aynı ilke).
#   * **İzin devralma YOK** (AUTHORIZATION.md §4.3): her rolün kümesi açık bağlarla
#     yazılır; `superadmin ⊃ platform_admin` bir sonuçtur, mekanizma değildir.
#   * **Idempotent:** var olan bağ atlanır; çift kayıt üretilmez (UNIQUE korur).
#   * **Sessiz restore YOK:** soft-delete edilmiş rol veya rol-izin bağı bulunursa
#     migration **açık hata** verir (OD21/K25 ilkesi; otomatik canlandırma yapılmaz).
#   * **SUPERADMIN kişisel veri erişimi KAZANMAZ:** platform izinleri PII erişimi
#     vermez; kiracı verisi yalnızca erişim talebi/onayı (AccessGrant) ile açılır
#     (OD1/OD3/OD4 — bu paketin kapsamı dışı).
#
# Geri alma: yalnızca bu migration'ın kurduğu izin bağları silinir (roller korunur;
# onlar M2'nin nesnesidir).

from django.db import migrations

from apps.platform.constants import PLATFORM_ROLE_PERMISSIONS

ROLLER = ("superadmin", "platform_admin")


def _rolleri_ve_baglari_kur(cur) -> None:
    for kod in ROLLER:
        cur.execute("SELECT id, deleted_at FROM platform.roles WHERE code = %s", [kod])
        satir = cur.fetchone()
        if satir is None:
            cur.execute(
                "INSERT INTO platform.roles (code, name) VALUES (%s, %s) RETURNING id",
                [kod, kod],
            )
            rol_id = cur.fetchone()[0]
        else:
            rol_id, deleted_at = satir
            if deleted_at is not None:
                raise RuntimeError(
                    f"platform.roles.{kod} soft-delete edilmiş durumda; otomatik geri "
                    "alınmaz (OD21/K25). Açık bir geri alma işlemi gerekir."
                )

        for izin_kodu in PLATFORM_ROLE_PERMISSIONS[kod]:
            cur.execute("SELECT id FROM hr.permissions WHERE code = %s", [izin_kodu])
            izin = cur.fetchone()
            if izin is None:
                raise RuntimeError(
                    f"İzin kodu katalogda yok: {izin_kodu} (rbac/0004_od3_platform_permissions "
                    "uygulanmamış olabilir). Eksik izin sessizce geçilemez."
                )
            izin_id = izin[0]

            cur.execute(
                "SELECT deleted_at FROM platform.role_permissions"
                " WHERE role_id = %s AND permission_id = %s",
                [rol_id, izin_id],
            )
            mevcut = cur.fetchone()
            if mevcut is not None:
                if mevcut[0] is not None:
                    raise RuntimeError(
                        f"platform.role_permissions bağı soft-delete edilmiş: {kod} → "
                        f"{izin_kodu}; otomatik geri alınmaz (OD21/K25)."
                    )
                continue  # idempotent: var olan etkin bağ atlanır

            cur.execute(
                "INSERT INTO platform.role_permissions (role_id, permission_id)"
                " VALUES (%s, %s)",
                [rol_id, izin_id],
            )


KATALOG_YETKISI = """
-- Platform rolü, paylaşılan izin kataloğunu (`hr.permissions`) SALT OKUNUR görür
-- (platform.role_permissions yönetimi için gerekir). Üretimde aynı yetkiyi
-- `scripts/03_platform_roles.sql` (süper kullanıcı) verir; burada da verilmesinin
-- nedeni test/sıfırdan kurulum ile üretimin **aynı** sonucu üretmesidir.
DO $$
BEGIN
    IF to_regclass('hr.permissions') IS NOT NULL
       AND EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'persopyro_platform') THEN
        GRANT USAGE ON SCHEMA hr TO persopyro_platform;
        REVOKE ALL ON hr.permissions FROM persopyro_platform;
        GRANT SELECT ON hr.permissions TO persopyro_platform;
    END IF;
END
$$;
"""

KATALOG_YETKISI_GERI_AL = """
DO $$
BEGIN
    IF to_regclass('hr.permissions') IS NOT NULL
       AND EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'persopyro_platform') THEN
        REVOKE ALL ON hr.permissions FROM persopyro_platform;
    END IF;
END
$$;
"""


def baglari_kur(apps, schema_editor):  # noqa: ARG001
    with schema_editor.connection.cursor() as cur:
        _rolleri_ve_baglari_kur(cur)


def baglari_sil(apps, schema_editor):  # noqa: ARG001
    with schema_editor.connection.cursor() as cur:
        for kod in ROLLER:
            cur.execute(
                """
                DELETE FROM platform.role_permissions rp
                 USING platform.roles r, hr.permissions p
                 WHERE rp.role_id = r.id AND rp.permission_id = p.id
                   AND r.code = %s AND p.code = ANY(%s)
                """,
                [kod, list(PLATFORM_ROLE_PERMISSIONS[kod])],
            )


class Migration(migrations.Migration):
    dependencies = [
        ("platform", "0004_od3_platform_soft_delete"),
        ("rbac", "0004_od3_platform_permissions"),
    ]

    operations = [
        migrations.RunPython(baglari_kur, baglari_sil),
        migrations.RunSQL(KATALOG_YETKISI, KATALOG_YETKISI_GERI_AL),
    ]
