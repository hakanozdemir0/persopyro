"""
Manuel test arayüzü URL'leri (önek: `/test-ui/`).

Kapı: `TEST_UI_ENABLED` (varsayılan `DJANGO_DEBUG`). Kapalıyken her görünüm ve uç
**404** döner (savunma derinliği). Üretimde yalnızca `PERSOPYRO_TEST_UI=1` ile açılır.

Giriş/çıkış için **yeni uç yoktur**: mevcut oturum uçları kullanılır
(`/api/v1/auth/login`, `/api/v1/auth/logout`).
"""

from django.urls import path

from apps.testui import api, views

urlpatterns = [
    # Sayfalar
    path("", views.app_page, name="test-ui-app"),
    path("login/", views.login_page, name="test-ui-login"),
    # JSON uçları (yalnızca bu ad alanı: üretim `/api/v1` sözleşmesine dokunulmaz)
    path("api/me", api.MeView.as_view(), name="test-ui-me"),
    path("api/dashboard", api.DashboardView.as_view(), name="test-ui-dashboard"),
    path("api/lookups", api.LookupView.as_view(), name="test-ui-lookups"),
    path("api/departments", api.DepartmentListView.as_view(), name="test-ui-departments"),
    path(
        "api/departments/<str:department_id>/move",
        api.DepartmentMoveView.as_view(),
        name="test-ui-department-move",
    ),
    path(
        "api/departments/<str:department_id>/update",
        api.DepartmentUpdateView.as_view(),
        name="test-ui-department-update",
    ),
    path(
        "api/departments/<str:department_id>/delete",
        api.DepartmentDeleteView.as_view(),
        name="test-ui-department-delete",
    ),
    path("api/employees", api.EmployeeListView.as_view(), name="test-ui-employees"),
    path(
        "api/employees/<str:employee_id>",
        api.EmployeeDetailView.as_view(),
        name="test-ui-employee-detail",
    ),
    path(
        "api/employees/<str:employee_id>/roles",
        api.EmployeeRoleAssignView.as_view(),
        name="test-ui-employee-role-assign",
    ),
    path("api/balances", api.BalanceListView.as_view(), name="test-ui-balances"),
    path("api/balances/adjust", api.BalanceAdjustView.as_view(), name="test-ui-balance-adjust"),
    path("api/roles", api.RoleListView.as_view(), name="test-ui-roles"),
    path("api/roles/create", api.RoleCreateView.as_view(), name="test-ui-role-create"),
    path("api/leave-policies", api.LeavePolicyListView.as_view(), name="test-ui-leave-policies"),
    path(
        "api/leave-policies/<str:policy_id>/update",
        api.LeavePolicyUpdateView.as_view(),
        name="test-ui-leave-policy-update",
    ),
    path(
        "api/roles/<str:role_id>/update",
        api.RoleUpdateView.as_view(),
        name="test-ui-role-update",
    ),
    path("api/users", api.UserCreateView.as_view(), name="test-ui-users"),
    path(
        "api/employees/<str:employee_id>/personal",
        api.EmployeePersonalView.as_view(),
        name="test-ui-employee-personal",
    ),
    path(
        "api/roles/<str:role_id>/permissions",
        api.RolePermissionView.as_view(),
        name="test-ui-role-permissions",
    ),
    path("api/positions", api.PositionCreateView.as_view(), name="test-ui-positions"),
    path("api/leave-types", api.LeaveTypeListView.as_view(), name="test-ui-leave-types"),
    path(
        "api/leave-types/<str:leave_type_id>/deactivate",
        api.LeaveTypeDeactivateView.as_view(),
        name="test-ui-leave-type-deactivate",
    ),
    path(
        "api/role-assignments/<str:assignment_id>/revoke",
        api.RoleAssignmentRevokeView.as_view(),
        name="test-ui-role-revoke",
    ),
    path(
        "api/role-assignments/<str:assignment_id>/restore",
        api.RoleAssignmentRestoreView.as_view(),
        name="test-ui-role-restore",
    ),
    path(
        "api/employees/<str:employee_id>/move",
        api.EmployeeMoveView.as_view(),
        name="test-ui-employee-move",
    ),
    path(
        "api/employees/<str:employee_id>/balances",
        api.EmployeeBalanceView.as_view(),
        name="test-ui-employee-balances",
    ),
    # İzin hakkı tanımlama/düzeltme (mutlak gün + gerekçe; servis katmanına gider).
    path(
        "api/employees/<str:employee_id>/entitlements",
        api.EmployeeEntitlementView.as_view(),
        name="test-ui-employee-entitlements",
    ),
    # P1-B: yaşam döngüsü + geçmiş + atama/koşul/yönetici değişikliği uçları.
    path(
        "api/employees/<str:employee_id>/history",
        api.EmployeeHistoryView.as_view(),
        name="test-ui-employee-history",
    ),
    path(
        "api/employees/<str:employee_id>/lifecycle/<str:islem>",
        api.EmployeeLifecycleView.as_view(),
        name="test-ui-employee-lifecycle",
    ),
    path(
        "api/employees/<str:employee_id>/assignment",
        api.EmployeeAssignmentChangeView.as_view(),
        name="test-ui-employee-assignment",
    ),
    path(
        "api/employees/<str:employee_id>/employment-terms",
        api.EmployeeTermsChangeView.as_view(),
        name="test-ui-employee-terms",
    ),
    path(
        "api/employees/<str:employee_id>/manager",
        api.EmployeeManagerChangeView.as_view(),
        name="test-ui-employee-manager",
    ),
    # Fazla mesai (P5a V1): bildirim, karar ve iptal.
    path("api/overtime", api.OvertimeListView.as_view(), name="test-ui-overtime"),
    path(
        "api/overtime/<str:overtime_id>/decision",
        api.OvertimeDecisionView.as_view(),
        name="test-ui-overtime-decision",
    ),
    path(
        "api/overtime/<str:overtime_id>/cancel",
        api.OvertimeCancelView.as_view(),
        name="test-ui-overtime-cancel",
    ),
    path("api/leave-requests", api.LeaveRequestListView.as_view(), name="test-ui-leave-requests"),
    path(
        "api/leave-requests/<str:request_id>/decision",
        api.LeaveRequestDecisionView.as_view(),
        name="test-ui-leave-decision",
    ),
    path(
        "api/leave-requests/<str:request_id>/cancel",
        api.LeaveRequestCancelView.as_view(),
        name="test-ui-leave-cancel",
    ),
]
