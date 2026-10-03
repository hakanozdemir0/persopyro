"""Vardiya yetkilerinin eski varsayılan rol verisiyle onarımı."""

from datetime import time

import pytest

from apps.rbac.models import Permission, Role, RolePermission, RoleType, UserRole
from apps.shifts.services import can_manage_shifts, create_shift, ensure_shift_manager_access
from apps.tenancy.context import tenant_context


@pytest.mark.django_db
def test_shift_access_repairs_default_manager_scope_and_permissions(tenant_a, user_a):
    with tenant_context(tenant_a.id):
        role = Role.objects.create(
            tenant=tenant_a,
            name="Kiracı Yöneticisi",
            code="tenant_admin",
            role_type=RoleType.DEFAULT,
            scope_type="TENANT",
        )
        role_permission = Permission.objects.get(code="role.manage")
        RolePermission.objects.create(
            tenant=tenant_a, role=role, permission=role_permission
        )
        UserRole.objects.create(tenant=tenant_a, user=user_a, role=role)

        # Eski/bozuk rol kapsamı, permission resolver'ın yönetici iznini
        # fail-closed bırakmasına neden olur.
        role.scope_type = "UNIT"
        role.save(update_fields=["scope_type", "updated_at"])

        ensure_shift_manager_access(user_a)

        role.refresh_from_db()
        assert role.scope_type == "TENANT"
        assert can_manage_shifts(user_a, "shift.schedule.manage")


@pytest.mark.django_db
def test_create_shift_accepts_tenant_instance(tenant_a, user_a):
    with tenant_context(tenant_a.id):
        role = Role.objects.create(
            tenant=tenant_a,
            name="Kiracı Yöneticisi",
            code="tenant_admin",
            role_type=RoleType.DEFAULT,
            scope_type="TENANT",
        )
        permission = Permission.objects.get(code="role.manage")
        RolePermission.objects.create(tenant=tenant_a, role=role, permission=permission)
        UserRole.objects.create(tenant=tenant_a, user=user_a, role=role)

        vardiya = create_shift(
            actor=user_a,
            tenant=tenant_a,
            code="sabah",
            name="Sabah",
            start_time=time(8, 0),
            end_time=time(17, 0),
        )

        assert vardiya.tenant_id == tenant_a.id
