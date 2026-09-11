# OD19 (kapalı) — Django Admin / `auth_*` paralel yetki yolunun kapatılması.
#
# Bu migration iki iş yapar:
#   * **M22c:** `is_staff` invariantı — veritabanı kısıtı (`users_is_staff_disabled`)
#     ve ölçüm adımı: mevcut `is_staff=True` kayıtları **körlemesine
#     değiştirilmez**; varsa migration açık hata ile DURUR (operatör karar verir).
#   * **M22b:** `auth_permission`, `auth_group`, `auth_group_permissions`,
#     `django_admin_log` tablolarında uygulama rolünün **tüm** yetkileri geri
#     alınır. Tablolar teknik olarak kalabilir (`django.contrib.auth`); **izin
#     kaynağı ve yazma yolu olamaz**.
#
# NEDEN AYNI SQL `scripts/02_grant_app_privileges.sql` İÇİNDE DE VAR:
# o betik `ALTER DEFAULT PRIVILEGES` + "ALL TABLES" nedeniyle her migrate
# sonrasında tablolara geniş DML verir; daraltma orada tekrarlanmazsa sessizce
# geri açılırdı (ölçüldü). İki yer bilinçli olarak aynı kuralı uygular.
#
# NOT: `django.contrib.admin` uygulama listesinden çıkarıldığı için **yeni
# kurulumlarda `django_admin_log` tablosu hiç oluşmaz**; bu yüzden REVOKE
# adımları `to_regclass` ile korunur (var olan tabloya uygulanır, olmayana
# dokunmaz). Mevcut kurulumda tablo boş olarak kalır — silinmez (geri dönüşü
# kolay, veri kaybı yok).

from django.conf import settings
from django.db import migrations, models

# OD19: uygulama rolü bu tablolarda hiçbir yetkiye sahip olmamalıdır.
DJANGO_TEKNIK_TABLOLAR = (
    "auth_permission",
    "auth_group",
    "auth_group_permissions",
    "django_admin_log",
)


def _rol(apps, schema_editor) -> str:  # noqa: ANN001, ARG001
    return schema_editor.connection.ops.quote_name(settings.APP_DB_ROLE)


def is_staff_olcumu(apps, schema_editor) -> None:  # noqa: ANN001, ARG001
    """
    M22c ön koşulu: mevcut `is_staff=True` kayıtlarını **ölçer** (değiştirmez).

    Kayıt bulunursa migration açık hata ile durur: bayrağı sessizce `False` yapmak
    "kim admin yetkisine sahipti" bilgisini kanıtsız silerdi. Ölçüm, RLS nedeniyle
    kiracı bağlamı kurularak yapılır (kiracı listesi RLS'siz `tenant_directory`'den).
    """
    with schema_editor.connection.cursor() as cur:
        cur.execute("SELECT tenant_id, slug FROM hr.tenant_directory ORDER BY slug")
        kiraclar = cur.fetchall()

        ihlaller: list[str] = []
        for tenant_id, slug in kiraclar:
            cur.execute(
                "SELECT set_config('app.tenant_id', %s, true)", [str(tenant_id)]
            )
            cur.execute("SELECT email FROM hr.users WHERE is_staff ORDER BY email")
            ihlaller.extend(f"{slug}/{email}" for (email,) in cur.fetchall())

    if ihlaller:
        raise RuntimeError(
            "OD19 ön koşulu ihlal edildi: `is_staff=True` olan kullanıcı(lar) var: "
            + ", ".join(ihlaller)
            + ". Bayrak körlemesine değiştirilmedi; önce bu hesapları inceleyip "
            "`is_staff=False` yapın (Django Admin kaldırıldı, bu bayrak hiçbir yetki "
            "vermez), sonra migration'ı yeniden çalıştırın."
        )


def django_teknik_yetkileri_kapat(apps, schema_editor) -> None:  # noqa: ANN001, ARG001
    """M22b: uygulama rolünün Django teknik tablolarındaki tüm yetkilerini geri al."""
    rol = _rol(apps, schema_editor)
    tablolar = ", ".join(f"'{t}'" for t in DJANGO_TEKNIK_TABLOLAR)
    sql = f"""
DO $$
DECLARE tablo text;
BEGIN
    FOREACH tablo IN ARRAY ARRAY[{tablolar}] LOOP
        IF to_regclass('hr.' || tablo) IS NOT NULL THEN
            EXECUTE format('REVOKE ALL ON hr.%I FROM {rol}', tablo);
        END IF;
    END LOOP;
END
$$;
"""
    with schema_editor.connection.cursor() as cur:
        cur.execute(sql)


def django_teknik_yetkileri_geri_ver(apps, schema_editor) -> None:  # noqa: ANN001, ARG001
    """
    Geri alma (istenmeyen): yetkiler eski hâline döner.

    OD19 kararı gereği bu geri alma **üretimde kullanılmamalıdır**; yalnızca
    migration'ın geri alınabilir olması için vardır.
    """
    rol = _rol(apps, schema_editor)
    with schema_editor.connection.cursor() as cur:
        for tablo in DJANGO_TEKNIK_TABLOLAR:
            cur.execute(
                "SELECT to_regclass('hr.' || %s) IS NOT NULL", [tablo]
            )
            if cur.fetchone()[0]:
                cur.execute(
                    f"GRANT SELECT, INSERT, UPDATE, DELETE ON hr.{tablo} TO {rol}"
                )


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0003_apitoken_od26"),
        ("tenancy", "0007_tenant_directory"),
        # Yetki geri alma, Django `auth` tabloları oluştuktan SONRA çalışmalıdır;
        # aksi halde sıfırdan kurulumda tablolar henüz yokken REVOKE atlanırdı.
        ("auth", "0012_alter_user_first_name_max_length"),
    ]

    operations = [
        # Ölçüm önce: ihlal varsa migration burada durur (kısıt eklenmeden).
        migrations.RunPython(is_staff_olcumu, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="user",
            name="is_staff",
            field=models.BooleanField(
                default=False,
                help_text=(
                    "OD19 (kapalı): Django Admin kaldırılmıştır. Bu bayrak hiçbir "
                    "Persopyro yetkisi VERMEZ ve üretimde True olamaz (uygulama guard'ı "
                    "+ DB kısıtı). Alan, Django uyumluluğu için şemada tutulur."
                ),
                verbose_name="yönetim paneline erişebilir (KULLANILMIYOR)",
            ),
        ),
        migrations.AddConstraint(
            model_name="user",
            constraint=models.CheckConstraint(
                condition=models.Q(("is_staff", False)), name="users_is_staff_disabled"
            ),
        ),
        migrations.RunPython(
            django_teknik_yetkileri_kapat, django_teknik_yetkileri_geri_ver
        ),
    ]
