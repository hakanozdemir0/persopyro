"""Platform data access requests, dual approvals, and expiring grants."""

from django.db import migrations


FORWARD_SQL = """
CREATE TABLE platform.access_requests (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES hr.tenants(id) ON DELETE RESTRICT,
    requester_id uuid NOT NULL,
    operation varchar(10) NOT NULL CHECK (operation IN ('read', 'update')),
    fields jsonb NOT NULL CHECK (jsonb_typeof(fields) = 'array'),
    purpose text NOT NULL CHECK (length(purpose) >= 20),
    ttl_minutes smallint NOT NULL CHECK (ttl_minutes BETWEEN 1 AND 60),
    status varchar(12) NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'approved', 'rejected', 'expired')),
    created_at timestamptz NOT NULL DEFAULT now(),
    decided_at timestamptz,
    CONSTRAINT access_requests_fields_nonempty CHECK (jsonb_array_length(fields) > 0)
);
CREATE INDEX platform_access_requests_tenant_idx
    ON platform.access_requests (tenant_id, created_at DESC);
CREATE INDEX platform_access_requests_pending_idx
    ON platform.access_requests (created_at DESC) WHERE status = 'pending';

CREATE TABLE platform.access_approvals (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    request_id uuid NOT NULL REFERENCES platform.access_requests(id) ON DELETE RESTRICT,
    approver_id uuid NOT NULL,
    decision varchar(8) NOT NULL CHECK (decision IN ('approve', 'reject')),
    reason text NOT NULL DEFAULT '',
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT access_approvals_distinct_actor UNIQUE (request_id, approver_id)
);
CREATE INDEX platform_access_approvals_request_idx
    ON platform.access_approvals (request_id, created_at);

CREATE TABLE platform.access_grants (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    request_id uuid NOT NULL UNIQUE REFERENCES platform.access_requests(id) ON DELETE RESTRICT,
    tenant_id uuid NOT NULL REFERENCES hr.tenants(id) ON DELETE RESTRICT,
    operation varchar(10) NOT NULL CHECK (operation IN ('read', 'update')),
    fields jsonb NOT NULL CHECK (jsonb_typeof(fields) = 'array'),
    purpose text NOT NULL,
    requester_id uuid NOT NULL,
    starts_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL,
    revoked_at timestamptz,
    revoked_by_id uuid,
    CONSTRAINT access_grants_expiry_valid CHECK (expires_at > starts_at),
    CONSTRAINT access_grants_revoke_pair CHECK
        ((revoked_at IS NULL) = (revoked_by_id IS NULL))
);
CREATE INDEX platform_access_grants_active_idx
    ON platform.access_grants (tenant_id, expires_at) WHERE revoked_at IS NULL;

REVOKE ALL ON platform.access_requests, platform.access_approvals,
    platform.access_grants FROM PUBLIC;
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'persopyro_app') THEN
        REVOKE ALL ON platform.access_requests, platform.access_approvals,
            platform.access_grants FROM persopyro_app;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'persopyro_platform') THEN
        GRANT SELECT, INSERT, UPDATE ON platform.access_requests TO persopyro_platform;
        GRANT SELECT, INSERT ON platform.access_approvals TO persopyro_platform;
        GRANT SELECT, INSERT, UPDATE ON platform.access_grants TO persopyro_platform;
    END IF;
END
$$;
"""

REVERSE_SQL = """
DROP TABLE IF EXISTS platform.access_grants;
DROP TABLE IF EXISTS platform.access_approvals;
DROP TABLE IF EXISTS platform.access_requests;
"""


class Migration(migrations.Migration):
    dependencies = [("platform", "0010_revoke_unapproved_tenant_pii")]
    operations = [migrations.RunSQL(FORWARD_SQL, REVERSE_SQL)]
