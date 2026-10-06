import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("shifts", "0002_rls_and_guards"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="shiftassignment",
            name="late_minutes",
            field=models.PositiveSmallIntegerField(default=0, verbose_name="geç kalınan dakika"),
        ),
        migrations.AddField(
            model_name="shiftassignment",
            name="late_reported_at",
            field=models.DateTimeField(blank=True, null=True, verbose_name="geç kalış bildirim zamanı"),
        ),
        migrations.AddField(
            model_name="shiftassignment",
            name="late_reported_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="reported_late_shift_assignments",
                to=settings.AUTH_USER_MODEL,
                verbose_name="geç kalışı bildiren",
            ),
        ),
        migrations.RunSQL(
            sql="""
                CREATE TRIGGER trg_shift_assignments_late_reporter_same_tenant
                    BEFORE INSERT OR UPDATE ON hr.shift_assignments
                    FOR EACH ROW EXECUTE FUNCTION
                    hr.ensure_same_tenant_ref('late_reported_by_id', 'users');
            """,
            reverse_sql="DROP TRIGGER IF EXISTS trg_shift_assignments_late_reporter_same_tenant ON hr.shift_assignments;",
        ),
    ]
