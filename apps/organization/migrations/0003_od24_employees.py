# M12 (OD24/OD27/OD21 kapalı) — `hr.employees` + `hr.employee_personal_data`.
#
# * `employees` = çalışanın **iş** kaydı; **PII İÇERMEZ** (ad/iletişim/kimlik/sağlık
#   verisi ayrı tablodadır). Böylece "bu birimde kim çalışıyor" (K0/K1) ile
#   "bu kişinin TCKN'si ne" (K2/K3) **farklı izinlerle** yanıtlanır (OD5 §5.7).
# * `employees.department_id` **zorunludur** (OD24 §4: v1'de her çalışan bir birimde).
# * Kısmi benzersizlik: `(tenant_id, employee_no) WHERE deleted_at IS NULL` (OD21).
# * `employee_personal_data` PK'si `employee_id`'dir (1:1) — ayrı `id` kolonu yoktur.
# * K3 alanları **şifrelüdür** (Fernet; `apps/common/fields.py`).
#
# Kiracı izolasyonu: `tenant_id` + RLS + FORCE (`0006_rls_organization`) ve
# `department_id`/`position_id`/`user_id`/`employee_id` için kiracı guard'ları
# (`0007_org_same_tenant_guards`). `position` alanı `positions` tablosuna bağlıdır
# (bu yüzden ayrı `AddField`).

import apps.common.fields
import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("organization", "0002_od24_tree_guards"),
    ]

    operations = [
        migrations.CreateModel(
            name="Employee",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("deleted_at", models.DateTimeField(blank=True, null=True)),
                ("employee_no", models.CharField(max_length=50, verbose_name="personel numarası")),
                ("hire_date", models.DateField(verbose_name="işe giriş tarihi")),
                (
                    "termination_date",
                    models.DateField(blank=True, null=True, verbose_name="çıkış tarihi"),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("active", "Aktif"),
                            ("suspended", "Askıda"),
                            ("terminated", "Ayrıldı"),
                            ("archived", "Arşiv"),
                        ],
                        default="active",
                        max_length=20,
                        verbose_name="durum",
                    ),
                ),
                (
                    "department",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="employees",
                        to="organization.department",
                        verbose_name="birim",
                    ),
                ),
                (
                    "tenant",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="employees",
                        to="tenancy.tenant",
                        verbose_name="kiracı",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        blank=True,
                        help_text=(
                            "Giriş hesabı (OD2); çalışanın giriş hesabı olmak zorunda değildir."
                        ),
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="employee_records",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="kullanıcı",
                    ),
                ),
            ],
            options={
                "verbose_name": "çalışan",
                "verbose_name_plural": "çalışanlar",
                "db_table": "employees",
            },
        ),
        migrations.CreateModel(
            name="EmployeePersonalData",
            fields=[
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("deleted_at", models.DateTimeField(blank=True, null=True)),
                (
                    "employee",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        primary_key=True,
                        related_name="personal_data",
                        serialize=False,
                        to="organization.employee",
                        verbose_name="çalışan",
                    ),
                ),
                ("first_name", models.CharField(max_length=100, verbose_name="ad")),
                ("last_name", models.CharField(max_length=100, verbose_name="soyad")),
                (
                    "birth_date",
                    models.DateField(blank=True, null=True, verbose_name="doğum tarihi"),
                ),
                (
                    "national_id",
                    models.CharField(blank=True, max_length=30, verbose_name="kimlik numarası"),
                ),
                ("address", models.TextField(blank=True, verbose_name="adres")),
                (
                    "personal_email",
                    models.EmailField(blank=True, max_length=190, verbose_name="kişisel e-posta"),
                ),
                (
                    "personal_phone",
                    models.CharField(blank=True, max_length=30, verbose_name="kişisel telefon"),
                ),
                ("iban", models.CharField(blank=True, max_length=34, verbose_name="IBAN")),
                (
                    "health_notes",
                    apps.common.fields.EncryptedTextField(
                        blank=True, null=True, verbose_name="sağlık notları (şifreli)"
                    ),
                ),
                (
                    "disability_status",
                    apps.common.fields.EncryptedCharField(
                        blank=True, max_length=500, null=True, verbose_name="engellilik durumu (şifreli)"
                    ),
                ),
                (
                    "biometric_ref",
                    apps.common.fields.EncryptedCharField(
                        blank=True, max_length=500, null=True, verbose_name="biyometrik referans (şifreli)"
                    ),
                ),
                (
                    "tenant",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="employee_personal_data",
                        to="tenancy.tenant",
                        verbose_name="kiracı",
                    ),
                ),
            ],
            options={
                "verbose_name": "çalışan kişisel verisi",
                "verbose_name_plural": "çalışan kişisel verileri",
                "db_table": "employee_personal_data",
            },
        ),
        migrations.AddField(
            model_name="employee",
            name="position",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="employees",
                to="organization.position",
                verbose_name="pozisyon",
            ),
        ),
        migrations.AddIndex(
            model_name="employee",
            index=models.Index(fields=["tenant", "department"], name="employees_tenant_dept_idx"),
        ),
        migrations.AddIndex(
            model_name="employee",
            index=models.Index(fields=["tenant", "user"], name="employees_tenant_user_idx"),
        ),
        migrations.AddIndex(
            model_name="employee",
            index=models.Index(fields=["tenant", "status"], name="employees_tenant_status_idx"),
        ),
        migrations.AddConstraint(
            model_name="employee",
            constraint=models.UniqueConstraint(
                condition=models.Q(("deleted_at__isnull", True)),
                fields=("tenant", "employee_no"),
                name="employees_tenant_no_uniq",
            ),
        ),
        migrations.AddConstraint(
            model_name="employee",
            constraint=models.CheckConstraint(
                condition=models.Q(("termination_date__isnull", True))
                | models.Q(("termination_date__gte", models.F("hire_date"))),
                name="employees_termination_after_hire",
            ),
        ),
        migrations.AddIndex(
            model_name="employeepersonaldata",
            index=models.Index(fields=["tenant"], name="emp_personal_tenant_idx"),
        ),
    ]
