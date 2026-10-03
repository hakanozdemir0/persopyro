"""Profile change requests are tenant isolated and same-tenant linked."""

from django.db import migrations


FORWARD_SQL = """
ALTER TABLE hr.employee_profile_change_requests ENABLE ROW LEVEL SECURITY;
ALTER TABLE hr.employee_profile_change_requests FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON hr.employee_profile_change_requests
    USING (tenant_id = hr.current_tenant_id())
    WITH CHECK (tenant_id = hr.current_tenant_id());

CREATE TRIGGER trg_profile_request_employee_same_tenant
    BEFORE INSERT OR UPDATE ON hr.employee_profile_change_requests
    FOR EACH ROW EXECUTE FUNCTION hr.ensure_same_tenant_ref('employee_id', 'employees');
CREATE TRIGGER trg_profile_request_requester_same_tenant
    BEFORE INSERT OR UPDATE ON hr.employee_profile_change_requests
    FOR EACH ROW EXECUTE FUNCTION hr.ensure_same_tenant_ref('requester_id', 'users');
CREATE TRIGGER trg_profile_request_reviewer_same_tenant
    BEFORE INSERT OR UPDATE ON hr.employee_profile_change_requests
    FOR EACH ROW EXECUTE FUNCTION hr.ensure_same_tenant_ref('reviewer_id', 'users');

REVOKE ALL ON hr.employee_profile_change_requests FROM PUBLIC;
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'persopyro_app') THEN
        GRANT SELECT, INSERT, UPDATE ON hr.employee_profile_change_requests TO persopyro_app;
        REVOKE DELETE, TRUNCATE ON hr.employee_profile_change_requests FROM persopyro_app;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'persopyro_platform') THEN
        REVOKE ALL ON hr.employee_profile_change_requests FROM persopyro_platform;
    END IF;
END
$$;
"""

REVERSE_SQL = """
DROP TRIGGER IF EXISTS trg_profile_request_employee_same_tenant ON hr.employee_profile_change_requests;
DROP TRIGGER IF EXISTS trg_profile_request_requester_same_tenant ON hr.employee_profile_change_requests;
DROP TRIGGER IF EXISTS trg_profile_request_reviewer_same_tenant ON hr.employee_profile_change_requests;
DROP POLICY IF EXISTS tenant_isolation ON hr.employee_profile_change_requests;
ALTER TABLE hr.employee_profile_change_requests NO FORCE ROW LEVEL SECURITY;
ALTER TABLE hr.employee_profile_change_requests DISABLE ROW LEVEL SECURITY;
"""


class Migration(migrations.Migration):
    dependencies = [("organization", "0024_employeeprofilechangerequest")]
    operations = [migrations.RunSQL(FORWARD_SQL, REVERSE_SQL)]
