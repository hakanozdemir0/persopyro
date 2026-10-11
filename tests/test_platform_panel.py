import uuid
from datetime import date, timedelta

import pytest
from django.conf import settings
from django.contrib.auth.hashers import make_password
from django.db import connection, connections
from django.test import Client
from django.utils import timezone

from apps.accounts.models import ApiToken, TokenRevokeReason
from apps.organization.models import Department, Employee, EmployeePersonalData
from apps.platform.access_requests import (
    PlatformAccessRequestError,
    create_access_request,
    decide_access_request,
    read_employee_people,
    revoke_access_grant,
)
from apps.platform.permissions import bootstrap_assign_role, effective_permissions
from apps.rbac.models import UserRole
from apps.rbac.services.roles import create_default_roles
from apps.tenancy.context import tenant_context

pytestmark = pytest.mark.django_db(databases=["default", "platform"])


def _platform_user(
    email: str,
    password: str = "Panel-Test-Password-52!",
    role: str = "superadmin",
) -> uuid.UUID:
    with connections["platform"].cursor() as cursor:
        cursor.execute(
            "INSERT INTO platform.users (email, full_name, password) "
            "VALUES (%s, %s, %s) RETURNING id",
            [email, "Panel Test", make_password(password)],
        )
        user_id = cursor.fetchone()[0]
    bootstrap_assign_role(
        target_user_id=user_id,
        role_code=role,
        reason="panel test bootstrap",
    )
    return user_id


def test_platform_panel_login_page_is_available() -> None:
    response = Client().get("/platform/login/")
    assert response.status_code == 200
    assert "email" in response.content.decode().lower()


def test_platform_panel_dashboard_requires_session() -> None:
    response = Client().get("/platform/")
    assert response.status_code == 302
    assert response.headers["Location"] == "/platform/login/"


def test_expired_company_session_does_not_block_platform_panel(tenant_a) -> None:
    user_id = _platform_user("expiry-panel@persopyro.test")
    with tenant_context(tenant_a.id):
        tenant_a.valid_until = date.today()
        tenant_a.save(update_fields=["valid_until", "updated_at"])
    client = Client()
    session = client.session
    session["platform_user_id"] = str(user_id)
    session["tenant_id"] = str(tenant_a.id)
    session.save()

    response = client.get("/platform/")

    assert response.status_code == 200
    assert response.wsgi_request.session.get("platform_user_id") == str(user_id)


def test_company_creation_permission_is_superadmin_only() -> None:
    superadmin_id = _platform_user("create-company-sa@persopyro.test")
    admin_id = _platform_user("create-company-admin@persopyro.test", role="platform_admin")
    assert "platform.tenant.create" in effective_permissions(superadmin_id)
    assert "platform.tenant.create" not in effective_permissions(admin_id)


def test_only_superadmin_can_toggle_company_status(tenant_a) -> None:  # noqa: ANN001
    superadmin_id = _platform_user("tenant-status-sa@persopyro.test")
    admin_id = _platform_user("tenant-status-admin@persopyro.test", role="platform_admin")

    def logged_client(user_id):
        client = Client()
        session = client.session
        session["platform_user_id"] = str(user_id)
        session.save()
        return client

    denied = logged_client(admin_id).post(
        f"/platform/tenants/{tenant_a.id}/status/", {"action": "deactivate"}
    )
    assert denied.status_code == 403

    client = logged_client(superadmin_id)
    paused = client.post(
        f"/platform/tenants/{tenant_a.id}/status/", {"action": "deactivate"}
    )
    assert paused.status_code == 302
    with tenant_context(tenant_a.id):
        tenant_a.refresh_from_db()
    assert tenant_a.status == "suspended"

    resumed = client.post(
        f"/platform/tenants/{tenant_a.id}/status/", {"action": "activate"}
    )
    assert resumed.status_code == 302
    with tenant_context(tenant_a.id):
        tenant_a.refresh_from_db()
    assert tenant_a.status == "active"
    with connections["platform"].cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM platform.audit_events "
            "WHERE action='platform.tenant.status.updated' AND tenant_id=%s",
            [str(tenant_a.id)],
        )
        assert cursor.fetchone()[0] == 2


