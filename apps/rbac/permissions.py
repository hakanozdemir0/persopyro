"""
DRF yetki sınıfları.

İlke: **varsayılan reddet** (docs/AUTHORIZATION.md bölüm 2). Bir görünüm hangi
izni gerektirdiğini açıkça belirtmezse erişim verilmez.

Yetki kararı **tek merkezde** verilir:

    apps.rbac.services.authorized_permission_codes(request)

    etkin izinler = kullanıcının izinleri ∩ token kapsamı

Böylece token ile gelen bir istek, token'ın kapsamı dışındaki bir izni
kullanamaz (OD26 / F8); kiracı erişime kapalıysa veya kullanıcı pasifse hiçbir
izin geçerli olmaz (F11). Kesişim burada TEKRAR edilmez — tek kaynak servis
katmanıdır; aksi halde iki kural zamanla ayrışır.

Kullanım:

    class EmployeeListView(APIView):
        permission_classes = [IsAuthenticated, HasPermissionCodes]
        required_permissions = ["employee.read"]
"""

from rest_framework.permissions import BasePermission

from apps.rbac.services import authorized_permission_codes


class HasPermissionCodes(BasePermission):
    """
    `required_permissions` listesindeki TÜM kodları ister (AND).

    Liste tanımlı değilse erişim REDDEDİLİR; böylece yeni bir uç nokta
    yetkilendirmeyi unutarak yayına alınamaz.
    """

    message = "Bu işlem için yetkiniz yok."

    def has_permission(self, request, view) -> bool:  # noqa: ANN001
        kodlar = getattr(view, "required_permissions", None)
        if not kodlar:
            return False

        kullanici = getattr(request, "user", None)
        if kullanici is None or not kullanici.is_authenticated or not kullanici.is_active:
            return False

        etkin = authorized_permission_codes(request)
        return all(kod in etkin for kod in kodlar)


class AnyPermissionCode(BasePermission):
    """`required_permissions` listesindeki kodlardan EN AZ BİRİNİ ister (OR)."""

    message = "Bu işlem için yetkiniz yok."

    def has_permission(self, request, view) -> bool:  # noqa: ANN001
        kodlar = getattr(view, "required_permissions", None)
        if not kodlar:
            return False

        kullanici = getattr(request, "user", None)
        if kullanici is None or not kullanici.is_authenticated or not kullanici.is_active:
            return False

        etkin = authorized_permission_codes(request)
        return any(kod in etkin for kod in kodlar)
