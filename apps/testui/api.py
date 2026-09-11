"""
Manuel test arayüzü — JSON uç noktaları (yalnızca geliştirme).

İlkeler:
  * **Yetki kararı burada verilmez.** Her işlem mevcut servis katmanına delege
    edilir (`apps/organization/services.py`, `apps/leave/services/*`); orada
    `izin ∧ kapsam` kuralı ve RLS aynen geçerlidir. Arayüz yalnızca düğmeleri
    gizleyerek kolaylık sağlar; güvenlik kontrolü **backend'dedir**.
  * **Bypass yok:** `BYPASSRLS`, `is_staff`, süper kullanıcı veya sabit kiracı
    bağlamı kullanılmaz; kiracı oturumdan çözülür (middleware).
  * **K2/K3 gösterilmez:** `employee_personal_data` alanları bu uçlardan hiç
    dönmez.
  * Hata kodları: 400 (girdi), 401 (oturum yok), 403 (yetki/kapsam), 409 (durum
    çakışması). Yanıtlarda yığın izi (stack trace) **gösterilmez**.
"""

from __future__ import annotations

import uuid
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.db.models import Q
from django.http import Http404
from rest_framework import status
from rest_framework.authentication import SessionAuthentication
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.leave.models import (
    ApprovalStep,
    ApprovalStepStatus,
    LeaveBalance,
    LeaveRequest,
    LeaveRequestStatus,
    LeaveType,
    RecordStatus,
)
from apps.leave.services import (
    LeaveAuthorizationError,
    LeaveBalanceError,
    LeaveError,
    LeavePolicyError,
    LeaveStateError,
    can_approve_step,
    can_cancel_leave_request,
    can_create_leave_request_for,
    can_manage_leave_types,
    can_view_leave_request,
    cancel_leave_request,
    create_leave_request,
    current_step,
    decide_approval_step,
    user_employee,
)
from apps.leave.services.balances import get_or_create_balance, period_year_for
from apps.organization.models import Department, Employee, Position
from apps.organization.services import (
    OrganizationAuthorizationError,
    OrganizationError,
    OrganizationTreeError,
    create_department,
    create_employee,
    move_department,
    move_employee,
    soft_delete_department,
)
from apps.rbac.services import effective_permission_codes, user_has_permission
from apps.rbac.services.scope import user_org_scope


def _test_ui_kapali() -> None:
    """Arayüz kapalıysa uçlar **yok** sayılır (404)."""
    if not getattr(settings, "TEST_UI_ENABLED", False):
        raise Http404("test_ui_disabled")


class TestUiErrors:
    """Servis hatalarını HTTP kodlarına ve anlaşılır mesaja çevirir."""

    #: Durum/kural çakışması olarak raporlanan hatalar (409).
    CAKISMA = (LeaveStateError, LeavePolicyError, LeaveBalanceError, OrganizationTreeError)
    #: Yetki/kapsam reddi olarak raporlanan hatalar (403).
    YETKI = (LeaveAuthorizationError, OrganizationAuthorizationError)

    @classmethod
    def cevap(cls, hata: Exception) -> Response:
        if isinstance(hata, cls.YETKI):
            return Response(
                {
                    "error": {
                        "code": "forbidden",
                        "message": (
                            "Bu işlem için yetkiniz veya organizasyon kapsamınız yok."
                        ),
                        "detail": str(hata),
                    }
                },
                status=status.HTTP_403_FORBIDDEN,
            )
        if isinstance(hata, cls.CAKISMA):
            return Response(
                {
                    "error": {"code": "conflict", "message": str(hata), "detail": ""},
                },
                status=status.HTTP_409_CONFLICT,
            )
        if isinstance(hata, (LeaveError, OrganizationError)):
            return Response(
                {"error": {"code": "bad_request", "message": str(hata), "detail": ""}},
                status=status.HTTP_400_BAD_REQUEST,
            )
        raise hata


