"""
Manuel test arayüzü — JSON uç noktaları (yalnızca geliştirme).

İlkeler:
  * **Yetki kararı burada verilmez.** Her işlem mevcut servis katmanına delege
    edilir (`apps/organization/services.py`, `apps/leave/services/*`); orada
    `izin ∧ kapsam` kuralı ve RLS aynen geçerlidir. Arayüz yalnızca düğmeleri
    gizleyerek kolaylık sağlar; güvenlik kontrolü **backend'dedir**.
  * **Bypass yok:** `BYPASSRLS`, `is_staff`, süper kullanıcı veya sabit kiracı
    bağlamı kullanılmaz; kiracı oturumdan çözülür (middleware).
  * **K2/K3 varsayılan gizli:** `employee_personal_data` alanları liste ve özet
    yanıtlarında **hiç dönmez**; yalnızca `employee.sensitive.read` + organizasyon
    kapsamı kapısından geçen `/personal` ucundan okunur/yazılır. K3 (özel nitelikli:
    sağlık) alanlar veritabanında **şifreli** durur (bkz. OD30/OD31).
  * Hata kodları: 400 (girdi), 401 (oturum yok), 403 (yetki/kapsam), 409 (durum
    çakışması). Yanıtlarda yığın izi (stack trace) **gösterilmez**.
"""

from __future__ import annotations

import uuid
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.db.models import Q
from django.http import Http404
from django.utils import timezone
from rest_framework import status
from rest_framework.authentication import SessionAuthentication
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import UserStatus
from apps.common.crypto import SecretDecryptionError
from apps.leave.leave_type_catalog import LeaveCategory
from apps.leave.models import (
    ApprovalStep,
    ApprovalStepStatus,
    LeaveBalance,
    LeavePolicy,
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
    create_leave_type,
    current_step,
    deactivate_leave_type,
    decide_approval_step,
    update_leave_policy,
    user_employee,
)
from apps.leave.services.balances import get_or_create_balance, period_year_for
from apps.organization.models import BloodType, Department, Employee, Position
from apps.organization.services import (
    OrganizationAuthorizationError,
    OrganizationError,
    OrganizationTreeError,
    create_department,
    create_employee,
    create_position,
    move_department,
    move_employee,
    set_primary_manager,
    soft_delete_department,
    sub_tree_department_ids,
    update_department,
)
from apps.rbac.models import (
    ORG_SCOPED_ROLE_SCOPE_TYPES,
    Permission,
    Role,
    RolePermission,
    UserPermissionOverride,
    UserRole,
)
from apps.rbac.services import (
    RoleAssignmentError,
    RoleAssignmentValidationError,
    assign_role_to_user,
    can_assign_role,
    create_custom_role,
    effective_permission_codes,
    restore_role_assignment,
    revoke_role_assignment,
    update_role_meta,
    update_role_permissions,
    user_has_permission,
)
from apps.rbac.services.scope import user_org_scope
from apps.testui.labels import (
    MODUL_ACIKLAMALARI,
    istisna_etiketi,
    kapsam_metni,
    kapsam_tipi_etiketi,
    modul_etiketi,
)


def _test_ui_kapali() -> None:
    """Arayüz kapalıysa uçlar **yok** sayılır (404)."""
    if not getattr(settings, "TEST_UI_ENABLED", False):
        raise Http404("test_ui_disabled")


class TestUiAuthorizationError(RuntimeError):
    """Test arayüzü katmanının kendi yetki reddi (HTTP 403)."""


class TestUiErrors:
    """Servis hatalarını HTTP kodlarına ve anlaşılır mesaja çevirir."""

    #: Durum/kural çakışması olarak raporlanan hatalar (409).
    CAKISMA = (LeaveStateError, LeavePolicyError, LeaveBalanceError, OrganizationTreeError)
    #: Yetki/kapsam reddi olarak raporlanan hatalar (403). `RoleAssignmentError`
    #: self-elevation/kapsam/izin redlerini taşır (Python `PermissionError` alt sınıfı).
    YETKI = (LeaveAuthorizationError, OrganizationAuthorizationError, RoleAssignmentError)

    @classmethod
    def cevap(cls, hata: Exception) -> Response:
        if isinstance(hata, TestUiAuthorizationError):
            return Response(
                {
                    "error": {
                        "code": "forbidden",
                        "message": "Bu işlem için yetkiniz veya organizasyon kapsamınız yok.",
                        "detail": str(hata),
                    }
                },
                status=status.HTTP_403_FORBIDDEN,
            )
        # Girdi doğrulaması yetki reddinden ÖNCE kontrol edilir (400 vs 403).
        if isinstance(hata, RoleAssignmentValidationError):
            return Response(
                {"error": {"code": "bad_request", "message": str(hata), "detail": ""}},
                status=status.HTTP_400_BAD_REQUEST,
            )
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

    def handle_exception(self, exc):  # noqa: ANN001, ANN201
        """
        Oturum yokken dönen 403'ü ayırt edilebilir kılar (`error.code`).

        Sözleşme durum kodu **değişmez** (oturum kimlik doğrulaması → 403); ancak
        `code="not_authenticated"` ile "yetki yok" (403 + `forbidden`) ayrılır, böylece
        arayüz gerçekten oturumu kapanmış kullanıcıyı girişe yönlendirebilir.
        """
        from rest_framework import exceptions as drf_exceptions  # noqa: PLC0415

        if isinstance(exc, (drf_exceptions.NotAuthenticated, drf_exceptions.PermissionDenied)) and (
            not getattr(getattr(self, "request", None), "user", None)
            or not self.request.user.is_authenticated
        ):
            return Response(
                {
                    "error": {
                        "code": "not_authenticated",
                        "message": "Oturum bulunamadı veya süresi doldu.",
                        "detail": "",
                    }
                },
                status=status.HTTP_403_FORBIDDEN,
            )
        return super().handle_exception(exc)


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


def _calisan_etiketi(employee: Employee) -> str:  # noqa: ANN001
    """
    Çalışanı seçim listelerinde gösteren kısa etiket.

    DİKKAT (regresyon): `employee.user` **bağlı olmayabilir** (`user_id` NULL'dur).
    Doğrudan `e.user.first_name` okunması, hesabı olmayan bir çalışan
    oluşturulduğunda `/test-ui/api/lookups` ucunu `AttributeError` ile tümden
    kırdı ve panel/çalışanlar sekmeleri boş kaldı. Etiket bu yüzden tek yerde ve
    güvenli sırayla üretilir: **ad-soyad → e-posta → çalışan no**.
    """
    if employee.user_id:
        ad = f"{employee.user.first_name} {employee.user.last_name}".strip()
        return str(ad or employee.user.email or employee.employee_no)
    return str(employee.employee_no)


def _calisan_ozeti(employee: Employee, izin_durumu: dict | None = None) -> dict:  # noqa: ANN001
    """Çalışan özeti — **K2/K3 alanları dahil edilmez**."""
    if izin_durumu is None:
        izin_durumu = _izin_durumu_haritasi([employee]).get(
            employee.pk, {"izinde": False, "kalan_gun": None, "isaret": "yok", "bitis": None}
        )
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
        # Unvan (position) ile birim (department) ayrı kavramlardır (M10/M12):
        # arayüzde ikisi ayrı satırda gösterilir.
        "department": employee.department.name if employee.department_id else None,
        "department_id": str(employee.department_id) if employee.department_id else None,
        "position": employee.position.name if employee.position_id else None,
        "position_id": str(employee.position_id) if employee.position_id else None,
        "status": employee.status,
        "status_label": employee.get_status_display(),
        "hire_date": employee.hire_date.isoformat() if employee.hire_date else None,
        "user_email": employee.user.email if employee.user_id else None,
        "user_name": (
            f"{employee.user.first_name} {employee.user.last_name}".strip()
            if employee.user_id
            else None
        ),
        "roles": _kullanicinin_rolleri(employee.user) if employee.user_id else [],
        "izin_durumu": izin_durumu,
        "annual": _yillik_ozet(employee),
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


def _kapsamda_mi(user, employee: Employee) -> bool:  # noqa: ANN001
    """Çalışan, aktörün organizasyon kapsamında mı? (hedef doğrulama)"""
    kapsam = user_org_scope(user)
    if kapsam.tenant_wide:
        return True
    if employee.department_id is None:
        return False
    return employee.department_id in kapsam.unit_ids


