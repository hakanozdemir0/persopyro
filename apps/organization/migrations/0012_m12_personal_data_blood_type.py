"""
M12 genişletme (3) — `hr.employee_personal_data.blood_type`.

Neden: kullanıcı bildirimi "çalışanların kayıt esnasında bütün bilgilerini iste,
kan grubu dahil". Kan grubu modelde yoktu.

Sınıf: **K3 (özel nitelikli kişisel veri — sağlık)**. Projede sağlık alanları
şifreli tutulur (`apps/common/fields.py`); kan grubu da `EncryptedCharField` ile
**Fernet şifreli** saklanır, bu yüzden kolon tipi `text`'tir ve değer üzerinde
arama/eşitlik yapılamaz (bilinçli). Değer kapalı kümedendir
(`BloodType`: A+/A-/B+/B-/AB+/AB-/0+/0-), doğrulama servis katmanındadır —
şifreli kolonda veritabanı `CHECK` yazılamaz.

NEDEN AYRI MIGRATION: uygulanmış migration'lar değiştirilmez (proje kuralı).
Eklemeli ve **nullable**dır: mevcut satırlar `NULL` (bilinmiyor) kalır; RLS,
yetkiler, kısıtlar ve diğer kolonlar değişmez.

Erişim kuralı: kişisel veri tablosu kapsamında `employee.sensitive.read` + kapsam
(OD5 §5.7); K3 alanların ayrı izinle korunması gerekiyorsa **OD31** ve **OD30**
kararları beklenir (bu migration karar değiştirmez).
"""

from __future__ import annotations

from django.db import migrations

import apps.common.fields


class Migration(migrations.Migration):
    dependencies = [
        ("organization", "0011_m12_personal_data_bank_name"),
    ]

    operations = [
        migrations.AddField(
            model_name="employeepersonaldata",
            name="blood_type",
            field=apps.common.fields.EncryptedCharField(
                blank=True,
                choices=[
                    ("A+", "A Rh+"),
                    ("A-", "A Rh-"),
                    ("B+", "B Rh+"),
                    ("B-", "B Rh-"),
                    ("AB+", "AB Rh+"),
                    ("AB-", "AB Rh-"),
                    ("0+", "0 Rh+"),
                    ("0-", "0 Rh-"),
                ],
                help_text=(
                    "K3 (sağlık) verisi: Fernet ile şifreli saklanır; boş = bilinmiyor. "
                    "Kapalı küme: A+/A-/B+/B-/AB+/AB-/0+/0- (Türkiye yazımı: 0 = O)."
                ),
                max_length=500,
                null=True,
                verbose_name="kan grubu (şifreli)",
            ),
        ),
    ]
