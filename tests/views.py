"""
Testler için sonda (probe) görünümleri.

Üretim URL yapılandırmasına eklenmez; `tests/urls.py` üzerinden yalnızca
testlerde devreye girer. Amaç: istek hattının (kiracı bağlamı, kimlik
doğrulama) ve yetki sınıflarının gerçek istek üzerinden sınanması.
"""

from django.http import JsonResponse
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.rbac.permissions import HasPermissionCodes
from apps.tenancy.context import current_tenant_id


def baglam_sondasi(request):
    """İstek hattının kurduğu kiracı bağlamını ve kullanıcıyı döndürür."""
    return JsonResponse(
        {
            "tenant": str(current_tenant_id()) if current_tenant_id() else None,
            "user": request.user.email if request.user.is_authenticated else None,
            "request_id": getattr(request, "request_id", ""),
        }
    )


class KorumaliSonda(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(
            {
                "tenant": str(current_tenant_id()) if current_tenant_id() else None,
                "user": request.user.email,
            }
        )


class IzinliSonda(APIView):
    """`required_permissions` ile korunan sonda."""

    permission_classes = [IsAuthenticated, HasPermissionCodes]
    required_permissions = ["employee.read"]

    def get(self, request):
        return Response({"ok": True, "user": request.user.email})


class IzinliIzinsizSonda(APIView):
    """
    Farklı bir izin isteyen sonda (`leave.request.approve`).

    Token kapsamı (`scopes`) kesişiminin sınanması için kullanılır: kullanıcı bu
    izne sahip olsa bile token'ın kapsamı dışındaysa istek `403` dönmelidir.
    """

    permission_classes = [IsAuthenticated, HasPermissionCodes]
    required_permissions = ["leave.request.approve"]

    def get(self, request):
        return Response({"ok": True, "user": request.user.email})


class IzinsizTanimsizSonda(APIView):
    """İzni belirtilmemiş görünüm: varsayılan reddet kuralı gereği 403 dönmeli."""

    permission_classes = [IsAuthenticated, HasPermissionCodes]

    def get(self, request):
        return Response({"ok": True})