def _izin_durumu_haritasi(employees) -> dict:  # noqa: ANN001
    """
    Çalışan bazında **mevcut izin durumu** (bildirim: "izinde olanların yanında
    yeşil nokta, 7 günden az süre kalanlarda sarı").

    * `yesil` → bugün onaylı izin aralığının içinde, bitişe 7+ gün var.
    * `sari`  → izinde ve bitişe **7 günden az** kaldı.
    * `yok`   → bugün izinde değil.

    Kaynak: onaylı `leave_requests` satırları (kişisel veri içermez, yalnız tarih).
    """
    bugun = timezone.localdate()
    idler = [e.pk for e in employees]
    if not idler:
        return {}
    satirlar = LeaveRequest.objects.filter(
        deleted_at__isnull=True,
        status=LeaveRequestStatus.APPROVED,
        employee_id__in=idler,
        start_date__lte=bugun,
        end_date__gte=bugun,
    ).order_by("employee_id", "end_date").values_list("employee_id", "end_date")
    harita: dict = {}
    for employee_id, bitis in satirlar:
        # Birden fazla örtüşen izin varsa **en yakın bitiş** belirleyicidir
        # ("7 günden az kaldı" uyarısı en erken biten izne göre verilir).
        if employee_id in harita:
            continue
        kalan = (bitis - bugun).days
        harita[employee_id] = {
            "izinde": True,
            "kalan_gun": kalan,
            "isaret": "sari" if kalan < 7 else "yesil",
            "bitis": bitis.isoformat(),
        }
    return harita


def _talep_ozeti(talep, user, bakiye_dahil: bool = False) -> dict:  # noqa: ANN001
    adim = current_step(talep)
    kendi_employee_id = getattr(user_employee(user), "pk", None)
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
        "status": talep.status,  # gerçek durum kodu (kapalı küme)
        "status_label": talep.get_status_display(),  # Türkçe karşılığı (sunum)
        "reason": talep.reason,
        "submitted_at": talep.submitted_at.isoformat() if talep.submitted_at else None,
        "decided_at": talep.decided_at.isoformat() if talep.decided_at else None,
        "canceled_at": talep.canceled_at.isoformat() if talep.canceled_at else None,
        "requires_document": talep.leave_type.requires_document,
        "document_ref": str(talep.document_ref) if talep.document_ref else None,
        "current_step_no": adim.step_no if adim is not None else None,
        "kendi_talebi": kendi_employee_id is not None
        and talep.employee_id == kendi_employee_id,
        "can_approve": can_approve_step(user, adim, talep),
        "can_cancel": can_cancel_leave_request(user, talep),
        "can_view": can_view_leave_request(user, talep),
        # Onay ekranı: onaylayan, talep sahibinin güncel bakiyesini görür
        # (bildirim: "onaylayan kişi talep gönderen kullanıcının mevcut izin
        # bakiyesini onaylama ekranında görebilmeli").
        "talep_sahibi_bakiye": _yillik_ozet(talep.employee) if bakiye_dahil else None,
    }


def _kullanicinin_rolleri(user) -> list[dict]:  # noqa: ANN001
    """Kullanıcının **etkin** rol atamaları: Türkçe rol adı + okunabilir kapsam metni."""
    atamalar = (
        UserRole.objects.filter(user=user, deleted_at__isnull=True)
        .select_related("role", "scope_org_unit")
        .order_by("role__name")
    )
    return [
        _atama_ozeti(atama)
        for atama in atamalar
        if atama.role is not None and atama.role.deleted_at is None
    ]


def _atama_ozeti(atama) -> dict:  # noqa: ANN001
    """Rol ataması özeti (Türkçe etiket + kapsam metni)."""
    return {
        "id": str(atama.pk),
        "code": atama.role.code,  # teknik kod (ayrıntı alanında gösterilir)
        "name": atama.role.name,  # DB'deki Türkçe ad (backend kodu değişmez)
        "scope_type": atama.role.scope_type,
        "scope_type_label": kapsam_tipi_etiketi(atama.role.scope_type),
        "scope_org_unit_id": str(atama.scope_org_unit_id) if atama.scope_org_unit_id else None,
        # Birim kapsamı adı (UNIT/UNIT_DIRECT): "Kapsam birimi" kolonunda gösterilir.
        "scope_org_unit_name": atama.scope_org_unit.name if atama.scope_org_unit_id else None,
        "scope_text": kapsam_metni(
            scope_type=atama.role.scope_type,
            birim_adi=atama.scope_org_unit.name if atama.scope_org_unit_id else None,
        ),
        "granted_at": atama.granted_at.isoformat() if atama.granted_at else None,
        "kaldirildi": atama.deleted_at is not None,
    }


def _yillik_bakiye(employee, yil: int):  # noqa: ANN001, ANN202
    """Yıllık izin bakiye kaydı (varsa). Tek doğruluk kaynağı `hr.leave_balances`."""
    return (
        LeaveBalance.objects.filter(
            employee=employee,
            period_year=yil,
            deleted_at__isnull=True,
            leave_type__code="annual_leave",
            leave_type__deleted_at__isnull=True,
        )
        .select_related("leave_type")
        .first()
    )


def _yillik_ozet(employee, yil: int | None = None) -> dict:  # noqa: ANN001
    """
    Yıllık izin özeti (hakediş/kullanılan/bekleyen/kalan) — **backend'den**.

    Hakediş yoksa hata değil, açık bir durum döner: arayüz "Henüz yıllık izin
    hakedişi oluşturulmamış." gösterir. Türetilmiş değer (kalan) `available_days`
    **üretilen kolonundan** gelir; arayüz hesap yapmaz.
    """
    yil = yil or period_year_for()
    bos = {
        "hakedis_var": False,
        "period_year": yil,
        "leave_type": "Yıllık İzin",
        "leave_type_code": "annual_leave",
        "entitled_days": None,
        "carried_forward_days": None,
        "adjustment_days": None,
        "used_days": None,
        "reserved_days": None,
        "available_days": None,
    }
    if employee is None:
        return {**bos, "mesaj": "Bu kullanıcıya bağlı çalışan kaydı yok."}
    bakiye = _yillik_bakiye(employee, yil)
    if bakiye is None:
        return {**bos, "mesaj": "Henüz yıllık izin hakedişi oluşturulmamış."}
    return {
        "hakedis_var": True,
        "period_year": bakiye.period_year,
        "leave_type": bakiye.leave_type.name,
        "leave_type_code": bakiye.leave_type.code,
        "entitled_days": str(bakiye.entitled_days),
        "carried_forward_days": str(bakiye.carried_forward_days),
        "adjustment_days": str(bakiye.adjustment_days),
        "used_days": str(bakiye.used_days),
        "reserved_days": str(bakiye.reserved_days),
        "available_days": str(bakiye.available_days),
        "mesaj": "",
    }


def _bakiye_listesi(employee, yil: int) -> list[dict]:  # noqa: ANN001
    """Çalışanın dönem bakiyeleri (yalnız kaydı olan izin türleri)."""
    kayitlar = (
        LeaveBalance.objects.filter(employee=employee, period_year=yil, deleted_at__isnull=True)
        .select_related("leave_type")
        .order_by("leave_type__code")
    )
    return [_bakiye_ozeti(kayit) for kayit in kayitlar if kayit.leave_type_id]


def _kapsamdaki_calisan(user, employee_id) -> Employee:  # noqa: ANN001
    """Aktörün kapsamındaki çalışan; kapsam dışıysa **404** (varlık sızdırılmaz)."""
    kimlik = _uuid(employee_id, "employee_id")
    calisan = kapsamdaki_calisanlar(user).filter(pk=kimlik).first()
    if calisan is None:
        raise Http404("calisan_kapsam_disi")
    return calisan


def _izin_istisnalari(user) -> list[dict]:  # noqa: ANN001
    """Kullanıcı bazlı izin istisnaları (salt okunur görünüm)."""
    kayitlar = (
        UserPermissionOverride.objects.filter(user=user, deleted_at__isnull=True)
        .select_related("permission")
        .order_by("permission__code")
    )
    return [
        {
            "permission": kayit.permission.code,
            "permission_name": kayit.permission.name,
            "effect": kayit.effect,
            "effect_label": istisna_etiketi(kayit.effect),
            "reason": kayit.reason,
        }
        for kayit in kayitlar
    ]


