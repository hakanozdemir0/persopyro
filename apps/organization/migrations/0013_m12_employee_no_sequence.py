"""
M12 genişletme (4) — **çalışan numarası sequence'i** (`hr.employee_no_seq`).

Neden: kullanıcı bildirimi "yeni çalışan oluştururken kullanıcıdan çalışan
numarası isteme; otomatik olsun, concurrency-safe üretilsin, frontend
hesaplamasın". Numarayı üretmenin **atomik** yolu veritabanı `sequence`'idir:
`nextval` iki eşzamanlı istekte aynı değeri vermez ve satır kilidi beklemez
(uygulama tarafında `max()+1` veya sayaç tablosu bu garantiyi vermez).

Kararlar:
  * Format: `EMP-000001` (prefix + 6 hane). Bu bir **sicil numarasıdır**;
    `public_id` **değildir** (OD2/OD25 ile karıştırılmaz).
  * Sequence **global**dir (kiracı bazlı DEĞİL): global benzersizlik kiracı içi
    benzersizliği kapsar (kısmi `(tenant_id, employee_no)` kısıtı yine yürürlükte).
    Numara bir kişisel veri değildir; kiracılar arasında paylaşılması veri
    sızdırmaz.
  * **Birimden bağımsızdır:** çalışan başka birime taşınırsa numara değişmez
    (numara atama sırasında bir kez üretilir ve saklanır).
  * İşlem geri alınsa da tüketilen sıra değeri geri gelmez → numaralarda boşluk
    olabilir; bu bilinçli (benzersizlik ≫ ardışıklık).

Yetki: yeni nesne `persopyro_owner` tarafından oluşturulur; uygulama rolüne
`USAGE, SELECT` yetkisi `scripts/02_grant_app_privileges.sql` içindeki
`ALTER DEFAULT PRIVILEGES` + `GRANT ... ON ALL SEQUENCES IN SCHEMA hr` ile verilir
(`migrate.sh` bu betiği her migration sonrası yeniden çalıştırır).

RLS: sequence'ler RLS kapsamında **değildir** (satır bazlı politika uygulanmaz);
erişim `USAGE` yetkisi ile sınırlıdır.

NEDEN AYRI MIGRATION: uygulanmış migration'lar değiştirilmez (proje kuralı).
Eklemeli ve geri alınabilir (`DROP SEQUENCE`) — mevcut kolon/kısıt/veri değişmez.
"""

from __future__ import annotations

from django.db import migrations

SEQUENCE = "hr.employee_no_seq"


class Migration(migrations.Migration):
    dependencies = [
        ("organization", "0012_m12_personal_data_blood_type"),
    ]

    operations = [
        migrations.RunSQL(
            sql=(
                f"CREATE SEQUENCE IF NOT EXISTS {SEQUENCE} "
                "AS bigint START WITH 1 INCREMENT BY 1 NO MAXVALUE CACHE 1"
            ),
            reverse_sql=f"DROP SEQUENCE IF EXISTS {SEQUENCE}",
        ),
    ]
