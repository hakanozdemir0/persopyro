"""
M12 genişletme — `hr.employee_personal_data` kişisel veri alanları.

Eklenen alanlar (K2 — kişisel veri; K3 değil, şifreleme gerekmez):
  * `emergency_contact_phone` / `emergency_contact_relation`: acil durumda aranacak
    yakın kişi ve yakınlık derecesi,
  * `citizenship`: vatandaşlık (boş = bilinmiyor; "TR" = Türkiye),
  * `work_permit_type` / `work_permit_valid_until`: Türk vatandaşı değilse oturma/
    çalışma izni türü ve geçerlilik tarihi.

NEDEN AYRI MIGRATION: uygulanmış migration'lar değiştirilmez (proje kuralı). Tablo,
RLS ve yetkiler **değişmez**; yalnızca additive kolon eklenir (mevcut satırlar boş
değerle kalır). Erişim kuralı: tablo kapsamında `employee.sensitive.read` (OD5 §5.7 —
organizasyon kapsamı tek başına bu tabloya erişim vermez).
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("organization", "0009_org_status_checks"),
    ]

    operations = [
        migrations.AddField(
            model_name="employeepersonaldata",
            name="emergency_contact_phone",
            field=models.CharField("yakın kişi telefonu", max_length=30, blank=True),
        ),
        migrations.AddField(
            model_name="employeepersonaldata",
            name="emergency_contact_relation",
            field=models.CharField("yakınlık derecesi", max_length=60, blank=True),
        ),
        migrations.AddField(
            model_name="employeepersonaldata",
            name="citizenship",
            field=models.CharField(
                "vatandaşlık",
                max_length=60,
                blank=True,
                default="",
                help_text="ISO ülke kodu veya ad; boş = bilinmiyor. 'TR' = Türkiye.",
            ),
        ),
        migrations.AddField(
            model_name="employeepersonaldata",
            name="work_permit_type",
            field=models.CharField(
                "oturma/çalışma izni",
                max_length=60,
                blank=True,
                help_text="Türk vatandaşı değilse izin türü (ör. 'çalışma izni', 'oturma izni').",
            ),
        ),
        migrations.AddField(
            model_name="employeepersonaldata",
            name="work_permit_valid_until",
            field=models.DateField("izin geçerlilik tarihi", null=True, blank=True),
        ),
    ]
