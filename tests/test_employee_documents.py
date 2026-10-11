"""Çalışan evraklarında açık yetki, rol kapsamı ve doğrudan indirme kapıları."""

from __future__ import annotations

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client

from apps.organization.models import EmployeeDocument
from apps.rbac.models import Permission, Role, RolePermission, UserPermissionOverride
from apps.tenancy.context import tenant_context
from tests.leave_helpers import kadro, rol_ver


@pytest.fixture(autouse=True)
def test_ui_enabled(settings, tmp_path):  # noqa: ANN001
    settings.TEST_UI_ENABLED = True
    settings.MEDIA_ROOT = str(tmp_path)


@pytest.fixture(autouse=True)
def tenant_scope(request):  # noqa: ANN001
    if "tenant_a" not in request.fixturenames:
        yield
        return
    with tenant_context(request.getfixturevalue("tenant_a").id):
        yield


def login(client, tenant, user):  # noqa: ANN001
    response = client.post(
        "/api/v1/auth/login",
        data={"tenant": tenant.slug, "email": user.email, "password": "Gizli-Parola-123"},
        content_type="application/json",
    )
    assert response.status_code == 200, response.content


def test_employee_documents_require_explicit_read_and_role_scope(tenant_a, user_a):  # noqa: ANN001
    lot = kadro(tenant_a, user_a)
    client = Client()
    employee_id = lot["personel"].pk
    list_path = f"/test-ui/api/employees/{employee_id}/documents"
    login(client, tenant_a, lot["admin"])
    uploaded = client.post(
        list_path,
        data={"files": SimpleUploadedFile("contract.txt", b"private document")},
    )
    assert uploaded.status_code == 201, uploaded.content
    document_id = uploaded.json()["data"]["created"][0]
    download_path = f"{list_path}/{document_id}/download"
    delete_path = f"{list_path}/{document_id}/delete"

    for user in (lot["personel_user"], lot["yonetici_user"], lot["kardes_user"]):
        login(client, tenant_a, user)
        assert client.get(list_path).status_code in (403, 404)
        assert client.get(download_path).status_code == 404
        assert client.post(delete_path).status_code in (403, 404)

    login(client, tenant_a, lot["yonetici_user"])
    detail = client.get(f"/test-ui/api/employees/{employee_id}").json()["data"]
    assert detail["can_read_documents"] is False
    with tenant_context(tenant_a.id):
        manager_role = Role.objects.get(tenant=tenant_a, code="dept_manager")
        read_permission = Permission.objects.get(code="document.read")
        delete_permission = Permission.objects.get(code="document.delete")
        RolePermission.objects.create(
            tenant=tenant_a, role=manager_role, permission=delete_permission
        )
    assert client.post(delete_path).status_code == 403

    with tenant_context(tenant_a.id):
        RolePermission.objects.create(
            tenant=tenant_a, role=manager_role, permission=read_permission
        )
    rol_ver(tenant_a, lot["yonetici_user"], "tenant_officer")
    assert len(client.get(list_path).json()["data"]) == 1
    assert client.get(download_path).status_code == 200


    assert client.get(
        f"/test-ui/api/employees/{lot['kardes'].pk}/documents"
    ).status_code == 404

    with tenant_context(tenant_a.id):
        RolePermission.objects.filter(role=manager_role, permission=read_permission).delete()
    assert client.get(list_path).status_code == 403
    assert client.get(download_path).status_code == 404

    with tenant_context(tenant_a.id):
        override = UserPermissionOverride.objects.create(
            tenant=tenant_a,
            user=lot["personel_user"],
            permission=read_permission,
            effect="allow",
            reason="Kendi evraklarını görme",
            granted_by=lot["admin"],
        )
    login(client, tenant_a, lot["personel_user"])
    assert len(client.get(list_path).json()["data"]) == 1
    assert client.get(
        f"/test-ui/api/employees/{lot['kardes'].pk}/documents"
    ).status_code == 404
    with tenant_context(tenant_a.id):
        override.effect = "deny"
        override.save(update_fields=["effect", "updated_at"])
    assert client.get(list_path).status_code == 403

    rol_ver(tenant_a, lot["kardes_user"], "hr_manager")
    login(client, tenant_a, lot["kardes_user"])
    assert len(client.get(list_path).json()["data"]) == 1
    assert client.get(download_path).status_code == 200


def test_confidential_permission_is_absent_from_role_editor(tenant_a, user_a):  # noqa: ANN001
    lot = kadro(tenant_a, user_a)
    client = Client()
    login(client, tenant_a, lot["admin"])
    role = next(
        item for item in client.get("/test-ui/api/roles").json()["data"]
        if item["code"] == "employee"
    )
    path = f"/test-ui/api/roles/{role['id']}/permissions"
    entries = {
        entry["code"]: entry
        for module in client.get(path).json()["data"]["modules"]
        for entry in module["permissions"]
    }
    assert "document.confidential.read" not in entries
    assert entries["document.read"]["secili"] is False
    selected = {code for code, entry in entries.items() if entry["secili"]}
    response = client.post(
        path,
        data={"permission_codes": sorted(selected | {"document.confidential.read"})},
        content_type="application/json",
    )
    assert response.status_code == 400


def test_production_upload_fails_closed_when_scanner_is_unavailable(
    tenant_a, user_a, settings
) -> None:
    lot = kadro(tenant_a, user_a)
    client = Client()
    login(client, tenant_a, lot["admin"])
    settings.FILE_SCANNING_REQUIRED = True
    settings.CLAMAV_HOST = ""

    response = client.post(
        f"/test-ui/api/employees/{lot['personel'].pk}/documents",
        data={"files": SimpleUploadedFile("contract.txt", b"private document")},
    )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "file_scanner_unavailable"
    with tenant_context(tenant_a.id):
        assert not EmployeeDocument.objects.filter(employee=lot["personel"]).exists()


def test_malformed_pdf_is_rejected_before_storage(tenant_a, user_a) -> None:
    lot = kadro(tenant_a, user_a)
    client = Client()
    login(client, tenant_a, lot["admin"])

    response = client.post(
        f"/test-ui/api/employees/{lot['personel'].pk}/documents",
        data={"files": SimpleUploadedFile("broken.pdf", b"not a PDF")},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_file_structure"
    with tenant_context(tenant_a.id):
        assert not EmployeeDocument.objects.filter(employee=lot["personel"]).exists()
