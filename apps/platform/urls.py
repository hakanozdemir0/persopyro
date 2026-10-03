from django.urls import path

from apps.platform import views

urlpatterns = [
    path("login/", views.platform_login, name="platform-login"),
    path("logout/", views.platform_logout, name="platform-logout"),
    path("tenants/create/", views.create_tenant, name="platform-tenant-create"),
    path("plans/", views.platform_plans, name="platform-plans"),
    path(
        "tenants/<uuid:tenant_id>/profile/",
        views.update_tenant_profile,
        name="platform-tenant-profile-update",
    ),
    path("tenants/<uuid:tenant_id>/people/", views.tenant_people, name="platform-tenant-people"),
    path("tenants/<uuid:tenant_id>/", views.tenant_detail, name="platform-tenant-detail"),
    path("", views.platform_dashboard, name="platform-dashboard"),
]
