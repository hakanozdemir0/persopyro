"""
M12 genişletme (2) — `hr.employee_personal_data.bank_name`.

Neden: kullanıcı bildirimi "IBAN bilgisi dışında banka bilgileri olarak sorması
ve göstermesi daha doğru olur — IBAN ve Banka olarak". IBAN mevcut; banka adı yok.

K2 (kişisel veri), şifreleme gerekmez. Eklemeli migration; mevcut kolon ve
kısıtlar değişmez.
"""

from __future__ import annotations

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("organization", "0010_m12_personal_data_k2_genisletme"),
    ]

    operations = [
        migrations.AddField(
            model_name="employeepersonaldata",
            name="bank_name",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Banka adı (IBAN ile birlikte banka bilgisi).",
                max_length=100,
                verbose_name="banka",
            ),
        ),
    ]