def test_platform_panel_login_accepts_active_superadmin() -> None:
    _platform_user("superadmin-panel@persopyro.test")
    response = Client().post(
        "/platform/login/",
        {"email": "superadmin-panel@persopyro.test", "password": "Panel-Test-Password-52!"},
    )
    assert response.status_code == 302
    assert response.headers["Location"] == "/platform/"
    assert "platform_user_id" in response.wsgi_request.session
    with connections["platform"].cursor() as cursor:
        cursor.execute(
            "SELECT action FROM platform.audit_events WHERE action = 'platform.login.succeeded'"
        )
        assert cursor.fetchone() == ("platform.login.succeeded",)


def test_platform_login_resets_expired_lockout_counter() -> None:
    user_id = _platform_user("expired-lockout@persopyro.test")
    with connections["platform"].cursor() as cursor:
        cursor.execute(
            "UPDATE platform.users SET failed_login_count = 5, lockout_until = %s WHERE id = %s",
            [timezone.now() - timedelta(minutes=1), str(user_id)],
        )

    response = Client().post(
        "/platform/login/",
        {"email": "expired-lockout@persopyro.test", "password": "wrong-password"},
    )
    assert response.status_code == 401
    with connections["platform"].cursor() as cursor:
        cursor.execute(
            "SELECT failed_login_count, lockout_until FROM platform.users WHERE id = %s",
            [str(user_id)],
        )
        assert cursor.fetchone() == (1, None)


def test_platform_term_rejects_invalid_date_before_company_lookup() -> None:
    user_id = _platform_user("term-panel@persopyro.test")
    client = Client()
    session = client.session
    session["platform_user_id"] = str(user_id)
    session.save()
    response = client.post(
        "/platform/tenants/00000000-0000-0000-0000-000000000001/profile/",
        {"valid_until": "not-a-date"},
    )
    assert response.status_code == 400


def test_superadmin_profile_form_updates_limits_and_can_send_optional_notice(
    tenant_a, monkeypatch
) -> None:
    from apps.platform import views

    user_id = _platform_user("term-save-panel@persopyro.test")
    logged = []
    mailed = []
    monkeypatch.setattr(
        views,
        "platform_user_row",
        lambda _user_id: (
            user_id,
            "plt-test",
            "term-save-panel@persopyro.test",
            "active",
            True,
            None,
        ),
    )
    monkeypatch.setattr(
        views,
        "effective_permissions",
        lambda _user_id: {"platform.settings.manage", "platform.role.assign"},
    )
    monkeypatch.setattr(views, "record_platform_event", lambda **event: logged.append(event))
    monkeypatch.setattr(views, "send_mail", lambda **mail: mailed.append(mail) or 1)

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, sql, _params=None):
            self.sql = sql

        def fetchone(self):
            return ("trial", [])

        def fetchall(self):
            return [
                ("trial", "Deneme"),
                ("starter", "Başlangıç"),
                ("professional", "Profesyonel"),
                ("enterprise", "Kurumsal"),
                ("custom", "Özel"),
            ]

    monkeypatch.setattr(views.connections["platform"], "cursor", lambda: Cursor())
    client = Client()
    session = client.session
    session["platform_user_id"] = str(user_id)
    session.save()

    response = client.post(
        f"/platform/tenants/{tenant_a.id}/profile/",
        {
            "name": "Kiracı A güncellendi",
            "status": "active",
            "plan_code": "professional",
            "valid_from": "2026-01-01",
            "valid_until": "2099-12-31",
            "max_users": "20",
            "max_storage_mb": "1000",
            "max_active_employees": "50",
            "max_employee_records": "100",
            "profile_fields": ["email"],
            "email": "firma@ornek.test",
            "required_profile_fields": ["email"],
            "custom_fields_json": (
                '[{"code":"custom_region","label":"Bölge","type":"text","required":true}]'
            ),
            "custom_value_custom_region": "Ege",
            "notify_company": "on",
        },
    )

    assert response.status_code == 302
    assert "changed=1" in response.headers["Location"]
    assert response.headers["Location"].endswith("#company-edit")
    with tenant_context(tenant_a.id):
        tenant_a.refresh_from_db()
        assert tenant_a.valid_until == date(2099, 12, 31)
        assert tenant_a.valid_from == date(2026, 1, 1)
        assert tenant_a.max_active_employees == 50
        assert tenant_a.max_employee_records == 100
        assert tenant_a.max_storage_mb == 1000
        assert tenant_a.email == "firma@ornek.test"
        assert tenant_a.name == "Kiracı A güncellendi"
        assert tenant_a.settings["company_profile_fields"][0]["code"] == "custom_region"
        assert tenant_a.settings["company_profile_values"]["custom_region"] == "Ege"
        assert tenant_a.settings["company_required_fields"] == ["email"]
    assert logged[0]["action"] == "platform.tenant.profile.updated"
    assert "valid_until" in logged[0]["details"]["changed_fields"]
    assert logged[1]["action"] == "platform.tenant.notification.sent"
    assert mailed[0]["recipient_list"] == ["firma@ornek.test"]


