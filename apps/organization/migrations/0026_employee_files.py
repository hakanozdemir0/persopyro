import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models
import apps.organization.models


class Migration(migrations.Migration):
    dependencies = [
        ("organization", "0025_profile_change_request_security"),
        ("tenancy", "0011_tenant_company_limits"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="employee",
            name="profile_photo",
            field=models.FileField(blank=True, upload_to=apps.organization.models.employee_upload_path, verbose_name="profil fotoğrafı"),
        ),
        migrations.CreateModel(
            name="EmployeeDocument",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("deleted_at", models.DateTimeField(blank=True, null=True)),
                ("title", models.CharField(max_length=180, verbose_name="evrak adı")),
                ("file", models.FileField(upload_to=apps.organization.models.employee_upload_path, verbose_name="dosya")),
                ("original_filename", models.CharField(max_length=255, verbose_name="dosya adı")),
                ("employee", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="documents", to="organization.employee")),
                ("tenant", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to="tenancy.tenant")),
                ("uploaded_by", models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
            ],
            options={"db_table": "employee_documents", "verbose_name": "çalışan evrakı", "verbose_name_plural": "çalışan evrakları"},
        ),
        migrations.RunSQL(
            """
            ALTER TABLE hr.employee_documents ENABLE ROW LEVEL SECURITY;
            ALTER TABLE hr.employee_documents FORCE ROW LEVEL SECURITY;
            CREATE POLICY tenant_isolation ON hr.employee_documents
                USING (tenant_id = hr.current_tenant_id())
                WITH CHECK (tenant_id = hr.current_tenant_id());
            CREATE TRIGGER trg_employee_document_employee_same_tenant
                BEFORE INSERT OR UPDATE ON hr.employee_documents
                FOR EACH ROW EXECUTE FUNCTION hr.ensure_same_tenant_ref('employee_id', 'employees');
            CREATE TRIGGER trg_employee_document_uploader_same_tenant
                BEFORE INSERT OR UPDATE ON hr.employee_documents
                FOR EACH ROW EXECUTE FUNCTION hr.ensure_same_tenant_ref('uploaded_by_id', 'users');
            REVOKE ALL ON hr.employee_documents FROM PUBLIC;
            DO $$ BEGIN
                IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'persopyro_app') THEN
                    GRANT SELECT, INSERT, UPDATE ON hr.employee_documents TO persopyro_app;
                    REVOKE DELETE, TRUNCATE ON hr.employee_documents FROM persopyro_app;
                END IF;
                IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'persopyro_platform') THEN
                    REVOKE ALL ON hr.employee_documents FROM persopyro_platform;
                END IF;
            END $$;
            """,
            """
            DROP TRIGGER IF EXISTS trg_employee_document_employee_same_tenant ON hr.employee_documents;
            DROP TRIGGER IF EXISTS trg_employee_document_uploader_same_tenant ON hr.employee_documents;
            DROP POLICY IF EXISTS tenant_isolation ON hr.employee_documents;
            ALTER TABLE hr.employee_documents NO FORCE ROW LEVEL SECURITY;
            ALTER TABLE hr.employee_documents DISABLE ROW LEVEL SECURITY;
            """,
        ),
    ]