class TestUiView(APIView):
    """Ortak taban: oturum kimlik doğrulaması (CSRF korumalı) ve giriş zorunluluğu."""

    authentication_classes = [SessionAuthentication]
    permission_classes = [IsAuthenticated]

    def initial(self, request, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        _test_ui_kapali()
        return super().initial(request, *args, **kwargs)


def _uuid(deger, alan: str) -> uuid.UUID:  # noqa: ANN001
    try:
        return uuid.UUID(str(deger))
    except (TypeError, ValueError) as hata:
        raise LeaveError(f"'{alan}' geçerli bir kimlik değil.") from hata


def _tarih(deger, alan: str):  # noqa: ANN001, ANN202
    from datetime import date

    if not deger:
        raise LeaveError(f"'{alan}' zorunludur.")
    try:
        return date.fromisoformat(str(deger))
    except ValueError as hata:
        raise LeaveError(f"'{alan}' GG-AA-YYYY biçiminde olmalı.") from hata


def _sayi(deger):  # noqa: ANN001, ANN202
    if deger in (None, ""):
        return None
    try:
        return Decimal(str(deger))
    except (InvalidOperation, ValueError) as hata:
        raise LeaveError("Gün değeri sayısal olmalı.") from hata


# --- kapsam yardımcıları ------------------------------------------------------


def kapsamdaki_calisanlar(user):  # noqa: ANN001, ANN202
    """
    Aktörün etkin kapsamındaki çalışanlar (SELF ∨ UNIT alt ağacı ∨ TENANT).

    Kapsam `user_org_scope()` ile **aynı** kaynaktan gelir; burada yalnızca
    listeleme sorgusu kurulur, yetki kararı verilmez (karar servis katmanında).
    """
    kapsam = user_org_scope(user)
    temel = Employee.objects.filter(deleted_at__isnull=True).select_related(
        "department", "position", "user", "tenant"
    )
    if kapsam.tenant_wide:
        return temel
    kosullar = Q(user_id=user.pk)
    if kapsam.unit_ids:
        kosullar |= Q(department_id__in=list(kapsam.unit_ids))
    return temel.filter(kosullar)


def _kapsam_ozeti(user) -> dict:  # noqa: ANN001
    kapsam = user_org_scope(user)
    return {
        "tenant_wide": kapsam.tenant_wide,
        "unit_count": len(kapsam.unit_ids),
        "self_only": kapsam.self_only,
        "assigned_only": kapsam.assigned_only,
    }


def _calisan_ozeti(employee: Employee) -> dict:  # noqa: ANN001
    """Çalışan özeti — **K2/K3 alanları dahil edilmez**."""
    iliski = (
        employee.manager_relations.filter(
            relation_type="primary", effective_to__isnull=True, deleted_at__isnull=True
        )
        .select_related("manager_employee")
        .order_by("-effective_from")
        .first()
    )
    return {
        "id": str(employee.pk),
        "employee_no": employee.employee_no,
        "department": employee.department.name if employee.department_id else None,
        "department_id": str(employee.department_id) if employee.department_id else None,
        "position": employee.position.name if employee.position_id else None,
        "position_id": str(employee.position_id) if employee.position_id else None,
        "status": employee.status,
        "hire_date": employee.hire_date.isoformat() if employee.hire_date else None,
        "user_email": employee.user.email if employee.user_id else None,
        "manager": (
            {
                "employee_no": iliski.manager_employee.employee_no,
                "name": (
                    iliski.manager_employee.user.email
                    if iliski.manager_employee.user_id
                    else None
                ),
            }
            if iliski is not None
            else None
        ),
    }


def _talep_ozeti(talep, user) -> dict:  # noqa: ANN001
    adim = current_step(talep)
    return {
        "id": str(talep.pk),
        "employee_no": talep.employee.employee_no,
        "employee_id": str(talep.employee_id),
        "department": talep.employee.department.name if talep.employee.department_id else None,
        "leave_type": talep.leave_type.name,
        "leave_type_code": talep.leave_type.code,
        "start_date": talep.start_date.isoformat(),
        "end_date": talep.end_date.isoformat(),
        "days_requested": str(talep.days_requested),
        "status": talep.status,
        "reason": talep.reason,
        "submitted_at": talep.submitted_at.isoformat() if talep.submitted_at else None,
        "decided_at": talep.decided_at.isoformat() if talep.decided_at else None,
        "canceled_at": talep.canceled_at.isoformat() if talep.canceled_at else None,
        "requires_document": talep.leave_type.requires_document,
        "document_ref": str(talep.document_ref) if talep.document_ref else None,
        "current_step_no": adim.step_no if adim is not None else None,
        "can_approve": can_approve_step(user, adim, talep),
        "can_cancel": can_cancel_leave_request(user, talep),
        "can_view": can_view_leave_request(user, talep),
    }


def _bakiye_ozeti(bakiye: LeaveBalance) -> dict:  # noqa: ANN001
    """Bakiye — türetilen değerler **backend'den** gelir (üretilen kolon)."""
    return {
        "leave_type": bakiye.leave_type.name,
        "leave_type_code": bakiye.leave_type.code,
        "period_year": bakiye.period_year,
        "entitled_days": str(bakiye.entitled_days),
        "carried_forward_days": str(bakiye.carried_forward_days),
        "adjustment_days": str(bakiye.adjustment_days),
        "used_days": str(bakiye.used_days),
        "reserved_days": str(bakiye.reserved_days),
        "available_days": str(bakiye.available_days),
    }


# --- uç noktalar --------------------------------------------------------------


class MeView(TestUiView):
    """Oturumdaki kullanıcı, kiracı, roller, kapsam ve etkin izinler."""

    def get(self, request):  # noqa: ANN001
        user = request.user
        from apps.rbac.models import UserRole  # noqa: PLC0415

        roller = [
            atama.role.code
            for atama in UserRole.objects.filter(user=user, deleted_at__isnull=True)
            .select_related("role")
            .order_by("role__code")
            if atama.role is not None and atama.role.deleted_at is None
        ]
        calisan = user_employee(user)
        izinler = sorted(effective_permission_codes(user))
        return Response(
            {
                "data": {
                    "user": {
                        "email": user.email,
                        "name": f"{user.first_name} {user.last_name}".strip(),
                        "public_id": getattr(user, "public_id", ""),
                    },
                    "tenant": {"id": str(user.tenant_id), "name": user.tenant.name},
                    "roles": roller,
                    "scope": _kapsam_ozeti(user),
                    "employee": _calisan_ozeti(calisan) if calisan is not None else None,
                    "permissions": izinler,
                    "menu": {
                        "departments_manage": user_has_permission(user, "org.department.manage"),
                        "employees_create": user_has_permission(user, "employee.create"),
                        "leave_create": user_has_permission(user, "leave.request.create")
                        or can_create_leave_request_for(user, calisan),
                        "leave_approve": user_has_permission(user, "leave.request.approve"),
                        "leave_types_manage": can_manage_leave_types(user),
                    },
                }
            }
        )


class DashboardView(TestUiView):
    """Panel: kişi/kiracı özeti + kendi bakiyeleri + bekleyen talepler."""

    def get(self, request):  # noqa: ANN001
        user = request.user
        calisan = user_employee(user)
        yil = period_year_for()

        bakiyeler = []
        if calisan is not None and user_has_permission(user, "leave.balance.read"):
            for tur in LeaveType.objects.filter(
                deleted_at__isnull=True, uses_balance=True
            ).order_by("code"):
                if not tur.balances.filter(employee=calisan, period_year=yil).exists():
                    continue
                bakiye = get_or_create_balance(
                    employee=calisan, leave_type=tur, period_year=yil
                )
                bakiyeler.append(_bakiye_ozeti(bakiye))

        kendi_talepleri = []
        if calisan is not None:
            kendi_talepleri = [
                _talep_ozeti(t, user)
                for t in (
                    calisan.leave_requests.filter(deleted_at__isnull=True)
                    .select_related("employee__department", "leave_type", "employee")
                    .order_by("-start_date")[:10]
                )
            ]

        onay_bekleyen = [
            _talep_ozeti(t, user)
            for t in (
                LeaveRequest.objects.filter(
                    deleted_at__isnull=True, status=LeaveRequestStatus.PENDING
                )
                .select_related("employee__department", "leave_type", "employee")
                .order_by("start_date")[:50]
            )
            if can_view_leave_request(user, t)
        ]

        return Response(
            {
                "data": {
                    "employee": _calisan_ozeti(calisan) if calisan is not None else None,
                    "period_year": yil,
                    "balances": bakiyeler,
                    "my_requests": kendi_talepleri,
                    "pending_requests": onay_bekleyen,
                    "pending_count": len(onay_bekleyen),
                }
            }
        )


class DepartmentListView(TestUiView):
    """Departman ağacı (kiracı içi; mutasyon yetkisi servis katmanında)."""

    def get(self, request):  # noqa: ANN001
        kayitlar = list(
            Department.objects.filter(deleted_at__isnull=True)
            .select_related("parent", "manager_employee")
            .order_by("code")
        )
        return Response(
            {
                "data": [
                    {
                        "id": str(d.id),
                        "code": d.code,
                        "name": d.name,
                        "parent_id": str(d.parent_id) if d.parent_id else None,
                        "parent_code": d.parent.code if d.parent_id else None,
                        "status": d.status,
                        "manager_employee_no": (
                            d.manager_employee.employee_no if d.manager_employee_id else None
                        ),
                        "employee_count": d.employees.filter(deleted_at__isnull=True).count(),
                    }
                    for d in kayitlar
                ],
                "meta": {
                    "can_manage": user_has_permission(request.user, "org.department.manage")
                },
            }
        )

    def post(self, request):  # noqa: ANN001
        veri = request.data
        try:
            parent = None
            if veri.get("parent_id"):
                parent = Department.objects.filter(
                    pk=_uuid(veri["parent_id"], "parent_id"), deleted_at__isnull=True
                ).first()
                if parent is None:
                    raise LeaveError("Üst birim bulunamadı.")
            birim = create_department(
                actor=request.user,
                tenant=request.user.tenant,
                code=str(veri.get("code", "")).strip(),
                name=str(veri.get("name", "")).strip(),
                parent=parent,
            )
        except (LeaveError, OrganizationError, LeaveAuthorizationError) as hata:
            return TestUiErrors.cevap(hata)
        return Response({"data": {"id": str(birim.pk)}}, status=status.HTTP_201_CREATED)


class DepartmentMoveView(TestUiView):
    """Birimin üst birimini değiştirir (taşıma)."""

    def post(self, request, department_id: str):  # noqa: ANN001
        try:
            birim = Department.objects.filter(pk=_uuid(department_id, "id")).first()
            if birim is None:
                raise LeaveError("Birim bulunamadı.")
            yeni_parent = None
            if request.data.get("parent_id"):
                yeni_parent = Department.objects.filter(
                    pk=_uuid(request.data["parent_id"], "parent_id"), deleted_at__isnull=True
                ).first()
                if yeni_parent is None:
                    raise LeaveError("Hedef üst birim bulunamadı.")
            tasindi = move_department(
                actor=request.user,
                department=birim,
                new_parent=yeni_parent,
                reason=str(request.data.get("reason", "")).strip(),
            )
        except (LeaveError, OrganizationError, LeaveAuthorizationError) as hata:
            return TestUiErrors.cevap(hata)
        return Response({"data": {"id": str(tasindi.pk)}})


class DepartmentDeleteView(TestUiView):
    """Birimi **soft delete** eder (OD21; fiziksel silme yok)."""

    def post(self, request, department_id: str):  # noqa: ANN001
        try:
            birim = Department.objects.filter(pk=_uuid(department_id, "id")).first()
            if birim is None:
                raise LeaveError("Birim bulunamadı.")
            silinen = soft_delete_department(
                actor=request.user,
                department=birim,
                reason=str(request.data.get("reason", "")).strip(),
            )
        except (LeaveError, OrganizationError, LeaveAuthorizationError) as hata:
            return TestUiErrors.cevap(hata)
        return Response({"data": {"id": str(silinen.pk), "status": "deleted"}})


class EmployeeListView(TestUiView):
    """Kapsamdaki çalışanlar + oluşturma."""

    def get(self, request):  # noqa: ANN001
        return Response(
            {
                "data": [_calisan_ozeti(e) for e in kapsamdaki_calisanlar(request.user)],
                "meta": {
                    "can_create": user_has_permission(request.user, "employee.create"),
                    "scope": _kapsam_ozeti(request.user),
                },
            }
        )

    def post(self, request):  # noqa: ANN001
        veri = request.data
        try:
            department = Department.objects.filter(
                pk=_uuid(veri.get("department_id"), "department_id"), deleted_at__isnull=True
            ).first()
            if department is None:
                raise LeaveError("Birim seçilmelidir.")
            position = None
            if veri.get("position_id"):
                position = Position.objects.filter(
                    pk=_uuid(veri["position_id"], "position_id"), deleted_at__isnull=True
                ).first()
            user = None
            if veri.get("user_email"):
                from django.contrib.auth import get_user_model  # noqa: PLC0415

                user = get_user_model().objects.filter(email=veri["user_email"]).first()
                if user is None:
                    raise LeaveError("Bağlanacak kullanıcı bulunamadı.")
            calisan = create_employee(
                actor=request.user,
                tenant=request.user.tenant,
                department=department,
                employee_no=str(veri.get("employee_no", "")).strip(),
                hire_date=_tarih(veri.get("hire_date"), "hire_date"),
                position=position,
                user=user,
            )
        except (LeaveError, OrganizationError, LeaveAuthorizationError) as hata:
            return TestUiErrors.cevap(hata)
        return Response({"data": _calisan_ozeti(calisan)}, status=status.HTTP_201_CREATED)


class EmployeeMoveView(TestUiView):
    """Çalışanın birimini değiştirir (denetimli: `employee.department_change`)."""

    def post(self, request, employee_id: str):  # noqa: ANN001
        try:
            calisan = Employee.objects.filter(pk=_uuid(employee_id, "id")).first()
            if calisan is None:
                raise LeaveError("Çalışan bulunamadı.")
            department = Department.objects.filter(
                pk=_uuid(request.data.get("department_id"), "department_id"),
                deleted_at__isnull=True,
            ).first()
            if department is None:
                raise LeaveError("Hedef birim bulunamadı.")
            tasindi = move_employee(
                actor=request.user,
                employee=calisan,
                department=department,
                reason=str(request.data.get("reason", "")).strip(),
            )
        except (LeaveError, OrganizationError, LeaveAuthorizationError) as hata:
            return TestUiErrors.cevap(hata)
        return Response({"data": _calisan_ozeti(tasindi)})


class EmployeeBalanceView(TestUiView):
    """Çalışanın dönem bakiyeleri (türetilen değerler backend'den)."""

    def get(self, request, employee_id: str):  # noqa: ANN001
        from apps.leave.services import can_read_balances  # noqa: PLC0415

        calisan = Employee.objects.filter(pk=_uuid(employee_id, "id")).first()
        if calisan is None:
            return Response(
                {"error": {"code": "not_found", "message": "Çalışan bulunamadı.", "detail": ""}},
                status=status.HTTP_404_NOT_FOUND,
            )
        if not can_read_balances(request.user, calisan):
            return TestUiErrors.cevap(LeaveAuthorizationError("Bakiye görme yetkisi/kapsamı yok."))
        yil = period_year_for()
        bakiyeler = [
            _bakiye_ozeti(b)
            for b in LeaveBalance.objects.filter(employee=calisan, period_year=yil)
            .select_related("leave_type")
            .order_by("leave_type__code")
        ]
        return Response({"data": bakiyeler, "meta": {"employee_no": calisan.employee_no}})


class LookupView(TestUiView):
    """Form seçenekleri: birimler, pozisyonlar, izin türleri."""

    def get(self, request):  # noqa: ANN001
        return Response(
            {
                "data": {
                    "departments": [
                        {"id": str(d.pk), "code": d.code, "name": d.name}
                        for d in Department.objects.filter(deleted_at__isnull=True).order_by("code")
                    ],
                    "positions": [
                        {"id": str(p.pk), "code": p.code, "name": p.name}
                        for p in Position.objects.filter(deleted_at__isnull=True).order_by("code")
                    ],
                    "leave_types": [
                        {
                            "id": str(t.pk),
                            "code": t.code,
                            "name": t.name,
                            "uses_balance": t.uses_balance,
                            "requires_document": t.requires_document,
                        }
                        for t in LeaveType.objects.filter(
                            deleted_at__isnull=True, status=RecordStatus.ACTIVE
                        ).order_by("code")
                    ],
                    "recent_days": 90,
                }
            }
        )


class LeaveRequestListView(TestUiView):
    """İzin talepleri: `mine` (kendi) veya `pending` (kapsamımdaki onay bekleyenler)."""

    def get(self, request):  # noqa: ANN001
        kapsam = request.query_params.get("scope", "mine")
        user = request.user
        temel = LeaveRequest.objects.filter(deleted_at__isnull=True).select_related(
            "employee__department", "leave_type", "employee"
        )
        if kapsam == "pending":
            adaylar = temel.filter(status=LeaveRequestStatus.PENDING).order_by("start_date")
            talepler = [t for t in adaylar if can_view_leave_request(user, t)]
        else:
            calisan = user_employee(user)
            talepler = (
                list(temel.filter(employee=calisan).order_by("-start_date")) if calisan else []
            )
        return Response(
            {
                "data": [_talep_ozeti(t, user) for t in talepler],
                "meta": {"scope": kapsam},
            }
        )

    def post(self, request):  # noqa: ANN001
        veri = request.data
        user = request.user
        try:
            hedef = user_employee(user)
            if veri.get("employee_id"):
                hedef = Employee.objects.filter(
                    pk=_uuid(veri["employee_id"], "employee_id"), deleted_at__isnull=True
                ).first()
                if hedef is None:
                    raise LeaveError("Çalışan bulunamadı.")
            if hedef is None:
                raise LeaveError("Çalışan kaydınız yok; talep oluşturulamaz.")
            tur = LeaveType.objects.filter(
                pk=_uuid(veri.get("leave_type_id"), "leave_type_id"), deleted_at__isnull=True
            ).first()
            if tur is None:
                raise LeaveError("İzin türü bulunamadı.")
            talep = create_leave_request(
                actor=user,
                employee=hedef,
                leave_type=tur,
                start_date=_tarih(veri.get("start_date"), "start_date"),
                end_date=_tarih(veri.get("end_date"), "end_date"),
                reason=str(veri.get("reason", "")).strip(),
                is_half_day=bool(veri.get("is_half_day", False)),
                requested_days=_sayi(veri.get("requested_days")),
                submit=bool(veri.get("submit", True)),
            )
        except (LeaveError, LeaveAuthorizationError) as hata:
            return TestUiErrors.cevap(hata)
        return Response(
            {"data": _talep_ozeti(talep, user)}, status=status.HTTP_201_CREATED
        )


class LeaveRequestDecisionView(TestUiView):
    """Geçerli onay adımı için karar (izin + kapsam + adım sahipliği sunucuda)."""

    def post(self, request, request_id: str):  # noqa: ANN001
        talep = LeaveRequest.objects.filter(pk=_uuid(request_id, "id")).first()
        if talep is None:
            return Response(
                {"error": {"code": "not_found", "message": "Talep bulunamadı.", "detail": ""}},
                status=status.HTTP_404_NOT_FOUND,
            )
        karar = str(request.data.get("decision", "")).strip()
        if karar not in (ApprovalStepStatus.APPROVED, ApprovalStepStatus.REJECTED):
            return Response(
                {
                    "error": {
                        "code": "bad_request",
                        "message": "Karar 'approved' veya 'rejected' olmalıdır.",
                        "detail": "",
                    }
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            sonuc = decide_approval_step(
                actor=request.user,
                request=talep,
                decision=karar,
                reason=str(request.data.get("reason", "")).strip(),
            )
        except (LeaveError, LeaveAuthorizationError) as hata:
            return TestUiErrors.cevap(hata)
        return Response({"data": _talep_ozeti(sonuc, request.user)})


class LeaveRequestCancelView(TestUiView):
    """Talebi iptal eder (gerekçe zorunlu; bakiye davranışı servis kararı)."""

    def post(self, request, request_id: str):  # noqa: ANN001
        talep = LeaveRequest.objects.filter(pk=_uuid(request_id, "id")).first()
        if talep is None:
            return Response(
                {"error": {"code": "not_found", "message": "Talep bulunamadı.", "detail": ""}},
                status=status.HTTP_404_NOT_FOUND,
            )
        try:
            sonuc = cancel_leave_request(
                actor=request.user,
                request=talep,
                reason=str(request.data.get("reason", "")).strip(),
            )
        except (LeaveError, LeaveAuthorizationError) as hata:
            return TestUiErrors.cevap(hata)
        return Response({"data": _talep_ozeti(sonuc, request.user)})


__all__ = [
    "ApprovalStep",
    "DashboardView",
    "DepartmentDeleteView",
    "DepartmentListView",
    "DepartmentMoveView",
    "EmployeeBalanceView",
    "EmployeeListView",
    "EmployeeMoveView",
    "LeaveRequestCancelView",
    "LeaveRequestDecisionView",
    "LeaveRequestListView",
    "LookupView",
    "MeView",
    "TestUiView",
    "kapsamdaki_calisanlar",
]