def test_superadmin_company_people_endpoint_masks_personal_data_without_grant(
    tenant_a, monkeypatch
) -> None:
    from apps.platform import views

    user_id = _platform_user("people-panel@persopyro.test")
    # Even a mistakenly assigned persistent permission cannot replace a scoped grant.
    monkeypatch.setattr(
        views,
        "effective_permissions",
        lambda _user_id: frozenset({"platform.tenant.read", "platform.tenant.pii.read"}),
    )
    monkeypatch.setattr(views, "_platform_can_access_tenant", lambda *_args, **_kwargs: True)
    with tenant_context(tenant_a.id):
        department = Department.objects.create(
            tenant=tenant_a,
            code="PANEL",
            name="Panel",
        )
        employee = Employee.objects.create(
            tenant=tenant_a,
            department=department,
            employee_no="P-100",
            hire_date="2026-01-01",
        )
        EmployeePersonalData.objects.create(
            tenant=tenant_a,
            employee=employee,
            first_name="Deniz",
            last_name="Yılmaz",
            national_id="12345678901",
        )
    client = Client()
    session = client.session
    session["platform_user_id"] = str(user_id)
    session.save()
    response = client.get(f"/platform/tenants/{tenant_a.id}/people/")
    assert response.status_code == 200
    content = response.content.decode()
    assert "Aktif" in content
    assert "active" not in content
    assert "12345678901" not in content
    assert "Deniz" not in content
    with connections["platform"].cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM platform.audit_events "
            "WHERE action = 'platform.tenant.people.read' AND tenant_id = %s",
            [str(tenant_a.id)],
        )
        assert cursor.fetchone()[0] == 1


def test_platform_admin_company_people_view_masks_personal_data(tenant_a, monkeypatch) -> None:
    user_id = _platform_user("admin-people@persopyro.test", role="platform_admin")
    from apps.platform import views

    monkeypatch.setattr(views, "_platform_can_access_tenant", lambda *_args, **_kwargs: True)
    with tenant_context(tenant_a.id):
        department = Department.objects.create(
            tenant=tenant_a,
            code="ADMINPANEL",
            name="Admin Panel",
        )
        employee = Employee.objects.create(
            tenant=tenant_a,
            department=department,
            employee_no="P-200",
            hire_date="2026-01-01",
        )
        EmployeePersonalData.objects.create(
            tenant=tenant_a,
            employee=employee,
            first_name="Gizli",
            last_name="Kişi",
            national_id="98765432100",
        )
    client = Client()
    session = client.session
    session["platform_user_id"] = str(user_id)
    session.save()
    response = client.get(f"/platform/tenants/{tenant_a.id}/people/")
    assert response.status_code == 200
    assert "P-200" in response.content.decode()
    assert "98765432100" not in response.content.decode()


def test_company_details_allow_superadmin_to_manage_selected_fields_without_approval(
    tenant_a, monkeypatch
) -> None:
    from apps.platform import views

    actor_id = _platform_user("company-details-superadmin@persopyro.test")
    with tenant_context(tenant_a.id):
        tenant_a.legal_name = "Örnek Tüzel Şirket A.Ş."
        tenant_a.save(update_fields=["legal_name", "updated_at"])

    registry = (
        tenant_a.id,
        tenant_a.slug,
        "",
        "active",
        10,
        500,
        "professional",
        None,
        None,
        None,
        25,
        100,
        ["legal_name"],
    )

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, *_args):
            pass

        def fetchone(self):
            return registry

        def fetchall(self):
            return []

    monkeypatch.setattr(
        views,
        "platform_user_row",
        lambda _user_id: (
            actor_id,
            "plt-test",
            "company-details-superadmin@persopyro.test",
            "active",
            True,
            None,
        ),
    )
    monkeypatch.setattr(
        views,
        "effective_permissions",
        lambda _user_id: {
            "platform.tenant.read",
            "platform.settings.manage",
            "platform.role.assign",
        },
    )
    monkeypatch.setattr(views, "record_platform_event", lambda **_event: None)
    monkeypatch.setattr(views.connections["platform"], "cursor", lambda: Cursor())

    client = Client()
    session = client.session
    session["platform_user_id"] = str(actor_id)
    session.save()
    response = client.get(f"/platform/tenants/{tenant_a.id}/")

    assert response.status_code == 200
    content = response.content.decode()
    assert "Örnek Tüzel Şirket A.Ş." in content
    assert "Şirket bilgilerini düzenle" in content
    assert "Çalışanlar" in content
    assert "Talep oluştur" not in content
    assert "Onayla" not in content