def _atama_getir(assignment_id) -> UserRole:  # noqa: ANN001
    """Kiracı bağlamındaki rol ataması (RLS dışına çıkmaz; yoksa 404)."""
    kimlik = _uuid(assignment_id, "assignment_id")
    atama = UserRole.objects.filter(pk=kimlik).select_related("role", "user").first()
    if atama is None:
        raise Http404("atama_yok")
    return atama


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
        roller = _kullanicinin_rolleri(user)
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
                    "role_codes": [r["code"] for r in roller],
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
                        # Rol/yetki yönetimi yalnızca `role.manage` sahibinde görünür
                        # (karar servis katmanında; UI yalnızca gizler).
                        "roles_manage": user_has_permission(user, "role.manage"),
                        "pii_gorunur": user_has_permission(user, "employee.sensitive.read"),
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
            # Onay ekranı: talep sahibinin güncel bakiyesi de döner.
            _talep_ozeti(t, user, bakiye_dahil=True)
            for t in (
                LeaveRequest.objects.filter(
                    deleted_at__isnull=True, status=LeaveRequestStatus.PENDING
                )
                .select_related("employee__department", "leave_type", "employee")
                .order_by("start_date")[:50]
            )
            if can_view_leave_request(user, t)
        ]

        # Yaklaşan izinler: onaylanmış ve başlangıcı bugünden sonra olan talepler
        # (kapsam süzgeci aynı kuraldan geçer; çalışan yalnız kendini görür).
        bugun = timezone.localdate()
        yaklasanlar = [
            _talep_ozeti(t, user)
            for t in (
                LeaveRequest.objects.filter(
                    deleted_at__isnull=True,
                    status=LeaveRequestStatus.APPROVED,
                    # Aralığı tamamen geçmiş izinler "yaklaşan" olarak gösterilmez
                    # (bildirim); devam eden izin (başlangıcı geçmiş, bitişi bugün
                    # veya sonrası) listede kalır.
                    end_date__gte=bugun,
                )
                .select_related("employee__department", "leave_type", "employee")
                .order_by("start_date")[:20]
            )
            if can_view_leave_request(user, t)
        ][:10]

        # Karara bağlanmış talepler (onay/red): kapsam süzgeci aynı kuraldan geçer.
        karara_baglananlar = [
            _talep_ozeti(t, user)
            for t in (
                LeaveRequest.objects.filter(
                    deleted_at__isnull=True,
                    status__in=[LeaveRequestStatus.APPROVED, LeaveRequestStatus.REJECTED],
                )
                .select_related("employee__department", "leave_type", "employee")
                .order_by("-decided_at")[:20]
            )
            if can_view_leave_request(user, t)
        ][:10]

        durum_sayilari = {"pending": 0, "approved": 0, "rejected": 0, "canceled": 0}
        for talep in LeaveRequest.objects.filter(deleted_at__isnull=True).only(
            "status", "employee__department_id"
        ):
            if can_view_leave_request(user, talep):
                durum_sayilari[talep.status] = durum_sayilari.get(talep.status, 0) + 1

        return Response(
            {
                "data": {
                    "employee": _calisan_ozeti(calisan) if calisan is not None else None,
                    "period_year": yil,
                    "profile": {
                        "name": f"{user.first_name} {user.last_name}".strip() or user.email,
                        "email": user.email,
                        "tenant": user.tenant.name,
                        "position": (
                            calisan.position.name if calisan and calisan.position_id else None
                        ),
                        "department": (
                            calisan.department.name if calisan and calisan.department_id else None
                        ),
                        "employee_no": calisan.employee_no if calisan is not None else None,
                        "annual": _yillik_ozet(calisan, yil),
                    },
                    "pii_gorunur": user_has_permission(user, "employee.sensitive.read"),
                    "upcoming_requests": yaklasanlar,
                    "decided_requests": karara_baglananlar,
                    "status_counts": durum_sayilari,
                    "balances": bakiyeler,
                    "annual": _yillik_ozet(calisan, yil),
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
        # `moves_for=<id>`: yalnızca o birimin **taşınabileceği** üst birimler
        # (kendisi ve alt ağacı hariç). Kaynak: `sub_tree_department_ids` — aynı
        # OD24 alt ağaç çözümleyicisi; karar yine servis/DB guard'larında.
        tasima_hedefleri = None
        hedef_id = request.query_params.get("moves_for")
        if hedef_id:
            birim = _kapsamda_birim(request.user, hedef_id)
            yasak = sub_tree_department_ids(birim)
            tasima_hedefleri = [
                {
                    "id": str(d.id),
                    "code": d.code,
                    "name": d.name,
                    "parent_id": str(d.parent_id) if d.parent_id else None,
                }
                for d in kayitlar
                if d.pk not in yasak
            ]
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
                    "can_manage": user_has_permission(request.user, "org.department.manage"),
                    "moves_for": str(hedef_id) if hedef_id else None,
                    "move_options": tasima_hedefleri,
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


class DepartmentUpdateView(TestUiView):
    """Birimin adını/kodunu değiştirir (denetimli; yetki servis katmanında)."""

    def post(self, request, department_id: str):  # noqa: ANN001
        birim = _kapsamda_birim(request.user, department_id)
        veri = request.data
        try:
            ad = str(veri.get("name") or "").strip()
            kod = str(veri.get("code") or "").strip()
            if veri.get("name") is not None and not ad:
                raise LeaveError("Departman adı boş olamaz.")
            if veri.get("code") is not None and not kod:
                raise LeaveError("Departman kodu boş olamaz.")
            guncel = update_department(
                actor=request.user,
                department=birim,
                name=(ad or None),
                code=(kod or None),
                reason=str(veri.get("reason") or ""),
            )
        except Exception as hata:  # noqa: BLE001
            return TestUiErrors.cevap(hata)
        return Response(
            {
                "data": {
                    "id": str(guncel.pk),
                    "code": guncel.code,
                    "name": guncel.name,
                    "parent_id": str(guncel.parent_id) if guncel.parent_id else None,
                }
            }
        )


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


def _dizin_kaydi(employee: Employee) -> dict:  # noqa: ANN001
    """
    Şirket dizini kaydı — **kişisel veri içermez** (docs/ORGANIZATION.md §5):
    ad/e-posta/telefon yerine çalışan numarası, genel kullanıcı kimliği, unvan ve birim.
    Bakiye/izin özeti **dönmez**.
    """
    return {
        "id": str(employee.pk),
        "employee_no": employee.employee_no,
        "public_id": getattr(employee.user, "public_id", None) if employee.user_id else None,
        "department": employee.department.name if employee.department_id else None,
        "department_id": str(employee.department_id) if employee.department_id else None,
        "position": employee.position.name if employee.position_id else None,
        "position_id": str(employee.position_id) if employee.position_id else None,
        "status": employee.status,
        "status_label": employee.get_status_display(),
        "dizin": True,
    }


class EmployeeListView(TestUiView):
    """
    Kapsamdaki çalışanlar + oluşturma.

    * `employee.read` sahibi: kapsamındaki kayıtlar (özet + izin bilgisi).
    * Yalnız `SELF` kapsamı olan kullanıcı: **şirket dizini** (PII'siz, bakiyesiz)
      + kendi kaydı tam olarak; böylece organizasyon şemasını görebilir ama
      başkalarının izin/bakiye detaylarını göremez.
    """

    def get(self, request):  # noqa: ANN001
        kendi = user_employee(request.user)
        kapsam = user_org_scope(request.user)
        # Kapsamı yalnızca **kendi kaydı** olan kullanıcı (ör. `employee`) şirket
        # dizinini görür: PII'siz kimlik bilgisi + unvan + birim ve kendi kaydı tam.
        # Böylece organizasyon şeması görünür ama başkalarının izin/bakiye detayları
        # görünmez (docs/ORGANIZATION.md §5 "kişisel veri içermez" ilkesi).
        if not kapsam.tenant_wide and not kapsam.unit_ids:
            # Yalnız kendi kaydı (bildirim: "normal bir çalışan sadece kendini
            # görsün; diğer çalışanları ve numaralarını görmesine gerek yok").
            kayitlar = (
                [{**_calisan_ozeti(kendi), "kendi_kaydim": True}] if kendi is not None else []
            )
            return Response(
                {
                    "data": kayitlar,
                    "meta": {
                        "can_create": False,
                        "scope": _kapsam_ozeti(request.user),
                        "self_only": True,
                    },
                }
            )
        calisanlar = list(kapsamdaki_calisanlar(request.user))
        durumlar = _izin_durumu_haritasi(calisanlar)
        return Response(
            {
                "data": [
                    _calisan_ozeti(
                        e,
                        durumlar.get(
                            e.pk,
                            {"izinde": False, "kalan_gun": None, "isaret": "yok", "bitis": None},
                        ),
                    )
                    for e in calisanlar
                ],
                "meta": {
                    "can_create": user_has_permission(request.user, "employee.create"),
                    "scope": _kapsam_ozeti(request.user),
                    "self_only": False,
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
            # Hesap açma/bağlama: ya mevcut hesap seçilir ya da yenisi açılır
            # (e-posta + parola; parola arayüzde üretilir ve yanıtta dönmez).
            if veri.get("email") and veri.get("password"):
                if not user_has_permission(request.user, "user.manage"):
                    raise TestUiAuthorizationError(
                        "Hesap açmak için 'user.manage' izni gerekir."
                    )
                from django.contrib.auth import get_user_model  # noqa: PLC0415
                from django.contrib.auth.password_validation import (  # noqa: PLC0415
                    validate_password,
                )
                from django.core.exceptions import ValidationError  # noqa: PLC0415

                eposta = str(veri["email"]).strip().lower()
                try:
                    validate_password(str(veri["password"]))
                except ValidationError as hata:
                    raise LeaveError(
                        "Parola kuralları: " + " ".join(hata.messages)
                    ) from hata
                user_modelu = get_user_model()
                if user_modelu.objects.filter(
                    tenant=request.user.tenant_id, email=eposta
                ).exists():
                    raise LeaveError("Bu e-posta ile kayıtlı bir kullanıcı zaten var.")
                user = user_modelu.objects.create_user(
                    tenant=request.user.tenant,
                    email=eposta,
                    password=str(veri["password"]),
                    first_name=str(veri.get("first_name") or "").strip(),
                    last_name=str(veri.get("last_name") or "").strip(),
                )
                from apps.audit.services import log_action  # noqa: PLC0415

                log_action(
                    tenant_id=request.user.tenant_id,
                    action="user.create",
                    user=request.user,
                    table_name="users",
                    record_id=user.pk,
                    changes={"email": eposta},  # parola ASLA yazılmaz
                )
            user = user if "user" in locals() else None
            if user is None and veri.get("user_id"):
                from django.contrib.auth import get_user_model  # noqa: PLC0415

                user = get_user_model().objects.filter(pk=_uuid(veri["user_id"], "user_id")).first()
                if user is None or str(user.tenant_id) != str(request.user.tenant_id):
                    raise LeaveError(
                        "Seçilen kullanıcı hesabı bulunamadı (veya bu kiracıya ait değil)."
                    )
            elif veri.get("user_email"):
                from django.contrib.auth import get_user_model  # noqa: PLC0415

                user = get_user_model().objects.filter(
                    email=str(veri["user_email"]).strip(), tenant=request.user.tenant_id
                ).first()
                if user is None:
                    raise LeaveError(
                        "Bu e-posta ile kayıtlı kullanıcı yok. Listeden seçin "
                        "(hesap oluşturma kapsam dışıdır)."
                    )
            # Birincil yönetici (bildirim: "her çalışan girişinde bağlı olduğu
            # yöneticinin de seçilmesi gerekmeli"): yetki ve kapsam **yazmadan
            # önce** doğrulanır ki kayıt yarım kalmasın.
            yonetici = None
            if veri.get("manager_employee_id"):
                if not user_has_permission(request.user, "employee.update"):
                    raise TestUiAuthorizationError(
                        "Yönetici atamak için 'employee.update' izni gerekir."
                    )
                yonetici = Employee.objects.filter(
                    pk=_uuid(veri["manager_employee_id"], "manager_employee_id"),
                    tenant_id=request.user.tenant_id,
                    deleted_at__isnull=True,
                ).first()
                if yonetici is None:
                    raise LeaveError("Seçilen yönetici bulunamadı (veya bu kiracıya ait değil).")
                if not _kapsamda_mi(request.user, yonetici):
                    raise TestUiAuthorizationError(
                        "Seçilen yönetici organizasyon kapsamınız dışında."
                    )
            calisan = create_employee(
                actor=request.user,
                tenant=request.user.tenant,
                department=department,
                # Numara **istemciden alınmaz**: boş bırakılır ve sunucu üretir
                # (`next_employee_no`, atomik sequence). Gönderilirse yalnızca
                # açıkça verilmiş bir sicil numarası olarak kabul edilir.
                employee_no=str(veri.get("employee_no") or "").strip() or None,
                hire_date=_tarih(veri.get("hire_date"), "hire_date"),
                position=position,
                user=user,
            )
            if yonetici is not None:
                if yonetici.pk == calisan.pk:
                    raise LeaveError("Çalışan kendi yöneticisi olamaz.")
                set_primary_manager(
                    actor=request.user,
                    employee=calisan,
                    manager_employee=yonetici,
                    reason="Çalışan oluşturulurken atandı",
                )
            # Kişisel veriler (yalnız `employee.sensitive.read` sahibi yazabilir).
            # Kayıt formu K2 alanlarının tamamını + K3 (kan grubu) alanını toplar;
            # yetki kapısı `_pii_yaz` içindedir (izin + kapsam), burada tekrarlanmaz.
            pii = {a: veri[a] for a in (*PII_ALANLARI, *K3_ALANLARI) if a in veri}
            if pii:
                _pii_yaz(request.user, calisan, pii)
                for alan in ("first_name", "last_name"):
                    if alan in pii and calisan.user_id and not getattr(calisan.user, alan):
                        setattr(calisan.user, alan, pii[alan])
                if calisan.user_id:
                    calisan.user.save(update_fields=["first_name", "last_name", "updated_at"])
        except (
            LeaveError,
            OrganizationError,
            LeaveAuthorizationError,
            TestUiAuthorizationError,
        ) as hata:
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


def _kapsamda_birim(user, department_id) -> Department:  # noqa: ANN001
    """Kiracı içindeki birim (taşıma hedefi hesabı için); yoksa 404."""
    kimlik = _uuid(department_id, "department_id")
    birim = Department.objects.filter(pk=kimlik, deleted_at__isnull=True).first()
    if birim is None:
        raise Http404("birim_yok")
    return birim


class EmployeeBalanceView(TestUiView):
    """Çalışanın dönem bakiyeleri (türetilen değerler backend'den)."""

    def get(self, request, employee_id: str):  # noqa: ANN001
        from apps.leave.services import can_read_balances  # noqa: PLC0415

        # Kapsam dışı çalışan **görünmez** (404): varlık sızdırılmaz.
        calisan = _kapsamdaki_calisan(request.user, employee_id)
        if not can_read_balances(request.user, calisan):
            return TestUiErrors.cevap(LeaveAuthorizationError("Bakiye görme yetkisi/kapsamı yok."))
        yil = period_year_for()
        bakiyeler = _bakiye_listesi(calisan, yil)
        return Response(
            {
                "data": bakiyeler,
                "meta": {
                    "employee_no": calisan.employee_no,
                    "annual": _yillik_ozet(calisan, yil),
                    "mesaj": "" if bakiyeler else "Bu dönem için bakiye kaydı yok.",
                },
            }
        )


def _atanabilir_roller(user) -> list[Role]:  # noqa: ANN001
    """
    Kiracının **kendi** atanabilir rolleri.

    Platform rolleri `hr.roles` içinde yaşamaz (OD3); yine de savunma amaçlı olarak
    `platform.*` izni içeren roller listelenmez.
    """
    platform_izinli = set(
        RolePermission.objects.filter(permission__code__startswith="platform.").values_list(
            "role_id", flat=True
        )
    )
    return [
        rol
        for rol in Role.objects.filter(
            tenant=user.tenant_id, deleted_at__isnull=True, is_assignable=True
        ).order_by("name")
        if rol.pk not in platform_izinli
    ]


class RoleListView(TestUiView):
    """
    Atanabilir roller + aktörün atayıp atayamayacağı (karar servis katmanında).

    **`role.read` zorunludur**: rol kataloğu (izin/atama sayılarıyla) yalnızca bu
    izni taşıyan rollere görünür (docs/AUTHORIZATION.md §6.2). İzinsiz kullanıcı
    `403` alır; arayüz bu durumda listeyi hiç istemez.
    """

    def get(self, request):  # noqa: ANN001
        if not user_has_permission(request.user, "role.read"):
            return TestUiErrors.cevap(
                RoleAssignmentError("Rolleri görüntülemek için 'role.read' izni gerekir.")
            )
        roller = []
        for rol in _atanabilir_roller(request.user):
            izin_var, gerekce = can_assign_role(request.user, rol)
            roller.append(
                {
                    "id": str(rol.pk),
                    "code": rol.code,
                    "name": rol.name,
                    "scope_type": rol.scope_type,
                    "scope_type_label": kapsam_tipi_etiketi(rol.scope_type),
                    "kapsam_gerekli": rol.scope_type in ORG_SCOPED_ROLE_SCOPE_TYPES,
                    "atanabilir": izin_var,
                    "gerekce": gerekce,
                    "role_type": rol.role_type,
                    "can_edit_meta": not UserRole.objects.filter(
                        user=request.user, role=rol, deleted_at__isnull=True
                    ).exists(),
                    "permission_count": RolePermission.objects.filter(role=rol).count(),
                    "user_count": UserRole.objects.filter(
                        role=rol, deleted_at__isnull=True
                    ).count(),
                }
            )
        return Response(
            {
                "data": roller,
                "meta": {
                    "can_manage": user_has_permission(request.user, "role.manage"),
                    "scope_types": [
                        {"value": "TENANT", "label": "Tüm kiracı"},
                        {"value": "UNIT", "label": "Seçili birim + alt birimleri"},
                        {"value": "UNIT_DIRECT", "label": "Yalnızca seçili birim"},
                        {"value": "SELF", "label": "Yalnızca kendi kaydı"},
                    ],
                },
            }
        )


def _kullanici_secenekleri(actor) -> list[dict]:  # noqa: ANN001
    """Çalışana bağlanabilecek kullanıcılar (aynı kiracı) + mevcut bağ bilgisi."""
    from django.contrib.auth import get_user_model  # noqa: PLC0415

    baglantilar: dict[str, str] = {}
    for calisan in Employee.objects.filter(
        deleted_at__isnull=True, user__isnull=False
    ).select_related("user"):
        baglantilar[str(calisan.user_id)] = calisan.employee_no
    # `User` modelinde `is_active` yoktur (proje standardı: `status`).
    kullanicilar = get_user_model().objects.filter(
        tenant=actor.tenant_id, status=UserStatus.ACTIVE
    ).order_by("email")
    return [
        {
            "id": str(k.pk),
            "email": k.email,
            "name": f"{k.first_name} {k.last_name}".strip(),
            "bagli_employee_no": baglantilar.get(str(k.pk)),
        }
        for k in kullanicilar
    ]


def _izin_turu_ozeti(tur) -> dict:  # noqa: ANN001
    """İzin türü özeti (yönetim kartı + talep formu için)."""
    return {
        "id": str(tur.pk),
        "code": tur.code,
        "name": tur.name,
        "category": tur.category,
        "category_label": tur.get_category_display(),
        "is_paid": tur.is_paid,
        "uses_balance": tur.uses_balance,
        "requires_approval": tur.requires_approval,
        "requires_document": tur.requires_document,
        "is_system": tur.is_system,
        "status": tur.status,
        "status_label": tur.get_status_display(),
        "aktif": tur.status == RecordStatus.ACTIVE and tur.deleted_at is None,
        "min_advance_notice_days": tur.min_advance_notice_days,
        "max_consecutive_days": (
            str(tur.max_consecutive_days) if tur.max_consecutive_days is not None else None
        ),
    }


class PositionCreateView(TestUiView):
    """Unvan (pozisyon) tanımı oluşturur (`org.position.manage`)."""

    def post(self, request):  # noqa: ANN001
        try:
            pozisyon = create_position(
                actor=request.user,
                tenant=request.user.tenant,
                code=str(request.data.get("code", "")).strip(),
                name=str(request.data.get("name", "")).strip(),
                level=str(request.data.get("level", "")).strip(),
            )
        except Exception as hata:  # noqa: BLE001
            return TestUiErrors.cevap(hata)
        return Response(
            {"data": {"id": str(pozisyon.pk), "code": pozisyon.code, "name": pozisyon.name}},
            status=status.HTTP_201_CREATED,
        )


class LeaveTypeListView(TestUiView):
    """İzin türleri: listeleme ve kiracıya özel tür oluşturma (`leave.type.manage`)."""

    def get(self, request):  # noqa: ANN001
        turler = LeaveType.objects.filter(deleted_at__isnull=True).order_by("code")
        return Response(
            {
                "data": [_izin_turu_ozeti(t) for t in turler],
                "meta": {
                    "can_manage": can_manage_leave_types(request.user),
                    "categories": [
                        {"value": k, "label": e} for k, e in LeaveCategory.choices
                    ],
                },
            }
        )

    def post(self, request):  # noqa: ANN001
        veri = request.data
        try:
            tur = create_leave_type(
                actor=request.user,
                tenant=request.user.tenant,
                code=str(veri.get("code", "")),
                name=str(veri.get("name", "")),
                category=str(veri.get("category") or LeaveCategory.OTHER),
                is_paid=bool(veri.get("is_paid", True)),
                uses_balance=bool(veri.get("uses_balance", False)),
                requires_approval=bool(veri.get("requires_approval", True)),
                requires_document=bool(veri.get("requires_document", False)),
                min_advance_notice_days=int(veri.get("min_advance_notice_days") or 0),
            )
        except (LeaveError, LeaveAuthorizationError) as hata:
            return TestUiErrors.cevap(hata)
        return Response({"data": _izin_turu_ozeti(tur)}, status=status.HTTP_201_CREATED)


def _politika_ozeti(policy) -> dict:  # noqa: ANN001
    """İzin politikası özeti (sunum; karar vermez)."""
    return {
        "id": str(policy.pk),
        "leave_type": policy.leave_type.name,
        "leave_type_code": policy.leave_type.code,
        "accrual_method": policy.accrual_method,
        "fixed_days": str(policy.fixed_days) if policy.fixed_days is not None else None,
        # "Bakiye yetersizse talep açılabilir" anahtarı (bildirim: bakiye 0 olsa
        # bile talep oluşturulabilsin → bu bayrak True olmalı).
        "allow_negative_balance": policy.allow_negative_balance,
        "allow_refund_after_approval": policy.allow_refund_after_approval,
        "status": policy.status,
        "is_active": policy.status == "active",
        "rules_count": policy.rules.filter(deleted_at__isnull=True).count(),
    }


class LeavePolicyListView(TestUiView):
    """
    İzin politikaları — `leave.type.manage` sahibi görür ve düzenler.

    Yetkisi olmayan kullanıcı **403** alır; arayüz kartı hiç göstermez.
    """

    def get(self, request):  # noqa: ANN001
        if not user_has_permission(request.user, "leave.type.manage"):
            raise Http404("politika_gorunmez")
        politikalar = LeavePolicy.objects.filter(
            deleted_at__isnull=True
        ).select_related("leave_type").order_by("leave_type__code")
        return Response({"data": [_politika_ozeti(p) for p in politikalar]})


class LeavePolicyUpdateView(TestUiView):
    """Politika kurallarını günceller (kararı `update_leave_policy` verir)."""

    def post(self, request, policy_id: str):  # noqa: ANN001
        politika = LeavePolicy.objects.filter(
            pk=_uuid(policy_id, "policy_id"), deleted_at__isnull=True
        ).first()
        if politika is None:
            raise Http404("politika_yok")
        veri = request.data
        try:
            update_leave_policy(
                actor=request.user,
                policy=politika,
                fixed_days=_sayi(veri.get("fixed_days")) if "fixed_days" in veri else None,
                allow_negative_balance=(
                    bool(veri.get("allow_negative_balance"))
                    if "allow_negative_balance" in veri
                    else None
                ),
                allow_refund_after_approval=(
                    bool(veri.get("allow_refund_after_approval"))
                    if "allow_refund_after_approval" in veri
                    else None
                ),
                reason=str(veri.get("reason") or ""),
            )
        except Exception as hata:  # noqa: BLE001
            return TestUiErrors.cevap(hata)
        return Response({"data": _politika_ozeti(politika)})


class LeaveTypeDeactivateView(TestUiView):
    """İzin türünü devre dışı bırakır (silmez; mevcut talepler korunur)."""

    def post(self, request, leave_type_id: str):  # noqa: ANN001
        tur = LeaveType.objects.filter(
            pk=_uuid(leave_type_id, "leave_type_id"), deleted_at__isnull=True
        ).first()
        if tur is None:
            raise Http404("izin_turu_yok")
        try:
            deactivate_leave_type(
                actor=request.user, leave_type=tur, reason=str(request.data.get("reason") or "")
            )
        except Exception as hata:  # noqa: BLE001
            return TestUiErrors.cevap(hata)
        return Response({"data": _izin_turu_ozeti(tur)})


class RoleCreateView(TestUiView):
    """Kiracıya özel rol oluşturur (`role.manage`)."""

    def post(self, request):  # noqa: ANN001
        try:
            rol = create_custom_role(
                actor=request.user,
                tenant=request.user.tenant,
                code=str(request.data.get("code") or ""),
                name=str(request.data.get("name") or ""),
                scope_type=str(request.data.get("scope_type") or "TENANT"),
                reason=str(request.data.get("reason") or ""),
            )
        except Exception as hata:  # noqa: BLE001
            return TestUiErrors.cevap(hata)
        return Response(
            {
                "data": {
                    "id": str(rol.pk),
                    "code": rol.code,
                    "name": rol.name,
                    "scope_type": rol.scope_type,
                    "scope_type_label": kapsam_tipi_etiketi(rol.scope_type),
                }
            },
            status=status.HTTP_201_CREATED,
        )


class RoleUpdateView(TestUiView):
    """Rolün adını/kapsam tipini değiştirir (`role.manage`; kapsam için atama guard'ı)."""

    def post(self, request, role_id: str):  # noqa: ANN001
        rol = Role.objects.filter(
            pk=_uuid(role_id, "role_id"), tenant=request.user.tenant_id, deleted_at__isnull=True
        ).first()
        if rol is None:
            raise Http404("rol_yok")
        try:
            update_role_meta(
                actor=request.user,
                role=rol,
                name=(str(request.data["name"]) if request.data.get("name") else None),
                scope_type=(
                    str(request.data["scope_type"]) if request.data.get("scope_type") else None
                ),
                reason=str(request.data.get("reason") or ""),
            )
        except Exception as hata:  # noqa: BLE001
            return TestUiErrors.cevap(hata)
        return Response(
            {
                "data": {
                    "id": str(rol.pk),
                    "code": rol.code,
                    "name": rol.name,
                    "scope_type": rol.scope_type,
                    "scope_type_label": kapsam_tipi_etiketi(rol.scope_type),
                }
            }
        )


class RolePermissionView(TestUiView):
    """
    Bir rolün izin kümesini okur/yazar (`role.manage`; kiracıda şirket yöneticisi).

    Okuma yanıtı izinleri **modül bazında gruplar** ve mevcut durumu işaretler;
    platform izinleri listelenmez (OD3).
    """

    def initial(self, request, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        super().initial(request, *args, **kwargs)

    def _rol(self, request, role_id: str):  # noqa: ANN001
        if not user_has_permission(request.user, "role.read"):
            raise RoleAssignmentError("Rol yetkilerini görüntülemek için 'role.read' gerekir.")
        rol = Role.objects.filter(
            pk=_uuid(role_id, "role_id"), tenant=request.user.tenant_id, deleted_at__isnull=True
        ).first()
        if rol is None:
            raise Http404("rol_yok")
        return rol

    def get(self, request, role_id: str):  # noqa: ANN001
        try:
            rol = self._rol(request, role_id)
        except RoleAssignmentError as hata:
            return TestUiErrors.cevap(hata)
        secili = set(
            RolePermission.objects.filter(role=rol).values_list("permission__code", flat=True)
        )
        aktor_izinleri = effective_permission_codes(request.user)
        moduller: dict[str, list[dict]] = {}
        for izin in Permission.objects.exclude(code__startswith="platform.").order_by(
            "module", "code"
        ):
            moduller.setdefault(izin.module or "diger", []).append(
                {
                    "code": izin.code,
                    "name": izin.name,
                    "secili": izin.code in secili,
                    "aktor_sahip": izin.code in aktor_izinleri,
                }
            )
        return Response(
            {
                "data": {
                    "role": {
                        "id": str(rol.pk),
                        "code": rol.code,
                        "name": rol.name,
                        "scope_type": rol.scope_type,
                        "scope_type_label": kapsam_tipi_etiketi(rol.scope_type),
                        "role_type": rol.role_type,
                    },
                    "scope_types": [
                        {"value": k, "label": v}
                        for k, v in (
                            ("TENANT", "Tüm kiracı"),
                            ("UNIT", "Seçili birim + alt birimleri"),
                            ("UNIT_DIRECT", "Yalnızca seçili birim"),
                            ("SELF", "Yalnızca kendi kaydı"),
                        )
                    ],
                    "modules": [
                        {
                            "module": m,
                            "module_label": modul_etiketi(m),
                            "module_aciklama": MODUL_ACIKLAMALARI.get(m, ""),
                            "permissions": izinler,
                        }
                        for m, izinler in sorted(moduller.items())
                    ],
                    "selected_count": len(secili),
                    "total_count": Permission.objects.exclude(
                        code__startswith="platform."
                    ).count(),
                    "can_edit": user_has_permission(request.user, "role.manage")
                    and not UserRole.objects.filter(
                        user=request.user, role=rol, deleted_at__isnull=True
                    ).exists(),
                }
            }
        )

    def post(self, request, role_id: str):  # noqa: ANN001
        try:
            rol = self._rol(request, role_id)
        except RoleAssignmentError as hata:
            return TestUiErrors.cevap(hata)
        kodlar = request.data.get("permission_codes")
        if not isinstance(kodlar, list):
            return TestUiErrors.cevap(
                LeaveError("'permission_codes' bir liste olmalıdır (izin kodları).")
            )
        try:
            update_role_permissions(
                actor=request.user,
                role=rol,
                permission_codes=kodlar,
                reason=str(request.data.get("reason") or ""),
            )
        except Exception as hata:  # noqa: BLE001
            return TestUiErrors.cevap(hata)
        return Response({"data": self.get(request, role_id).data})


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
                    "users": _kullanici_secenekleri(request.user),
                    # Kan grubu: K3 (sağlık) — kapalı küme; seçenekler tek kaynaktan
                    # (model `BloodType`) gelir, etiketler arayüzde gösterilir.
                    "blood_types": [
                        {"value": kod, "label": etiket} for kod, etiket in KAN_GRUBU_SECENEKLERI
                    ],
                    # Çalışan oluştururken "Yönetici" seçimi için: yalnız
                    # kapsamdaki çalışanlar (ad/e-posta değil, kimlik + no).
                    "employees": [
                        {
                            "id": str(e.pk),
                            "employee_no": e.employee_no,
                            "label": _calisan_etiketi(e),
                            "department": e.department.name if e.department_id else None,
                        }
                        for e in kapsamdaki_calisanlar(request.user)
                    ]
                    if user_has_permission(request.user, "employee.update")
                    else [],
                    "categories": [{"value": k, "label": e} for k, e in LeaveCategory.choices],
                    "leave_type_presets": [
                        {
                            "ad": "Yıllık izin",
                            "code": "yillik_izin",
                            "category": "annual",
                            "is_paid": True,
                            "uses_balance": True,
                            "requires_approval": True,
                            "requires_document": False,
                        },
                        {
                            "ad": "Hastalık / rapor",
                            "code": "hastalik_izni",
                            "category": "sick",
                            "is_paid": True,
                            "uses_balance": False,
                            "requires_approval": True,
                            "requires_document": True,
                        },
                        {
                            "ad": "Ücretsiz izin",
                            "code": "ucretsiz_izin",
                            "category": "unpaid",
                            "is_paid": False,
                            "uses_balance": False,
                            "requires_approval": True,
                            "requires_document": False,
                        },
                        {
                            "ad": "Doğum izni",
                            "code": "dogum_izni",
                            "category": "maternity",
                            "is_paid": True,
                            "uses_balance": False,
                            "requires_approval": True,
                            "requires_document": True,
                        },
                    ],
                    "recent_days": 90,
                },
                "meta": {
                    "can_manage_positions": user_has_permission(
                        request.user, "org.position.manage"
                    ),
                    "can_manage_leave_types": can_manage_leave_types(request.user),
                    "can_manage_policy": user_has_permission(request.user, "leave.type.manage"),
                    "can_create_users": user_has_permission(request.user, "user.manage"),
                    "can_see_pii": user_has_permission(request.user, "employee.sensitive.read"),
                },
            }
        )


#: Kişisel veri alanları (K2) — arayüz ve API aynı listeyi kullanır.
PII_ALANLARI = (
    "first_name",
    "last_name",
    "birth_date",
    "address",
    "personal_email",
    "personal_phone",
    "emergency_contact_phone",
    "emergency_contact_relation",
    "citizenship",
    "work_permit_type",
    "work_permit_valid_until",
    "national_id",
    "iban",
    "bank_name",
)


K3_ALANLARI = ("blood_type",)
"""Özel nitelikli (sağlık) alanlar — **şifreli** kolonlar, kapalı kümeyle doğrulanır."""

KAN_GRUBU_SECENEKLERI: tuple[tuple[str, str], ...] = tuple(
    (str(kod), str(etiket)) for kod, etiket in BloodType.choices
)
KAN_GRUBU_KAPALI_KUME: set[str] = {kod for kod, _ in KAN_GRUBU_SECENEKLERI}
KAN_GRUBU_ETIKET: dict[str, str] = {kod: etiket for kod, etiket in KAN_GRUBU_SECENEKLERI}


def _kan_grubu_etiket(kod: str | None) -> str | None:
    """Kan grubu kodunu okunabilir etikete çevirir (ör. `0+` → `0 Rh+`)."""
    if not kod:
        return None
    return KAN_GRUBU_ETIKET.get(kod, kod)


def _sifreli_oku(kayit, alan: str):  # noqa: ANN001, ANN202
    """
    Şifreli (K3) alanı okur; anahtar/veri sorunu varsa `None` döner.

    Neden yutuluyor: anahtar değişmiş/bozuk bir satır yüzünden **tüm** kişisel veri
    yanıtının 500'e düşmesi kabul edilemez; okunamayan alan "bilinmiyor" (`None`)
    döner. Yazma yolu etkilenmez: şifreleme sırasında hata oluşursa istek hata
    verir (fail-closed).
    """
    try:
        return getattr(kayit, alan)
    except SecretDecryptionError:
        return None


def _pii_ozeti(kayit) -> dict:  # noqa: ANN001
    """Kişisel veri kaydı (yalnız `employee.sensitive.read` sahibine döner)."""
    kan = _sifreli_oku(kayit, "blood_type")
    return {
        "first_name": kayit.first_name,
        "last_name": kayit.last_name,
        "birth_date": kayit.birth_date.isoformat() if kayit.birth_date else None,
        "address": kayit.address,
        "personal_email": kayit.personal_email,
        "personal_phone": kayit.personal_phone,
        "emergency_contact_phone": kayit.emergency_contact_phone,
        "emergency_contact_relation": kayit.emergency_contact_relation,
        "citizenship": kayit.citizenship,
        "work_permit_type": kayit.work_permit_type,
        "work_permit_valid_until": (
            kayit.work_permit_valid_until.isoformat() if kayit.work_permit_valid_until else None
        ),
        "national_id": kayit.national_id,
        "iban": kayit.iban,
        "bank_name": kayit.bank_name,
        # K3 (şifreli): kod + okunabilir etiket birlikte döner.
        "blood_type": kan,
        "blood_type_label": _kan_grubu_etiket(kan),
    }


def _pii_guardlari(user, employee) -> None:  # noqa: ANN001
    """Kişisel veri erişimi: tablo kapsamı izni (OD5 §5.7) + kapsam."""
    if not user_has_permission(user, "employee.sensitive.read"):
        raise TestUiAuthorizationError(
            "Kişisel verileri görüntülemek için 'employee.sensitive.read' izni gerekir."
        )


def _pii_yaz(user, calisan, veri) -> None:  # noqa: ANN001
    """Kişisel veriyi yazar/günceller; doğrulama ve denetim burada yapılır."""
    _pii_guardlari(user, calisan)
    from apps.organization.models import EmployeePersonalData  # noqa: PLC0415

    kayit = EmployeePersonalData.objects.filter(employee=calisan).first()
    yeni = kayit is None
    if kayit is None:
        kayit = EmployeePersonalData(employee=calisan, tenant=calisan.tenant)
    for alan in PII_ALANLARI:
        if alan not in veri:
            continue
        deger = veri.get(alan)
        if alan in {"birth_date", "work_permit_valid_until"}:
            setattr(kayit, alan, _tarih(deger, alan) if deger else None)
        else:
            setattr(kayit, alan, ("" if deger is None else str(deger)).strip())
    # K3 (özel nitelikli — şifreli): yalnız kapalı kümeden değer kabul edilir.
    for alan in K3_ALANLARI:
        if alan not in veri:
            continue
        ham = veri.get(alan)
        kod = ("" if ham is None else str(ham)).strip()
        if not kod:
            setattr(kayit, alan, None)  # boş = bilinmiyor (şifreli kolonda NULL)
        elif alan == "blood_type":
            if kod not in KAN_GRUBU_KAPALI_KUME:
                raise LeaveError(
                    "Kan grubu geçersiz. Kapalı kümeden seçin: "
                    + ", ".join(sorted(KAN_GRUBU_KAPALI_KUME, key=len))
                    + "."
                )
            setattr(kayit, alan, kod)
        else:  # yeni K3 alanı eklenirse kendi doğrulaması yazılmalı (fail-closed)
            raise LeaveError(f"'{alan}' alanı bu arayüzden yazılamaz.")
    if not (kayit.first_name or "").strip() or not (kayit.last_name or "").strip():
        raise LeaveError("Kişisel veri için ad ve soyad zorunludur.")
    uyruk = (kayit.citizenship or "").strip().upper()
    if uyruk and uyruk != "TR" and not (kayit.work_permit_type or "").strip():
        raise LeaveError(
            "Vatandaşlık Türkiye dışında ise oturma/çalışma izni bilgisi zorunludur."
        )
    kayit.save()
    # Denetim: kişisel veri DEĞERİ yazılmaz, yalnızca hangi alanların değiştiği bilgisi.
    # K3 (sağlık) alanlar ayrı anahtarla yazılır ki denetimde sınıfı görünür olsun.
    from apps.audit.services import log_action  # noqa: PLC0415

    log_action(
        tenant_id=calisan.tenant_id,
        action="employee.personal_data.create" if yeni else "employee.personal_data.update",
        user=user,
        table_name="employee_personal_data",
        record_id=calisan.pk,
        changes={
            "alanlar": sorted({a for a in PII_ALANLARI if a in veri}),
            "k3_alanlar": sorted({a for a in K3_ALANLARI if a in veri}),
        },
    )


class EmployeePersonalView(TestUiView):
    """
    Kişisel veri (K2) okuma/yazma — **yalnızca** `employee.sensitive.read` sahibi
    (şirket yöneticisi ve izin verdiği kişiler). Organizasyon kapsamı tek başına
    bu tabloya erişim vermez (OD5 §5.7).
    """

    def get(self, request, employee_id: str):  # noqa: ANN001
        try:
            calisan = _kapsamdaki_calisan(request.user, employee_id)
            _pii_guardlari(request.user, calisan)
        except TestUiAuthorizationError as hata:
            return TestUiErrors.cevap(hata)
        kayit = getattr(calisan, "personal_data", None)
        return Response(
            {
                "data": _pii_ozeti(kayit) if kayit is not None else None,
                "meta": {
                    "employee_no": calisan.employee_no,
                    "var": kayit is not None,
                    "mesaj": "" if kayit is not None else "Kişisel veri kaydı henüz yok.",
                },
            }
        )

    def post(self, request, employee_id: str):  # noqa: ANN001
        try:
            calisan = _kapsamdaki_calisan(request.user, employee_id)
            _pii_yaz(request.user, calisan, request.data)
        except (TestUiAuthorizationError, LeaveError, OrganizationError) as hata:
            return TestUiErrors.cevap(hata)
        return Response({"data": _pii_ozeti(calisan.personal_data)})


class UserCreateView(TestUiView):
    """Giriş hesabı açar (`user.manage`). Parola **yanıtta dönmez**, denetime yazılmaz."""

    def post(self, request):  # noqa: ANN001
        if not user_has_permission(request.user, "user.manage"):
            return TestUiErrors.cevap(
                TestUiAuthorizationError("Hesap açmak için 'user.manage' izni gerekir.")
            )
        from django.contrib.auth import get_user_model  # noqa: PLC0415
        from django.contrib.auth.password_validation import validate_password  # noqa: PLC0415
        from django.core.exceptions import ValidationError  # noqa: PLC0415

        eposta = str(request.data.get("email") or "").strip().lower()
        parola = str(request.data.get("password") or "")
        ad = str(request.data.get("first_name") or "").strip()
        soyad = str(request.data.get("last_name") or "").strip()
        if not eposta or "@" not in eposta:
            return TestUiErrors.cevap(LeaveError("Geçerli bir e-posta adresi girin."))
        if not parola:
            return TestUiErrors.cevap(LeaveError("Parola zorunludur."))
        try:
            validate_password(parola)
        except ValidationError as hata:
            return TestUiErrors.cevap(LeaveError("Parola kuralları: " + " ".join(hata.messages)))
        user_modelu = get_user_model()
        if user_modelu.objects.filter(tenant=request.user.tenant_id, email=eposta).exists():
            return TestUiErrors.cevap(
                LeaveError("Bu e-posta ile bir kullanıcı zaten var (kiracı içinde benzersiz).")
            )
        try:
            kullanici = user_modelu.objects.create_user(
                tenant=request.user.tenant,
                email=eposta,
                password=parola,
                first_name=ad,
                last_name=soyad,
            )
        except Exception as hata:  # noqa: BLE001
            return TestUiErrors.cevap(LeaveError(f"Hesap açılamadı: {hata}"))
        from apps.audit.services import log_action  # noqa: PLC0415

        log_action(
            tenant_id=request.user.tenant_id,
            action="user.create",
            user=request.user,
            table_name="users",
            record_id=kullanici.pk,
            changes={"email": eposta},  # parola ASLA yazılmaz
        )
        return Response(
            {
                "data": {
                    "id": str(kullanici.pk),
                    "email": kullanici.email,
                    "name": f"{kullanici.first_name} {kullanici.last_name}".strip(),
                }
            },
            status=status.HTTP_201_CREATED,
        )


class EmployeeDetailView(TestUiView):
    """
    Çalışan detayı: unvan/birim/yönetici + yıllık izin özeti.

    "Rol ve Yetkiler" bölümü (roller, kapsamlar, izin istisnaları, etkin izin
    sayısı) **yalnızca `role.manage` sahibine** döner: aksi halde başka bir
    kullanıcının yetki kümesi sızardı.
    """

    def get(self, request, employee_id: str):  # noqa: ANN001
        try:
            calisan = _kapsamdaki_calisan(request.user, employee_id)
        except Http404:
            # Kapsam dışı: kayıt **yokmuş gibi** 404 (bildirim: çalışan
            # başkalarını ve numaralarını görmesin; "yetkiniz yok" bilgisi de
            # gösterilmez).
            raise
        yil = period_year_for()
        ozet = _calisan_ozeti(calisan)
        detay = {
            **ozet,
            "annual": _yillik_ozet(calisan, yil),
            "balances": (
                _bakiye_listesi(calisan, yil)
                if user_has_permission(request.user, "leave.balance.read")
                else []
            ),
            "yetki_gorunur": user_has_permission(request.user, "role.manage"),
            # Her yanıtta bulunur (yetki bölümü kapalı olsa bile): arayüz kararlı bir
            # şekle göre çalışır.
            "is_self": calisan.user_id is not None
            and str(calisan.user_id) == str(request.user.pk),
            # Şekil sözleşmesi (regresyon): kullanıcı hesabı **bağlı olmayan**
            # çalışanda da bu alanlar boş liste olarak döner. Eksik bırakıldığında
            # arayüz `yetki_gorunur` true iken `assignable_roles.map` çağırıp
            # çöküyordu (detay ekranı hiç açılmıyordu).
            "revoked_assignments": [],
            "permission_overrides": [],
            "effective_permissions": [],
            "assignable_roles": [],
        }
        if detay["yetki_gorunur"] and calisan.user_id:
            atamalar = (
                UserRole.objects.filter(user=calisan.user, deleted_at__isnull=True)
                .select_related("role", "scope_org_unit")
                .order_by("role__name")
            )
            kaldirilanlar = (
                UserRole.objects.filter(user=calisan.user, deleted_at__isnull=False)
                .select_related("role", "scope_org_unit")
                .order_by("-deleted_at")[:20]
            )
            detay["roles"] = [_atama_ozeti(a) for a in atamalar]
            detay["revoked_assignments"] = [
                {
                    **_atama_ozeti(a),
                    "deleted_at": a.deleted_at.isoformat() if a.deleted_at else None,
                }
                for a in kaldirilanlar
            ]
            detay["permission_overrides"] = _izin_istisnalari(calisan.user)
            detay["effective_permissions"] = sorted(effective_permission_codes(calisan.user))
            detay["assignable_roles"] = [
                {
                    "id": str(rol.pk),
                    "code": rol.code,
                    "name": rol.name,
                    "scope_type": rol.scope_type,
                    "scope_type_label": kapsam_tipi_etiketi(rol.scope_type),
                    "kapsam_gerekli": rol.scope_type in ORG_SCOPED_ROLE_SCOPE_TYPES,
                    "atanabilir": can_assign_role(request.user, rol)[0],
                    "gerekce": can_assign_role(request.user, rol)[1],
                }
                for rol in _atanabilir_roller(request.user)
            ]
        return Response({"data": detay})


class EmployeeRoleAssignView(TestUiView):
    """Çalışanın bağlı kullanıcısına rol atar (guard'lar servis katmanında)."""

    def post(self, request, employee_id: str):  # noqa: ANN001
        calisan = _kapsamdaki_calisan(request.user, employee_id)
        if calisan.user_id is None:
            return Response(
                {
                    "error": {
                        "code": "bad_request",
                        "message": (
                            "Bu çalışana bağlı kullanıcı hesabı yok; önce kullanıcı bağlanmalı."
                        ),
                        "detail": "",
                    }
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        rol_id = request.data.get("role_id")
        kapsam_id = request.data.get("scope_org_unit_id") or None
        gerekce = str(request.data.get("reason") or "").strip()
        kimlik = _uuid(rol_id, "role_id")
        roller = {rol.pk: rol for rol in _atanabilir_roller(request.user)}
        if kimlik not in roller:
            raise LeaveError("Rol bulunamadı veya bu kiracıda atanamaz.")
        try:
            atama = assign_role_to_user(
                actor=request.user,
                target_user=calisan.user,
                role=roller[kimlik],
                scope_org_unit_id=kapsam_id,
                reason=gerekce,
            )
        except Exception as hata:  # noqa: BLE001
            return TestUiErrors.cevap(hata)
        return Response({"data": _atama_ozeti(atama)}, status=status.HTTP_201_CREATED)


class RoleAssignmentRevokeView(TestUiView):
    """Rol atamasını kaldırır (soft-delete + denetim, OD21)."""

    def post(self, request, assignment_id: str):  # noqa: ANN001
        atama = _atama_getir(assignment_id)
        try:
            revoke_role_assignment(
                actor=request.user, assignment=atama, reason=str(request.data.get("reason") or "")
            )
        except Exception as hata:  # noqa: BLE001
            return TestUiErrors.cevap(hata)
        return Response({"data": {"kaldirildi": True, "role": atama.role.name}})


class RoleAssignmentRestoreView(TestUiView):
    """Kaldırılmış rol atamasını açıkça geri alır (OD21)."""

    def post(self, request, assignment_id: str):  # noqa: ANN001
        atama = _atama_getir(assignment_id)
        try:
            restore_role_assignment(
                actor=request.user, assignment=atama, reason=str(request.data.get("reason") or "")
            )
        except Exception as hata:  # noqa: BLE001
            return TestUiErrors.cevap(hata)
        return Response({"data": _atama_ozeti(atama)})


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
            # Onay kuyruğunda talep sahibinin bakiyesi de döner (yetki zaten
            # `can_view_leave_request` süzgecinden geçti).
            return Response(
                {
                    "data": [_talep_ozeti(t, user, bakiye_dahil=True) for t in talepler],
                    "meta": {"scope": kapsam},
                }
            )
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
