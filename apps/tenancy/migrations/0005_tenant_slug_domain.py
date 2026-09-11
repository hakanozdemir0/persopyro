# Tenant.slug ve Tenant.domain eklenmesi (elle yazılmıştır).
#
# NEDEN ELLE: `slug` zorunlu ve tekil bir alandır; mevcut satırlar için varsayılan
# değer istenmesin diye üç adımda eklenir (null ekle → doldur → NOT NULL yap).
#
# ÖLÇÜLMÜŞ TUZAK: İlk adımda `SlugField` kullanılırsa Django, PostgreSQL'de
# SlugField'ın varsayılan `db_index=True` davranışı nedeniyle
# `tenants_slug_<hash>_like` indeksini oluşturur; ardından `AlterField` aynı
# indeksi yeniden yaratmaya çalışır ve "relation already exists" hatası verir.
# Bu yüzden ilk adımda `CharField`, son adımda `SlugField` kullanılır: indeks ve
# tekil kısıt tek seferde, son adımda oluşur.
#
# `domain` için böyle bir sorun yoktur (CharField'ın varsayılan indeksi yoktur).

from django.db import migrations, models
from django.utils.text import slugify


def _fill_slugs(apps, schema_editor):
    """Mevcut kiracılar için addan türetilmiş benzersiz slug üretir."""
    Tenant = apps.get_model("tenancy", "Tenant")
    for tenant in Tenant.objects.filter(slug__isnull=True).order_by("created_at"):
        base = slugify(tenant.name)[:50] or "kiraci"
        slug = base
        sayac = 1
        while Tenant.objects.filter(slug=slug).exists():
            sayac += 1
            slug = f"{base}-{sayac}"
        tenant.slug = slug
        tenant.save(update_fields=["slug"])


class Migration(migrations.Migration):

    dependencies = [
        ("tenancy", "0004_rls_role_permissions"),
    ]

    operations = [
        migrations.AddField(
            model_name="tenant",
            name="slug",
            field=models.CharField(
                max_length=60,
                null=True,
                help_text="Kiracıyı tanımlayan kısa ad; giriş ve alt alan adı için kullanılır.",
                verbose_name="kısa ad (slug)",
            ),
        ),
        migrations.RunPython(_fill_slugs, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="tenant",
            name="slug",
            field=models.SlugField(
                max_length=60,
                unique=True,
                help_text="Kiracıyı tanımlayan kısa ad; giriş ve alt alan adı için kullanılır.",
                verbose_name="kısa ad (slug)",
            ),
        ),
        migrations.AddField(
            model_name="tenant",
            name="domain",
            field=models.CharField(
                blank=True,
                max_length=190,
                null=True,
                unique=True,
                help_text="Varsa bu alan adı doğrudan kiracıya çözümlenir (ör. ik.acme.com).",
                verbose_name="özel alan adı",
            ),
        ),
    ]
