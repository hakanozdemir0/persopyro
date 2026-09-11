# M10 (OD24/OD27/OD21 kapalı) — `hr.departments` + `hr.positions` (yatay tablolar).
#
# * **OD27:** v1'de tek tüzel kişilik → `company_id`/`legal_entity_id` **YOK**;
#   tablolar doğrudan `tenant_id`'ye bağlıdır.
# * **OD21:** soft-delete (`deleted_at`) + **kısmi** benzersizlik (yalnız aktif kayıt).
# * **OD24:** ağaç `parent_id` **adjacency list**'tir; `path`/closure kolonu YOK
#   (döngü/derinlik garantileri `0002_od24_tree_guards` ile gelir).
#
# Kiracı izolasyonu: `tenant_id` + RLS + FORCE (`0006_rls_organization`) ve
# kiracı-içi yabancı anahtar guard'ları (`0007_org_same_tenant_guards`).
#
# `hr.departments.manager_employee_id` (birim sorumlusu) burada YOKTUR: hedefi
# `employees` tablosudur → döngüsel bağımlılık nedeniyle `0005`'te eklenir.

import django.db.models.deletion
import uuid
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ("tenancy", "0009_od25_public_id_columns"),
    ]

    operations = [
        migrations.CreateModel(
            name="Department",
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
                ("code", models.CharField(max_length=50, verbose_name="birim kodu")),
                ("name", models.CharField(max_length=190, verbose_name="birim adı")),
                (
                    "status",
                    models.CharField(
                        choices=[("active", "Aktif"), ("inactive", "Pasif"), ("archived", "Arşiv")],
                        default="active",
                        max_length=20,
                        verbose_name="durum",
                    ),
                ),
                (
                    "parent",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="children",
                        to="organization.department",
                        verbose_name="üst birim",
                    ),
                ),
                (
                    "tenant",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="departments",
                        to="tenancy.tenant",
                        verbose_name="kiracı",
                    ),
                ),
            ],
            options={
                "verbose_name": "organizasyon birimi",
                "verbose_name_plural": "organizasyon birimleri",
                "db_table": "departments",
            },
        ),
        migrations.CreateModel(
            name="Position",
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
                ("code", models.CharField(max_length=50, verbose_name="pozisyon kodu")),
                ("name", models.CharField(max_length=190, verbose_name="pozisyon adı")),
                (
                    "level",
                    models.CharField(
                        blank=True,
                        help_text=(
                            "Serbest etiket (ör. junior/senior/manager); sıralama/raporlama amaçlıdır."
                        ),
                        max_length=40,
                        verbose_name="seviye",
                    ),
                ),
                (
                    "tenant",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="positions",
                        to="tenancy.tenant",
                        verbose_name="kiracı",
                    ),
                ),
            ],
            options={
                "verbose_name": "pozisyon",
                "verbose_name_plural": "pozisyonlar",
                "db_table": "positions",
            },
        ),
        migrations.AddIndex(
            model_name="department",
            index=models.Index(fields=["tenant", "parent"], name="departments_tenant_parent_idx"),
        ),
        migrations.AddIndex(
            model_name="department",
            index=models.Index(fields=["tenant", "status"], name="departments_tenant_status_idx"),
        ),
        migrations.AddConstraint(
            model_name="department",
            constraint=models.UniqueConstraint(
                condition=models.Q(("deleted_at__isnull", True)),
                fields=("tenant", "code"),
                name="departments_tenant_code_uniq",
            ),
        ),
        migrations.AddConstraint(
            model_name="position",
            constraint=models.UniqueConstraint(
                condition=models.Q(("deleted_at__isnull", True)),
                fields=("tenant", "code"),
                name="positions_tenant_code_uniq",
            ),
        ),
    ]
