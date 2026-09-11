# Şema kurulumunu içeren ilk migration (elle düzenlendi).
#
# NEDEN ELLE EKLENDİ: `hr` şeması ve `hr.current_tenant_id()` fonksiyonu, ilk
# tablo oluşturulmadan ÖNCE var olmalıdır. Tüm iş tabloları `search_path=hr,public`
# sayesinde `hr` içinde oluşur; RLS politikaları da bu fonksiyonu kullanır.
#
# Geri alma (reverse) uyarısı: bu migration geri alınırsa `hr` şeması CASCADE ile
# silinir, yani şema içindeki TÜM veri kaybolur. Bu, ilk kurulum migration'ı
# olduğu için kabul edilmiştir.

import uuid

from django.db import migrations, models

CREATE_SCHEMA_AND_HELPER = """
CREATE SCHEMA IF NOT EXISTS hr;

-- Kiracı bağlamını okuyan yardımcı fonksiyon.
-- NOT: NULLIF şarttır; PostgreSQL, SET LOCAL ile tanışan özel bir parametreyi
-- işlem sonunda NULL'a değil BOŞ STRING'e döndürür (bkz. docs/CHANGELOG.md 0.4.0).
CREATE OR REPLACE FUNCTION hr.current_tenant_id() RETURNS uuid
LANGUAGE sql STABLE AS $$
    SELECT NULLIF(current_setting('app.tenant_id', TRUE), '')::uuid
$$;

COMMENT ON FUNCTION hr.current_tenant_id() IS
    'Aktif kiracı kimliği (RLS politikaları bu fonksiyonu kullanır).';
"""

DROP_SCHEMA_AND_HELPER = """
DROP FUNCTION IF EXISTS hr.current_tenant_id();
DROP SCHEMA IF EXISTS hr CASCADE;
"""


class Migration(migrations.Migration):

    initial = True

    dependencies = [
    ]

    operations = [
        migrations.RunSQL(
            sql=CREATE_SCHEMA_AND_HELPER,
            reverse_sql=DROP_SCHEMA_AND_HELPER,
        ),
        migrations.CreateModel(
            name='Tenant',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('name', models.CharField(max_length=200, verbose_name='şirket adı')),
                ('legal_name', models.CharField(blank=True, max_length=250, verbose_name='tüzel unvan')),
                ('tax_number', models.CharField(blank=True, max_length=50, verbose_name='vergi numarası')),
                ('email', models.EmailField(blank=True, max_length=190, verbose_name='e-posta')),
                ('phone', models.CharField(blank=True, max_length=30, verbose_name='telefon')),
                ('country', models.CharField(blank=True, max_length=100, verbose_name='ülke')),
                ('city', models.CharField(blank=True, max_length=100, verbose_name='şehir')),
                ('address', models.TextField(blank=True, verbose_name='adres')),
                ('status', models.CharField(choices=[('active', 'Aktif'), ('trial', 'Deneme'), ('suspended', 'Askıda'), ('canceled', 'İptal')], default='trial', max_length=20, verbose_name='durum')),
                ('max_users', models.PositiveIntegerField(default=10, verbose_name='kullanıcı limiti')),
                ('max_storage_mb', models.DecimalField(decimal_places=2, default=500, max_digits=12, verbose_name='depolama limiti (MB)')),
                ('settings', models.JSONField(blank=True, default=dict, verbose_name='ayarlar')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='oluşturma')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='güncelleme')),
                ('deleted_at', models.DateTimeField(blank=True, null=True, verbose_name='silinme')),
            ],
            options={
                'verbose_name': 'kiracı',
                'verbose_name_plural': 'kiracılar',
                'db_table': 'tenants',
                'indexes': [models.Index(fields=['status'], name='tenants_status_idx')],
                'constraints': [models.CheckConstraint(condition=models.Q(('max_users__gte', 0)), name='tenants_max_users_nonneg'), models.CheckConstraint(condition=models.Q(('max_storage_mb__gte', 0)), name='tenants_max_storage_mb_nonneg')],
            },
        ),
    ]
