"""
DRF yetki sınıfları.

İlke: **varsayılan reddet** (docs/AUTHORIZATION.md bölüm 2). Bir görünüm hangi
izni gerektirdiğini açıkça belirtmezse erişim verilmez.

Kullanım:

    class EmployeeListView(APIView):
        permission_classes = [IsAuthenticated, HasPermissionCodes]
        required_permissions = ["employee.read"]
"""

from rest_framework.permissions import BasePermission

from apps.rbac.services import effective_permission_codes


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

        etkin = effective_permission_codes(kullanici)
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

        etkin = effective_permission_codes(kullanici)
        return any(kod in etkin for kod in kodlar)
