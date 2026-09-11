"""
Demo verisi oluşturucu (geliştirme/smoke testi için).

`manage.py shell` içine stdin olarak verilir:
    .venv/bin/python manage.py shell < scripts/_demo_data.py

Idempotenttir: aynı kiracı/kullanıcı varsa yeniden oluşturmaz.
Parola `DEMO_PASSWORD` ortam değişkeninden okunur (koda yazılmaz).
"""

import os
import uuid

from apps.accounts.models import User
from apps.rbac.models import Role, UserRole
from apps.rbac.services import create_default_roles
from apps.tenancy.context import tenant_context
from apps.tenancy.models import Tenant

TENANT_ID = uuid.uuid5(uuid.NAMESPACE_DNS, "demo.persopyro.local")
PAROLA = os.environ["DEMO_PASSWORD"]
EPOSTA = "demo@persopyro.test"

with tenant_context(TENANT_ID):
    kiracı, kiracı_olustu = Tenant.objects.get_or_create(
        id=TENANT_ID,
        defaults={"name": "Demo Şirket", "slug": "demo", "status": "active"},
    )
    roller = create_default_roles(kiracı)

    kullanıcı, kullanıcı_olustu = User.objects.get_or_create(
        tenant=kiracı,
        email=EPOSTA,
        defaults={"first_name": "Demo", "last_name": "Kullanıcı"},
    )
    if kullanıcı_olustu or not kullanıcı.has_usable_password():
        kullanıcı.set_password(PAROLA)
        kullanıcı.save(update_fields=["password", "password_changed_at", "updated_at"])

    # Demo kullanıcıya kiracı yöneticisi rolü verilir.
    rol = Role.objects.get(tenant=kiracı, code="tenant_admin")
    UserRole.objects.get_or_create(tenant=kiracı, user=kullanıcı, role=rol)

    izin_sayisi = len(
        {p for p in rol.role_permissions.values_list("permission__code", flat=True)}
    )

print(f"DEMO_HAZIR kiracı={kiracı.slug} slug={kiracı.slug} kullanıcı={EPOSTA} "
      f"rol_sayısı={len(roller)} admin_izin={izin_sayisi} "
      f"yeni_kiracı={kiracı_olustu} yeni_kullanıcı={kullanıcı_olustu}")
