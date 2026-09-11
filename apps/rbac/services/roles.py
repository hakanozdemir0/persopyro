"""
Rol/izin üretim servisleri.

Kapsam: yeni bir kiracı oluşturulduğunda varsayılan rollerin ve izin
eşleşmelerinin üretilmesi. Nesne seviyesi kapsam (kendi kaydı / kendi ekibi)
organizasyon modülüyle birlikte eklenecektir.
"""

from django.db import transaction

from apps.rbac.models import Permission, Role, RolePermission
from apps.rbac.permission_catalog import DEFAULT_ROLE_TEMPLATES


class MissingPermissionError(RuntimeError):
    """Şablonda tanımlı bir izin kodu veritabanında yoksa yükseltilir."""


@transaction.atomic
def create_default_roles(tenant) -> dict[str, Role]:
    """
    Kiracı için varsayılan rolleri üretir (idempotent).

    Var olan rol atlanır; izin eşleşmeleri tamamlanır. Şablonda tanımlı bir izin
    kodu veritabanında yoksa sessizce geçilmez, hata verilir: eksik izin
    "yetki verildi sanılan ama verilmeyen" bir güvenlik açığı yaratır.
    """
    izinler = {p.code: p for p in Permission.objects.all()}
    eksikler = sorted(
        {
            kod
            for sablon in DEFAULT_ROLE_TEMPLATES.values()
            for kod in sablon["permissions"]
            if kod not in izinler
        }
    )
    if eksikler:
        raise MissingPermissionError(
            "Şablonda tanımlı izin kodları veritabanında yok: " + ", ".join(eksikler)
        )

    olusan: dict[str, Role] = {}
    for kod, sablon in DEFAULT_ROLE_TEMPLATES.items():
        rol, _ = Role.objects.get_or_create(
            tenant=tenant,
            code=kod,
            defaults={
                "name": sablon["name"],
                "role_type": sablon["role_type"],
                "is_assignable": True,
            },
        )
        mevcut = set(
            RolePermission.objects.filter(role=rol).values_list("permission__code", flat=True)
        )
        yeni = [
            RolePermission(tenant=tenant, role=rol, permission=izinler[izin_kodu])
            for izin_kodu in sablon["permissions"]
            if izin_kodu not in mevcut
        ]
        if yeni:
            RolePermission.objects.bulk_create(yeni, ignore_conflicts=True)
        olusan[kod] = rol
    return olusan
