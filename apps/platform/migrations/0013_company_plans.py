from django.db import migrations

FORWARD_SQL = """
CREATE TABLE platform.company_plans (
    code varchar(40) PRIMARY KEY,
    label varchar(100) NOT NULL,
    description varchar(500) NOT NULL DEFAULT '',
    max_users integer NOT NULL DEFAULT 0 CHECK (max_users >= 0),
    max_storage_mb numeric(12,2) NOT NULL DEFAULT 0 CHECK (max_storage_mb >= 0),
    max_active_employees integer NOT NULL DEFAULT 0 CHECK (max_active_employees >= 0),
    max_employee_records integer NOT NULL DEFAULT 0 CHECK (max_employee_records >= 0),
    active boolean NOT NULL DEFAULT true,
    sort_order integer NOT NULL DEFAULT 0,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
INSERT INTO platform.company_plans
    (code, label, description, max_users, max_storage_mb, sort_order)
VALUES
    ('trial', 'Deneme', 'Deneme kullanımı', 10, 500, 1),
    ('starter', 'Başlangıç', '', 10, 500, 2),
    ('professional', 'Profesyonel', '', 50, 2048, 3),
    ('enterprise', 'Kurumsal', '', 0, 0, 4),
    ('custom', 'Özel', 'Özel limitler', 0, 0, 5);
"""


class Migration(migrations.Migration):
    dependencies = [("platform", "0012_company_profile_fields_and_limits")]
    operations = [migrations.RunSQL(FORWARD_SQL, "DROP TABLE platform.company_plans;")]
