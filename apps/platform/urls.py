from django.urls import path

from apps.platform import views

urlpatterns = [
    path("login/", views.platform_login, name="platform-login"),
    path("logout/", views.platform_logout, name="platform-logout"),
    path("tenants/create/", views.create_tenant, name="platform-tenant-create"),
    path("tenants/<uuid:tenant_id>/status/", views.tenant_status_toggle, name="platform-tenant-status"),
    path("plans/", views.platform_plans, name="platform-plans"),
    path("admins/", views.platform_admins, name="platform-admins"),
    path("audit/", views.platform_audit, name="platform-audit"),
    path("profile/", views.platform_profile, name="platform-profile"),
    path(
        "tenants/<uuid:tenant_id>/profile/",
        views.update_tenant_profile,
        name="platform-tenant-profile-update",
    ),
    path("tenants/<uuid:tenant_id>/people/", views.tenant_people, name="platform-tenant-people"),
    path("tenants/<uuid:tenant_id>/admins/<uuid:user_id>/credentials/", views.tenant_admin_credentials, name="platform-tenant-admin-credentials"),
    path("tenants/<uuid:tenant_id>/", views.tenant_detail, name="platform-tenant-detail"),
    path("", views.platform_dashboard, name="platform-dashboard"),
]
