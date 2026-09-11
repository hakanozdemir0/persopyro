"""
Rol/izin üretim servisleri.

Kapsam: yeni bir kiracı oluşturulduğunda varsayılan rollerin ve izin
eşleşmelerinin üretilmesi. Nesne seviyesi kapsam (kendi kaydı / kendi ekibi)
organizasyon modülüyle birlikte eklenecektir.

Soft-delete (OD21 kararı, docs/AUTHORIZATION.md): silinmiş bir kayıt **sessizce**
yeniden etkinleştirilmez; geri alma açık `*.restore` izni + denetim kaydı gerektirir.
Bu yüzden burada `get_or_create` kullanılmaz: silinmiş varsayılan rol bulunursa
otomatik restore **edilmez**, kopya **üretilmez**, durum açık bir hatayla bildirilir.
"""

from django.db import transaction

from apps.rbac.models import Permission, Role, RolePermission
from apps.rbac.permission_catalog import DEFAULT_ROLE_TEMPLATES


class MissingPermissionError(RuntimeError):
    """Şablonda tanımlı bir izin kodu veritabanında yoksa yükseltilir."""


class SoftDeletedDefaultRoleError(RuntimeError):
    """
    Varsayılan rol veya rol-izin bağı silinmiş durumda bulundu.

    Silinmiş kayıt yetki vermez (F7), dolayısıyla sessizce "var" sayılırsa kiracı
    **yetkisiz** kalır ama işlem başarılı görünür. Otomatik restore ise OD21'i ihlal
    eder. Bu yüzden işlem durdurulur ve çağıran açık bir geri alma kararı verir.
    """


@transaction.atomic
def create_default_roles(tenant) -> dict[str, Role]:
    """
    Kiracı için varsayılan rolleri üretir (idempotent).

    Var olan **etkin** rol atlanır; eksik izin eşleşmeleri tamamlanır. Şablonda
    tanımlı bir izin kodu veritabanında yoksa sessizce geçilmez, hata verilir: eksik
    izin "yetki verildi sanılan ama verilmeyen" bir güvenlik açığı yaratır.

    Silinmiş varsayılan rol/izin bağı varsa `SoftDeletedDefaultRoleError` yükseltilir;
    **hiçbir değişiklik yapılmaz** (doğrulama, değişiklikten önce çalışır).
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

    # Ön kontrol: silinmiş varsayılan rol var mı?
    # (Silinmiş rol yetki vermez; sessizce "var" saymak veya otomatik canlandırmak
    #  yanlış olur — OD21: geri alma açık izin + denetim kaydı gerektirir.)
    # NOT: `RolePermission` bağ tablosunda `deleted_at` yoktur (fiziksel silinir), bu
    # yüzden burada yalnızca roller denetlenir.
    mevcut_roller = {
        rol.code: rol
        for rol in Role.objects.filter(tenant=tenant, code__in=list(DEFAULT_ROLE_TEMPLATES))
    }
    silinmis: list[str] = [
        f"role:{kod}" for kod, rol in mevcut_roller.items() if rol.deleted_at is not None
    ]
    if silinmis:
        raise SoftDeletedDefaultRoleError(
            "Silinmiş varsayılan rol bulundu; otomatik geri alınmaz (OD21). "
            "Açık `*.restore` işlemi gerekir: " + ", ".join(sorted(silinmis))
        )

    olusan: dict[str, Role] = {}
    for kod, sablon in DEFAULT_ROLE_TEMPLATES.items():
        rol = mevcut_roller.get(kod)
        if rol is None:
            rol = Role.objects.create(
                tenant=tenant,
                code=kod,
                name=sablon["name"],
                role_type=sablon["role_type"],
                is_assignable=True,
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