def test_platform_admin_cannot_edit_company_profile(tenant_a, monkeypatch) -> None:
    from apps.platform import views

    actor_id = _platform_user("company-details-admin@persopyro.test", role="platform_admin")
    monkeypatch.setattr(
        views,
        "platform_user_row",
        lambda _user_id: (
            actor_id,
            "plt-test",
            "company-details-admin@persopyro.test",
            "active",
            True,
            None,
        ),
    )
    monkeypatch.setattr(views, "effective_permissions", lambda _user_id: {"platform.tenant.read"})
    client = Client()
    session = client.session
    session["platform_user_id"] = str(actor_id)
    session.save()
    response = client.post(f"/platform/tenants/{tenant_a.id}/profile/", {"name": "Değişiklik"})
    assert response.status_code == 403


def test_superadmin_can_create_company_and_initial_admin() -> None:
    from apps.accounts.models import User
    from apps.rbac.models import UserRole
    from apps.tenancy.models import Tenant

    actor_id = _platform_user("company-create@persopyro.test")
    client = Client()
    session = client.session
    session["platform_user_id"] = str(actor_id)
    session.save()

    response = client.post(
        "/platform/tenants/create/",
        {
            "name": "Yeni Şirket",
            "slug": "yeni-sirket",
            "admin_email": "yonetici@yeni-sirket.test",
            "admin_first_name": "Yeni",
            "admin_last_name": "Yönetici",
            "admin_password": "Company-Admin-Password-52!",
            "max_users": "25",
            "plan_code": "enterprise",
            "max_storage_mb": "2048",
            "max_active_employees": "80",
            "max_employee_records": "120",
            "valid_from": "2026-10-01",
            "valid_until": "2027-10-01",
            "profile_fields": ["legal_name", "tax_number"],
            "legal_name": "Yeni Şirket A.Ş.",
            "tax_number": "9876543210",
            "custom_fields_json": (
                '[{"label":"Şirket bölgesi","type":"text","required":true,"options":[]}]'
            ),
            "custom_value_custom_sirket_bolgesi": "Ege",
        },
    )

    assert response.status_code == 302
    assert response.headers["Location"] == "/platform/"
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT tenant_id FROM hr.tenant_directory WHERE slug = %s",
            ["yeni-sirket"],
        )
        tenant_id = cursor.fetchone()[0]
    with tenant_context(tenant_id):
        tenant = Tenant.objects.get(pk=tenant_id)
        admin = User.objects.get(tenant=tenant, email="yonetici@yeni-sirket.test")
        assert tenant.name == "Yeni Şirket"
        assert tenant.max_users == 25
        assert tenant.max_storage_mb == 2048
        assert tenant.max_active_employees == 80
        assert tenant.max_employee_records == 120
        assert tenant.valid_from == date(2026, 10, 1)
        assert tenant.valid_until == date(2027, 10, 1)
        assert tenant.settings["company_profile_fields"][0]["code"] == "custom_sirket_bolgesi"
        assert tenant.settings["company_profile_values"]["custom_sirket_bolgesi"] == "Ege"
        assert tenant.legal_name == "Yeni Şirket A.Ş."
        assert tenant.tax_number == "9876543210"
        assert admin.check_password("Company-Admin-Password-52!")
        assert UserRole.objects.filter(user=admin, role__code="tenant_admin").exists()


def test_platform_admin_cannot_create_company() -> None:
    actor_id = _platform_user("company-create-pa@persopyro.test", role="platform_admin")
    client = Client()
    session = client.session
    session["platform_user_id"] = str(actor_id)
    session.save()

    response = client.post(
        "/platform/tenants/create/",
        {"name": "Reddedilecek", "slug": "reddedilecek"},
    )

    assert response.status_code == 403


def test_platform_admin_password_set_revokes_legacy_api_tokens(tenant_a, user_a) -> None:
    with tenant_context(tenant_a.id):
        roles = create_default_roles(tenant_a)
        UserRole.objects.create(tenant=tenant_a, user=user_a, role=roles["tenant_admin"])
        token, _ = ApiToken.issue(
            user_a,
            purpose="platform password reset security test",
            scopes=["employee.read"],
            ttl_hours=24,
        )
        token.auth_version_snapshot = None
        token.save(update_fields=["auth_version_snapshot"])

    actor_id = _platform_user("set-password-revokes-token@persopyro.test")
    client = Client()
    session = client.session
    session["platform_user_id"] = str(actor_id)
    session.save()

    response = client.post(
        f"/platform/tenants/{tenant_a.id}/admins/{user_a.id}/credentials/",
        {"action": "set_password", "password": "New-Admin-Password-52!"},
    )

    assert response.status_code == 302
    with tenant_context(tenant_a.id):
        token.refresh_from_db()
        assert token.revoked_reason == TokenRevokeReason.SECURITY_INCIDENT


@pytest.mark.django_db(
    databases=["default", "platform"],
    transaction=True,
    available_apps=settings.INSTALLED_APPS,
)
def test_employee_pii_needs_two_approvals_is_field_scoped_and_revocable(tenant_a) -> None:
    requester_id = _platform_user("employee-pii-requester@persopyro.test")
    approver_one = _platform_user("employee-pii-approver-one@persopyro.test")
    approver_two = _platform_user("employee-pii-approver-two@persopyro.test")
    with tenant_context(tenant_a.id):
        department = Department.objects.create(tenant=tenant_a, code="GRANT", name="Grant")
        employee = Employee.objects.create(
            tenant=tenant_a,
            department=department,
            employee_no="GR-1",
            hire_date="2026-01-01",
        )
        EmployeePersonalData.objects.create(
            tenant=tenant_a,
            employee=employee,
            first_name="Gizli",
            last_name="Kişi",
            national_id="12345678901",
            address="Gizli adres",
            blood_type="A+",
        )
    request_id = create_access_request(
        requester_id=requester_id,
        tenant_id=tenant_a.id,
        operation="read",
        fields=["employee.identity"],
        resources=[str(employee.pk)],
        purpose="Çalışan kimlik alanı incelemesi gerekiyor",
        ttl_minutes=30,
    )
    assert decide_access_request(
        approver_id=approver_one,
        tenant_id=tenant_a.id,
        request_id=request_id,
        decision="approve",
    ) is None
    with pytest.raises(PlatformAccessRequestError, match="Etkin çalışan verisi"):
        read_employee_people(
            grant_id=request_id,
            requester_id=requester_id,
            tenant_id=tenant_a.id,
        )
    grant_id = decide_access_request(
        approver_id=approver_two,
        tenant_id=tenant_a.id,
        request_id=request_id,
        decision="approve",
    )
    assert grant_id is not None
    data = read_employee_people(
        grant_id=grant_id,
        requester_id=requester_id,
        tenant_id=tenant_a.id,
    )
    assert data[0]["details"] == {"employee.identity": "12345678901"}
    requester_client = Client()
    requester_session = requester_client.session
    requester_session["platform_user_id"] = str(requester_id)
    requester_session.save()
    granted_page = requester_client.get(
        f"/platform/tenants/{tenant_a.id}/people/?grant_id={grant_id}"
    )
    assert granted_page.status_code == 200
    assert "12345678901" in granted_page.content.decode()
    assert "Gizli adres" not in granted_page.content.decode()
    with pytest.raises(PlatformAccessRequestError, match="Etkin çalışan verisi"):
        read_employee_people(
            grant_id=grant_id,
            requester_id=approver_one,
            tenant_id=tenant_a.id,
        )
    revoke_access_grant(
        grant_id=grant_id,
        actor_id=requester_id,
        tenant_id=tenant_a.id,
    )
    assert requester_client.get(
        f"/platform/tenants/{tenant_a.id}/people/?grant_id={grant_id}"
    ).status_code == 403
    with pytest.raises(PlatformAccessRequestError, match="Etkin çalışan verisi"):
        read_employee_people(
            grant_id=grant_id,
            requester_id=requester_id,
            tenant_id=tenant_a.id,
        )
