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
from django.db import DatabaseError, transaction
from django.db.models import Count, Prefetch, Q
from django.http import Http404
from django.utils import timezone
from django.utils.text import slugify
from rest_framework import exceptions as drf_exceptions
from rest_framework import status
from rest_framework.authentication import SessionAuthentication
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.auth_version import (
    AuthVersionMismatchError as AuthVersionMismatch,
)
from apps.accounts.auth_version import (
    oturum_auth_version_dogrula,
)
from apps.accounts.models import UserStatus
from apps.common.crypto import SecretDecryptionError, decrypt_str, encrypt_str
from apps.leave.leave_type_catalog import LeaveCategory
from apps.leave.models import (
    ApprovalStep,
    ApprovalStepStatus,
    BalanceEntryKind,
    LeaveBalance,
    LeaveBalanceEntry,
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
    adjust_leave_balance,
    can_approve_step,
    can_cancel_leave_request,
    can_create_leave_request_for,
    direct_reports,
    can_manage_balances,
    can_manage_leave_types,
    can_view_leave_request,
    cancel_leave_request,
    create_leave_request,
    create_leave_type,
    current_step,
    deactivate_leave_type,
    activate_leave_type,
    decide_approval_step,
    update_leave_policy,
    update_leave_type,
    user_employee,
)
from apps.leave.services.balances import (
    get_or_create_balance,
    next_accrual_date,
    period_year_for,
    set_leave_entitlement,
)
from apps.organization.history import employee_history, timeline_sayfa
from apps.organization.lifecycle import (
    LifecycleStateError,
    LifecycleValidationError,
    change_manager,
    notice_etiketi,
    restore_employee,
    suspend_employee,
    terminate_employee,
)
from apps.organization.models import (
    BloodType,
    Department,
    Employee,
    EmployeeManager,
    FormFieldDefinition,
    Position,
)
from apps.organization.services import (
    OrganizationAuthorizationError,
    OrganizationError,
    OrganizationTreeError,
    change_assignment,
    change_employment_terms,
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
from apps.overtime.models import (
    OVERTIME_MAX_MINUTES,
    OvertimeRequest,
    OvertimeRule,
    OvertimeStatus,
)
from apps.overtime.services import (
    OvertimeAuthorizationError,
    OvertimeError,
    OvertimeStateError,
    OvertimeValidationError,
    can_approve_overtime,
    can_cancel_overtime,
    can_create_overtime_for,
    can_view_overtime,
    cancel_overtime_request,
    create_overtime_rule,
    create_overtime_request,
    delete_overtime_rule,
    decide_overtime_request,
    list_overtime_rules,
    update_overtime_rule,
)
from apps.overtime.services import (
    current_step as current_overtime_step,
)
from apps.overtime.services import (
    user_employee as overtime_user_employee,
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
from apps.rbac.services.scope import object_in_org_scope, user_org_scope
from apps.testui.labels import (
    MODUL_ACIKLAMALARI,
    izin_aciklamasi,
    istisna_etiketi,
    kapsam_metni,
    kapsam_tipi_etiketi,
    modul_etiketi,
)


def _sayfa_no(request, varsayilan: int = 1) -> int:  # noqa: ANN001
    """`?page=N` parametresini güvenli sayfa numarasına çevirir (min 1)."""
    try:
        return max(1, int(request.query_params.get("page", varsayilan)))
    except (TypeError, ValueError):
        return varsayilan


def _test_ui_kapali() -> None:
    """Arayüz kapalıysa uçlar **yok** sayılır (404)."""
    if not getattr(settings, "TEST_UI_ENABLED", False):
        raise Http404("test_ui_disabled")


class TestUiAuthorizationError(RuntimeError):
    """Test arayüzü katmanının kendi yetki reddi (HTTP 403)."""


class TestUiErrors:
    """Servis hatalarını HTTP kodlarına ve anlaşılır mesaja çevirir."""

    #: Durum/kural çakışması olarak raporlanan hatalar (409).
    CAKISMA = (
        LeaveStateError,
        LeavePolicyError,
        LeaveBalanceError,
        OrganizationTreeError,
        OvertimeStateError,
    )
    #: Yetki/kapsam reddi olarak raporlanan hatalar (403). `RoleAssignmentError`
    #: self-elevation/kapsam/izin redlerini taşır (Python `PermissionError` alt sınıfı).
    YETKI = (
        LeaveAuthorizationError,
        OrganizationAuthorizationError,
        RoleAssignmentError,
        OvertimeAuthorizationError,
    )

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
        if isinstance(hata, (LeaveError, OrganizationError, OvertimeError)):
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
        super().initial(request, *args, **kwargs)
        # P1-B (OD59): her oturumlu istekte auth_version doğrulaması — suspend/
        # terminate/archive/restore sonrası eski oturum 401 ile düşer.
        if getattr(request, "user", None) is not None and request.user.is_authenticated:
            try:
                oturum_auth_version_dogrula(
                    request.user,
                    request.session,
                    request_id=getattr(request, "request_id", ""),
                )
            except AuthVersionMismatch as exc:
                raise drf_exceptions.AuthenticationFailed(str(exc)) from exc

    def handle_exception(self, exc):  # noqa: ANN001, ANN201
        """
        Oturum yokken dönen 403'ü ayırt edilebilir kılar (`error.code`).

        Sözleşme durum kodu **değişmez** (oturum kimlik doğrulaması → 403); ancak
        `code="not_authenticated"` ile "yetki yok" (403 + `forbidden`) ayrılır, böylece
        arayüz gerçekten oturumu kapanmış kullanıcıyı girişe yönlendirebilir.
        """
        from rest_framework import exceptions as drf_exceptions_local  # noqa: PLC0415

        if isinstance(
            exc, (drf_exceptions_local.NotAuthenticated, drf_exceptions_local.PermissionDenied)
        ) and (
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
        raise LeaveError(f"'{alan}' YYYY-AA-GG (ISO) biçiminde olmalı.") from hata


def _saat(deger, alan: str):  # noqa: ANN001, ANN202
    """
    `HH:MM` (veya `HH:MM:SS`) metnini saat nesnesine çevirir.

    Fazla mesai saatleri **yerel duvar saati**dir (`work_date` + saat); dönüşüm
    yapılmaz, süre iki saat arasındaki farktan sunucuda hesaplanır.
    """
    from datetime import datetime

    if not deger:
        raise OvertimeValidationError(f"'{alan}' zorunludur.")
    metin = str(deger).strip()
    for bicim in ("%H:%M", "%H:%M:%S"):
        try:
            return datetime.strptime(metin, bicim).time()  # noqa: DTZ007 - duvar saati
        except ValueError:
            continue
    raise OvertimeValidationError(f"'{alan}' SS:DD biçiminde olmalıdır.")


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
    ).prefetch_related(
        # P1-B performans: liste satırı başına manager-relation sorgusunu (N+1)
        # önler. Prefetch edilmiş related manager üzerinde `.filter()` cache'i
        # kullanmadığı için filtre/sıralama **queryset'te** yapılır ve sonuç
        # `to_attr` ile taşınır (`_calisan_ozeti` bu listeden okur).
        Prefetch(
            "manager_relations",
            queryset=EmployeeManager.objects.filter(
                relation_type="primary", effective_to__isnull=True, deleted_at__isnull=True
            )
            .select_related("manager_employee")
            .order_by("-effective_from"),
            to_attr="birincil_yonetici_iliskileri",
        )
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
    if hasattr(employee, "birincil_yonetici_iliskileri"):
        # Prefetch edilmiş (liste yolu): filtre/sıralama queryset'te yapıldı,
        # satır başına ek sorgu yok.
        iliskiler = employee.birincil_yonetici_iliskileri
        iliski = iliskiler[0] if iliskiler else None
    else:
        # Prefetch yoksa (ör. tekil detay yolu) mevcut sorgu korunur.
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
        # P1-B: liste filtreleri (çıkış süreci / askıda sekmeleri) için türetilmiş alanlar.
        "termination_date": (
            employee.termination_date.isoformat() if employee.termination_date else None
        ),
        # Çıkış süreci tablosu ("Çalışanlar → İşten Çıkış / Çıkış Süreci") tarihi
        # sunucudan hazır okunur biçimde gösterir; liste satırında `lifecycle`
        # bulunmadığı için tarih burada türetilir (kaynak tek: termination_date).
        "termination_date_display": (
            employee.termination_date.strftime("%d.%m.%Y")
            if employee.termination_date
            else None
        ),
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


def _departman_calisan_ozeti(employee: Employee, acik_alanlar: list[FormFieldDefinition]) -> dict:
    """Şirket içi dizin için ad, kurumsal e-posta ve açık iş bilgileri."""
    kayit = getattr(employee, "personal_data", None)
    ozel = (getattr(kayit, "custom_fields", {}) or {}) if kayit is not None else {}
    alanlar = []
    for alan in acik_alanlar:
        if alan.data_class == "K3":
            continue
        deger = getattr(kayit, alan.field_code, None) if alan.field_code in PII_ALANLARI else ozel.get(alan.field_code)
        if deger not in (None, ""):
            alanlar.append({"code": alan.field_code, "label": _form_alani_yanit(alan)["label"], "value": str(deger)})
    ad = f"{employee.user.first_name} {employee.user.last_name}".strip() if employee.user_id else employee.employee_no
    iliski = (getattr(employee, "departman_yonetici_iliskileri", []) or [])
    yonetici = iliski[0].manager_employee if iliski else None
    return {
        "id": str(employee.pk),
        "employee_no": employee.employee_no,
        "name": ad or employee.employee_no,
        "email": employee.user.email if employee.user_id else None,
        "department_id": str(employee.department_id) if employee.department_id else None,
        "department": employee.department.name if employee.department_id else None,
        "position": employee.position.name if employee.position_id else None,
        "manager": (
            f"{yonetici.user.first_name} {yonetici.user.last_name}".strip()
            if yonetici is not None and yonetici.user_id
            else (yonetici.employee_no if yonetici is not None else None)
        ),
        "public_fields": alanlar,
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


def _yaklasan_durumu(talep, bugun=None) -> dict:  # noqa: ANN001
    """
    Yaklaşan izin satırının **bugüne göre** durumu: renk **ve** metin birlikte.

    * `izinde`    → `start <= bugün <= end` (bugün izin aralığının içinde) — yeşil.
    * `yaklasiyor`→ başlangıca 1–7 gün kaldı — sarı.
    * `ileri`     → 7 günden uzak — nötr.

    Renk tek bilgi kaynağı değildir: her durumun Türkçe etiketi de döner.
    Zaman dilimi proje standardıyla (`timezone.localdate`) belirlenir.
    """
    gun = bugun or timezone.localdate()
    if talep.start_date <= gun <= talep.end_date:
        return {
            "durum_kodu": "izinde",
            "durum_etiketi": "İzinde",
            "renk": "yesil",
            "kalan_gun": 0,
        }
    kalan = (talep.start_date - gun).days
    if 0 < kalan <= 7:
        return {
            "durum_kodu": "yaklasiyor",
            "durum_etiketi": f"{kalan} gün sonra",
            "renk": "sari",
            "kalan_gun": kalan,
        }
    return {
        "durum_kodu": "ileri",
        "durum_etiketi": f"{kalan} gün sonra",
        "renk": "notr",
        "kalan_gun": kalan,
    }


def _onaylayici_gosterimi(user) -> dict:  # noqa: ANN001
    """
    Onaylayıcının **görünen adı** (kurumsal kimlik; kişisel veri değil).

    `Mehmet Demir — Finans Müdürü` biçiminde: ad + (varsa) iş unvanı. Unvan,
    onaylayıcının çalışan kaydındaki `position` alanından gelir; hesabı bağlı
    değilse yalnız ad/e-posta gösterilir.
    """
    if user is None:
        return {"ad": None, "unvan": None, "etiket": None}
    ad = f"{user.first_name} {user.last_name}".strip() or user.email
    calisan = (
        Employee.objects.filter(user=user, deleted_at__isnull=True)
        .select_related("position")
        .first()
    )
    unvan = calisan.position.name if calisan is not None and calisan.position_id else None
    return {"ad": ad, "unvan": unvan, "etiket": f"{ad} — {unvan}" if unvan else ad}


def _onay_adimlari(talep) -> list[dict]:  # noqa: ANN001
    """Talebin onay adımları (sıra, onaylayıcı, durum) — sunucu kaynak-doğru."""
    adimlar = (
        ApprovalStep.objects.filter(
            subject_kind="leave_request", subject_id=talep.pk, deleted_at__isnull=True
        )
        .select_related("approver_user")
        .order_by("step_no")
    )
    return [
        {
            "step_no": adim.step_no,
            "status": adim.status,
            "status_label": adim.get_status_display(),
            "onaylayici": _onaylayici_gosterimi(adim.approver_user),
            "roller": adim.approver_role_code or None,
            # Rol/kapsam tabanlı adımlarda okunabilir rol adı (ör. "İnsan Kaynakları").
            "rol_adi": (
                Role.objects.filter(code=adim.approver_role_code)
                .values_list("name", flat=True)
                .first()
                if adim.approver_role_code
                else None
            ),
            "guncel": adim.status == ApprovalStepStatus.PENDING,
        }
        for adim in adimlar
    ]


def _bakiye_degerleri_gorunur(user, employee) -> bool:  # noqa: ANN001
    """İzin bakiyesi **değerlerini** görme: `leave.balance.read` ∧ kapsam (fail-closed)."""
    if employee is None:
        return False
    return user_has_permission(user, "leave.balance.read") and object_in_org_scope(
        user,
        department_id=employee.department_id,
        owner_user_id=employee.user_id,
    )


def _karar_bakiye_ozeti(talep, user) -> dict:  # noqa: ANN001
    """
    **Karar anındaki** izin bakiyesi (immutable snapshot) özeti.

    * Değerler yalnız `leave.balance.read` ∧ kapsam sahibine döner; aksi halde
      yanıta **konmaz** (`gorunur=False`, tüm değer alanları `None`).
    * Snapshot yoksa **backfill yapılmaz**: bugünkü bakiye geçmişmiş gibi
      gösterilmez; arayüz "Karar anı bakiyesi kayıtlı değil" der (`var=False`).
    * Alanlar `leave_balances` semantiğiyle aynıdır; yeni kavram üretilmez.
    """
    snapshot = getattr(talep, "decision_snapshot", None)
    gorunur = _bakiye_degerleri_gorunur(user, talep.employee)
    ozet = {
        "var": snapshot is not None,
        "gorunur": bool(gorunur and snapshot is not None),
        "uses_balance": snapshot.uses_balance if snapshot is not None else None,
        "snapshot_at": (
            snapshot.snapshot_at.isoformat()
            if snapshot is not None and gorunur
            else None
        ),
        "entitled_days": None,
        "carried_forward_days": None,
        "adjustment_days": None,
        "hak_toplam": None,
        "used_days": None,
        "reserved_days": None,
        "available_days": None,
    }
    if snapshot is None or not gorunur:
        return ozet

    def metin(alan: str):  # noqa: ANN202
        deger = getattr(snapshot, alan)
        return None if deger is None else str(deger)

    hak = [snapshot.entitled_days, snapshot.carried_forward_days, snapshot.adjustment_days]
    ozet.update(
        {
            "entitled_days": metin("entitled_days"),
            "carried_forward_days": metin("carried_forward_days"),
            "adjustment_days": metin("adjustment_days"),
            "hak_toplam": (
                str(sum(hak, Decimal("0.0"))) if all(d is not None for d in hak) else None
            ),
            "used_days": metin("used_days"),
            "reserved_days": metin("reserved_days"),
            "available_days": metin("available_days"),
        }
    )
    return ozet


def _karar_gerekcesi(talep) -> str | None:  # noqa: ANN001
    """
    Karara bağlanan adımın gerekçesi (red gerekçesi dahil); yoksa None.

    Kaynak `approval_steps.decision_reason`: red durumunda **reddeden** adımın,
    onay durumunda karara bağlanmış adımların gerekçesi okunur (iptal edilen
    adımların "Talep reddedildi" metni karar gerekçesi sayılmaz).
    """
    hedef_durum = (
        ApprovalStepStatus.REJECTED
        if talep.status == LeaveRequestStatus.REJECTED
        else ApprovalStepStatus.APPROVED
    )
    gerekce = (
        ApprovalStep.objects.filter(
            subject_id=talep.pk, status=hedef_durum, deleted_at__isnull=True
        )
        .exclude(decision_reason="")
        .order_by("-step_no")
        .values_list("decision_reason", flat=True)
        .first()
    )
    return gerekce or None


def _talep_ozeti(talep, user, bakiye_dahil: bool = False) -> dict:  # noqa: ANN001
    adim = current_step(talep)
    kendi_employee_id = getattr(user_employee(user), "pk", None)
    adimlar = _onay_adimlari(talep)
    siradaki = next((a for a in adimlar if a["guncel"]), None)
    return {
        "id": str(talep.pk),
        "employee_no": talep.employee.employee_no,
        "employee": _calisan_etiketi(talep.employee),
        "employee_id": str(talep.employee_id),
        "department": talep.employee.department.name if talep.employee.department_id else None,
        "leave_type": talep.leave_type.name,
        "leave_type_code": talep.leave_type.code,
        "start_date": talep.start_date.isoformat(),
        "end_date": talep.end_date.isoformat(),
        "start_time": talep.start_time.isoformat() if talep.start_time else None,
        "end_time": talep.end_time.isoformat() if talep.end_time else None,
        "days_requested": str(talep.days_requested),
        "status": talep.status,  # gerçek durum kodu (kapalı küme)
        "status_label": talep.get_status_display(),  # Türkçe karşılığı (sunum)
        "reason": talep.reason,
        "submitted_at": talep.submitted_at.isoformat() if talep.submitted_at else None,
        "decided_at": talep.decided_at.isoformat() if talep.decided_at else None,
        "karar_veren": (
            _onaylayici_gosterimi(talep.decided_by)["etiket"] if talep.decided_by_id else None
        ),
        # Karar detayı (M14/K): karar gerekçesi + **karar anı** bakiyesi (snapshot).
        # Bugünkü bakiye burada gösterilmez; snapshot yoksa değer dönmez.
        "karar_gerekcesi": _karar_gerekcesi(talep),
        "karar_bakiye": _karar_bakiye_ozeti(talep, user),
        "canceled_at": talep.canceled_at.isoformat() if talep.canceled_at else None,
        "requires_document": talep.leave_type.requires_document,
        "document_ref": str(talep.document_ref) if talep.document_ref else None,
        "current_step_no": adim.step_no if adim is not None else None,
        # Onay akışı: adımlar ve **sıradaki onaylayıcı** (önizleme). Kaynak
        # `approval_steps`; arayüz onaylayıcı tahmin etmez.
        "onay_adimlari": adimlar,
        "siradaki_onaylayici": siradaki,
        "kendi_talebi": kendi_employee_id is not None
        and talep.employee_id == kendi_employee_id,
        "can_edit": (
            kendi_employee_id is not None
            and talep.employee_id == kendi_employee_id
            and talep.status in (
                LeaveRequestStatus.DRAFT,
                LeaveRequestStatus.PENDING,
                LeaveRequestStatus.APPROVED,
            )
            and can_cancel_leave_request(user, talep)
        ),
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
        "entitled_days": _gun_metin(bakiye.entitled_days),
        "carried_forward_days": _gun_metin(bakiye.carried_forward_days),
        "adjustment_days": _gun_metin(bakiye.adjustment_days),
        "used_days": _gun_metin(bakiye.used_days),
        "reserved_days": _gun_metin(bakiye.reserved_days),
        "available_days": _gun_metin(bakiye.available_days),
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


def _lifecycle_ozeti(user, calisan: Employee) -> dict:  # noqa: ANN001
    """
    P1-B — Çalışan Durumu bölümü verisi (türetilmiş; PII yok).

    `notice` (OD41) sunucuda türetilir; aksiyon yetkileri izin ∧ durum makinesi
    ile hesaplanır (arayüz yalnız düğmeyi gizler, karar backend'dedir).
    """
    from apps.organization.lifecycle import IZINLI_GECISLER as _IZINLI  # noqa: PLC0415
    from apps.organization.lifecycle import ihbar_döneminde as _ihbar  # noqa: PLC0415

    notice = notice_etiketi(calisan)
    return {
        "status": calisan.status,
        "status_label": calisan.get_status_display(),
        "termination_date": (
            calisan.termination_date.isoformat() if calisan.termination_date else None
        ),
        "termination_date_display": (
            calisan.termination_date.strftime("%d.%m.%Y")
            if calisan.termination_date
            else None
        ),
        "notice": notice,
        "can": {
            "suspend": calisan.status == "active"
            and user_has_permission(user, "employee.suspend"),
            "restore": calisan.status in ("suspended", "terminated")
            and user_has_permission(user, "employee.restore"),
            "terminate": calisan.status in ("active", "suspended")
            and user_has_permission(user, "employee.terminate"),
            "history": user_has_permission(user, "employee.history.read"),
        },
        "gecisler": sorted(_IZINLI.get(calisan.status, frozenset())),
        "ihbar_döneminde": _ihbar(calisan),
    }


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


def _gun_metin(deger) -> str:  # noqa: ANN001
    """Gün tabanlı eski UI sözleşmesini tek ondalıkla korur."""
    return str(Decimal(deger).quantize(Decimal("0.1")))


def _bakiye_ozeti(bakiye: LeaveBalance) -> dict:  # noqa: ANN001
    """
    Bakiye — türetilen değerler **backend'den** gelir (üretilen kolon).

    `sonraki_hakedis`: işe giriş yıl dönümüne göre bir sonraki hakediş tarihi
    (`next_accrual_date`); arayüz hesap yapmaz, yalnız gösterir.
    """
    sonraki = next_accrual_date(bakiye.employee.hire_date) if bakiye.employee_id else None
    saatli = bakiye.leave_type.is_time_based and bakiye.leave_type.workday_minutes
    saat = lambda deger: (
        str((Decimal(deger) * Decimal(bakiye.leave_type.workday_minutes) / Decimal("60")).normalize())
        if saatli and deger is not None else None
    )
    gun = lambda deger: _gun_metin(deger) if deger is not None else None
    return {
        "leave_type": bakiye.leave_type.name,
        "leave_type_code": bakiye.leave_type.code,
        "leave_type_id": str(bakiye.leave_type_id),
        "period_year": bakiye.period_year,
        "entitled_days": gun(bakiye.entitled_days),
        "carried_forward_days": gun(bakiye.carried_forward_days),
        "adjustment_days": gun(bakiye.adjustment_days),
        "used_days": gun(bakiye.used_days),
        "reserved_days": gun(bakiye.reserved_days),
        "available_days": gun(bakiye.available_days),
        "is_time_based": bool(saatli),
        "workday_minutes": bakiye.leave_type.workday_minutes if saatli else None,
        "entitled_hours": saat(bakiye.entitled_days),
        "carried_forward_hours": saat(bakiye.carried_forward_days),
        "adjustment_hours": saat(bakiye.adjustment_days),
        "used_hours": saat(bakiye.used_days),
        "reserved_hours": saat(bakiye.reserved_days),
        "available_hours": saat(bakiye.available_days),
        "sonraki_hakedis": sonraki.isoformat() if sonraki else None,
        "sonraki_hakedis_metin": sonraki.strftime("%d.%m.%Y") if sonraki else None,
    }


def _izin_haklari_listesi(employee, yil: int) -> list[dict]:  # noqa: ANN001
    """
    Çalışanın dönem **izin hakları** — bakiye kaydı OLMAYAN türler de listelenir.

    NEDEN (bildirim): "yeni oluşturulmuş çalışana izin hakkı eklenemedi". Kaynak neden,
    arayüzün yalnızca **var olan** `leave_balances` satırlarını listelemesiydi: yeni
    çalışanda hiç satır olmadığı için "hak tanımla" düğmesi de hiç görünmüyordu.
    Artık satır kümesi **bakiye kullanan aktif izin türleri** üzerinden kurulur; değerler
    varsa `leave_balances`'tan gelir (türetilmiş `kalan` = `available_days` üretilen kolon).

    Ek alanlar:
      * `hakedis_tanimli` → dönem için defterde `accrual` kaydı var mı (ilk tanımlama
        mı, düzeltme mi olduğunu arayüz sunucudan öğrenir; tahmin etmez).
      * `hak_toplam` → `entitled + carried_forward + adjustment` (hak − kullanılan −
        bekleyen = kalan eşitliği korunur).
    """
    satirlar: list[dict] = []
    turler = LeaveType.objects.filter(
        deleted_at__isnull=True, status=RecordStatus.ACTIVE, uses_balance=True
    ).order_by("code")
    for tur in turler:
        bakiye = (
            LeaveBalance.objects.filter(
                employee=employee, leave_type=tur, period_year=yil, deleted_at__isnull=True
            )
            .select_related("leave_type")
            .first()
        )
        if bakiye is None:
            satirlar.append(
                {
                    "leave_type_id": str(tur.pk),
                    "leave_type": tur.name,
                    "leave_type_code": tur.code,
                    "period_year": yil,
                    "hakedis_var": False,
                    "hakedis_tanimli": False,
                    "hak_toplam": None,
                    "entitled_days": None,
                    "carried_forward_days": None,
                    "adjustment_days": None,
                    "used_days": None,
                    "reserved_days": None,
                    "kalan": None,
                    "sonraki_hakedis_metin": None,
                    "mesaj": "Bu dönem için hak tanımlanmamış.",
                    "is_time_based": tur.is_time_based,
                    "workday_minutes": tur.workday_minutes,
                    "hak_toplam_hours": None,
                    "used_hours": None,
                    "reserved_hours": None,
                    "kalan_hours": None,
                }
            )
            continue
        hak_toplam = (
            Decimal(bakiye.entitled_days)
            + Decimal(bakiye.carried_forward_days)
            + Decimal(bakiye.adjustment_days)
        )
        hakedis_tanimli = LeaveBalanceEntry.objects.filter(
            balance=bakiye, entry_kind=BalanceEntryKind.ACCRUAL, period_year=yil
        ).exists()
        sonraki = next_accrual_date(employee.hire_date) if employee.hire_date else None
        satirlar.append(
            {
                "leave_type_id": str(tur.pk),
                "leave_type": tur.name,
                "leave_type_code": tur.code,
                "period_year": yil,
                "hakedis_var": True,
                "hakedis_tanimli": hakedis_tanimli,
                "hak_toplam": _gun_metin(hak_toplam) if not tur.is_time_based else str(hak_toplam),
                "entitled_days": _gun_metin(bakiye.entitled_days) if not tur.is_time_based else str(bakiye.entitled_days),
                "carried_forward_days": _gun_metin(bakiye.carried_forward_days) if not tur.is_time_based else str(bakiye.carried_forward_days),
                "adjustment_days": _gun_metin(bakiye.adjustment_days) if not tur.is_time_based else str(bakiye.adjustment_days),
                "used_days": _gun_metin(bakiye.used_days) if not tur.is_time_based else str(bakiye.used_days),
                "reserved_days": _gun_metin(bakiye.reserved_days) if not tur.is_time_based else str(bakiye.reserved_days),
                "kalan": _gun_metin(bakiye.available_days) if not tur.is_time_based else str(bakiye.available_days),
                "is_time_based": tur.is_time_based,
                "workday_minutes": tur.workday_minutes if tur.is_time_based else None,
                "hak_toplam_hours": (
                    str((hak_toplam * Decimal(tur.workday_minutes) / Decimal("60")).normalize())
                    if tur.is_time_based and tur.workday_minutes else None
                ),
                "used_hours": (
                    str((Decimal(bakiye.used_days) * Decimal(tur.workday_minutes) / Decimal("60")).normalize())
                    if tur.is_time_based and tur.workday_minutes else None
                ),
                "reserved_hours": (
                    str((Decimal(bakiye.reserved_days) * Decimal(tur.workday_minutes) / Decimal("60")).normalize())
                    if tur.is_time_based and tur.workday_minutes else None
                ),
                "kalan_hours": (
                    str((Decimal(bakiye.available_days) * Decimal(tur.workday_minutes) / Decimal("60")).normalize())
                    if tur.is_time_based and tur.workday_minutes else None
                ),
                "sonraki_hakedis_metin": sonraki.strftime("%d.%m.%Y") if sonraki else None,
                "mesaj": "",
            }
        )
    return satirlar


# --- Fazla mesai (P5a V1) -----------------------------------------------------


def _sure_metin(dakika: int | None) -> str | None:
    """Dakikayı okunabilir süreye çevirir (arayüz hesap yapmaz)."""
    if dakika is None:
        return None
    saat, kalan = divmod(int(dakika), 60)
    if saat and kalan:
        return f"{saat} sa {kalan} dk"
    if saat:
        return f"{saat} sa"
    return f"{kalan} dk"


def _adim_onaylayici(adim) -> dict:  # noqa: ANN001
    """
    Karar bekleyen adımın onaylayıcısı — **sunucu kaynak-doğru** (arayüz tahmin etmez).

    Adım ya bir kişiye (birincil yönetici) ya da bir role (İK) bağlıdır; iki durum da
    okunabilir tek etikete indirgenir.
    """
    if adim is None:
        return {"etiket": None, "tur": None, "ad": None, "rol": None}
    if adim.approver_user_id is not None:
        gosterim = _onaylayici_gosterimi(adim.approver_user)
        return {
            "etiket": gosterim["etiket"],
            "tur": "kisi",
            "ad": gosterim["ad"],
            "rol": gosterim["unvan"],
        }
    rol_adi = (
        Role.objects.filter(code=adim.approver_role_code).values_list("name", flat=True).first()
        or adim.approver_role_code
    )
    return {"etiket": rol_adi, "tur": "rol", "ad": None, "rol": rol_adi}


def _fazla_mesai_adimlari(talep) -> list[dict]:  # noqa: ANN001
    """Talebin onay adımları (sıra, onaylayıcı, durum) — onay izi görünür kalır."""
    adimlar = (
        ApprovalStep.objects.filter(
            subject_kind="overtime_request",
            subject_id=talep.pk,
            deleted_at__isnull=True,
        )
        .select_related("approver_user")
        .order_by("step_no")
    )
    return [
        {
            "step_no": adim.step_no,
            "status": adim.status,
            "status_label": adim.get_status_display(),
            "onaylayici": _adim_onaylayici(adim),
            "guncel": adim.status == ApprovalStepStatus.PENDING,
        }
        for adim in adimlar
    ]


def _fazla_mesai_ozeti(talep, user) -> dict:  # noqa: ANN001
    """Fazla mesai kaydı özeti (kişisel veri içermez; süre sunucudan gelir)."""
    adim = current_overtime_step(talep)
    adimlar = _fazla_mesai_adimlari(talep)
    return {
        "id": str(talep.pk),
        "employee_id": str(talep.employee_id),
        "employee_no": talep.employee.employee_no,
        "employee": _calisan_etiketi(talep.employee),
        "department": talep.employee.department.name if talep.employee.department_id else None,
        "work_date": talep.work_date.isoformat(),
        "start_time": talep.start_time.strftime("%H:%M"),
        "end_time": talep.end_time.strftime("%H:%M"),
        "saat_araligi": (
            f"{talep.start_time.strftime('%H:%M')} - {talep.end_time.strftime('%H:%M')}"
        ),
        "total_minutes": talep.total_minutes,
        "sure_metin": _sure_metin(talep.total_minutes),
        "description": talep.description,
        "overtime_rule_id": str(talep.overtime_rule_id) if talep.overtime_rule_id else None,
        "overtime_rule_code": talep.overtime_rule.code if talep.overtime_rule_id else None,
        "overtime_rule_name": talep.overtime_rule.name if talep.overtime_rule_id else None,
        "status": talep.status,
        "status_label": talep.get_status_display(),
        "compensation_type": talep.compensation_type,
        "onaylayici": _adim_onaylayici(adim),
        "karar_bekleyen_adim": adim.step_no if adim is not None else None,
        "onay_adimlari": adimlar,
        "kendi_kaydim": talep.employee.user_id is not None
        and str(talep.employee.user_id) == str(getattr(user, "pk", None)),
        "can_cancel": can_cancel_overtime(user, talep),
        "can_decide": adim is not None and can_approve_overtime(user, talep),
        "decided_at": talep.decided_at.isoformat() if talep.decided_at else None,
        "karar_veren": (
            _onaylayici_gosterimi(talep.decided_by)["etiket"] if talep.decided_by_id else None
        ),
        "cancel_reason": talep.cancel_reason or None,
        "created_at": talep.created_at.isoformat(),
    }


def _fazla_mesai_getir(user, talep_id):  # noqa: ANN001
    """Kapsamdaki/kendi fazla mesai kaydı; görünmüyorsa **404** (varlık sızdırılmaz)."""
    kimlik = _uuid(talep_id, "overtime_id")
    talep = (
        OvertimeRequest.objects.filter(pk=kimlik, deleted_at__isnull=True)
        .select_related("employee__department", "employee__user", "decided_by", "overtime_rule")
        .first()
    )
    if talep is None or not can_view_overtime(user, talep):
        raise Http404("fazla_mesai_kapsam_disi")
    return talep


def _gorunur_fazla_mesailer(user):  # noqa: ANN001, ANN202
    """
    Aktörün görebildiği fazla mesai kayıtları (kapsam süzgeci **sunucuda**).

    Kiracı sınırı RLS ile zaten kapalıdır; burada izin + kapsam uygulanır.
    """
    if not user_has_permission(user, "overtime.request.read"):
        return []
    kayitlar = (
        OvertimeRequest.objects.filter(deleted_at__isnull=True)
        .select_related("employee__department", "employee__user", "decided_by", "overtime_rule")
        .order_by("-work_date", "-start_time")[:200]
    )
    return [t for t in kayitlar if can_view_overtime(user, t)]


# --- uç noktalar --------------------------------------------------------------


class MeView(TestUiView):
    """Oturumdaki kullanıcı, kiracı, roller, kapsam ve etkin izinler."""

    def get(self, request):  # noqa: ANN001
        user = request.user
        roller = _kullanicinin_rolleri(user)
        calisan = user_employee(user)
        izinler = sorted(effective_permission_codes(user))
        arayuz_izinleri = {kod for kod in izinler if kod.startswith("ui.")}

        def arayuz_menu(kod: str, eski_kod: str | tuple[str, ...] | None = None) -> bool:
            # Migration uygulanmamış/yeni kiracıda mevcut menü davranışı korunur;
            # en az bir ui kodu tanımlandığında görünürlük yalnız ui koduna bağlıdır.
            eski_kodlar = (eski_kod,) if isinstance(eski_kod, str) else (eski_kod or ())
            return (
                user_has_permission(user, kod)
                if arayuz_izinleri
                else any(user_has_permission(user, eski) for eski in eski_kodlar)
            )

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
                        # Her ana sekmenin görünürlüğü backend yetki kararından
                        # gelir; istemci yalnızca bu sonucu uygular.
                        "panel": arayuz_menu("ui.panel.view", ("employee.read", "leave.request.read", "leave.request.create", "overtime.request.read", "role.manage")),
                        "departments": arayuz_menu("ui.departments.view", "org.department.read"),
                        "employees": arayuz_menu("ui.employees.view", "employee.read"),
                        "employees_list": arayuz_menu("ui.employees.list", "employee.read"),
                        "employees_new": arayuz_menu("ui.employees.new", "employee.create"),
                        "employees_form": arayuz_menu("ui.employees.form", "employee.form.manage"),
                        "employees_exit": arayuz_menu("ui.employees.exit", ("employee.terminate", "employee.restore")),
                        "employees_suspended": arayuz_menu("ui.employees.suspended", ("employee.suspend", "employee.restore")),
                        "leave": arayuz_menu("ui.leave.view", ("leave.request.read", "leave.request.create")),
                        "leave_request": arayuz_menu("ui.leave.request", ("leave.request.read", "leave.request.create")),
                        "leave_policy": arayuz_menu("ui.leave.policy", "leave.type.manage"),
                        "leave_types": arayuz_menu("ui.leave.types", "leave.type.manage"),
                        "leave_entitlements": arayuz_menu("ui.leave.entitlements", ("leave.balance.read", "leave.balance.manage")),
                        "overtime": arayuz_menu("ui.overtime.view", ("overtime.request.read", "overtime.request.create")),
                        "overtime_requests": arayuz_menu("ui.overtime.requests", ("overtime.request.read", "overtime.request.create")),
                        "overtime_rules": arayuz_menu("ui.overtime.rules", "overtime.rule.read"),
                        "approvals": arayuz_menu("ui.approvals.view", ("leave.request.approve", "overtime.request.approve")),
                        "roles_view": arayuz_menu("ui.roles.view", ("role.read", "role.manage")),
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
                        # İzin bakiyeleri ekranı yalnızca bakiye okuma yetkisi olana
                        # görünür (asıl kapı backend'de; UI yalnızca gizler).
                        "balances_read": user_has_permission(user, "leave.balance.read"),
                        "balances_manage": user_has_permission(user, "leave.balance.manage"),
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
            {**_talep_ozeti(t, user), **_yaklasan_durumu(t, bugun)}
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

        # Operasyonel özet (panel üst kartları): sayılar **kapsam süzgecinden** geçer.
        bugun_izinde = [
            t
            for t in LeaveRequest.objects.filter(
                deleted_at__isnull=True,
                status=LeaveRequestStatus.APPROVED,
                start_date__lte=bugun,
                end_date__gte=bugun,
            )
            .select_related("employee__department", "leave_type", "employee")
            .order_by("end_date")[:50]
            if can_view_leave_request(user, t)
        ]

        # Bakiye kritik çalışanlar (kalan ≤ 3 gün) — geniş alt alan için.
        kritik_bakiyeler: list[dict] = []
        if user_has_permission(user, "leave.balance.read"):
            for kisi in kapsamdaki_calisanlar(user):
                ozet_yillik = _yillik_ozet(kisi, yil)
                if not ozet_yillik.get("hakedis_var"):
                    continue
                if Decimal(ozet_yillik["available_days"]) <= Decimal("3.0"):
                    kritik_bakiyeler.append(
                        {
                            "employee_id": str(kisi.pk),
                            "employee_no": kisi.employee_no,
                            "employee": _calisan_etiketi(kisi),
                            "department": (
                                kisi.department.name if kisi.department_id else None
                            ),
                            **ozet_yillik,
                        }
                    )
            kritik_bakiyeler = kritik_bakiyeler[:10]

        # Karara bağlanmış talepler (onay/red): kapsam süzgeci aynı kuraldan geçer.
        karara_baglananlar = [
            _talep_ozeti(t, user)
            for t in (
                LeaveRequest.objects.filter(
                    deleted_at__isnull=True,
                    status__in=[LeaveRequestStatus.APPROVED, LeaveRequestStatus.REJECTED],
                )
                .select_related(
                    "employee__department", "leave_type", "employee", "decision_snapshot"
                )
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

        # Fazla mesai özeti (P5a V1): panel kalabalıklaştırılmaz, yalnız iki sayı döner.
        #   * `bekleyen`       → kullanıcının **kendi** onay bekleyen bildirimi
        #   * `karar_bekleyen` → kullanıcının karara bağlayabileceği bildirim sayısı
        fazla_mesai = {"bekleyen": 0, "karar_bekleyen": 0}
        if user_has_permission(user, "overtime.request.read"):
            gorunur_fm = _gorunur_fazla_mesailer(user)
            fazla_mesai["bekleyen"] = sum(
                1
                for t in gorunur_fm
                if t.status == OvertimeStatus.PENDING
                and calisan is not None
                and t.employee_id == calisan.pk
            )
            fazla_mesai["karar_bekleyen"] = sum(
                1
                for t in gorunur_fm
                if t.status == OvertimeStatus.PENDING and can_approve_overtime(user, t)
            )

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
                        # İşe giriş tarihi "Bilgilerim" kartında gösterilir (bildirim).
                        "hire_date": (
                            calisan.hire_date.isoformat() if calisan and calisan.hire_date else None
                        ),
                        "annual": _yillik_ozet(calisan, yil),
                    },
                    "fazla_mesai": fazla_mesai,
                    "pii_gorunur": user_has_permission(user, "employee.sensitive.read"),
                    "upcoming_requests": yaklasanlar,
                    "decided_requests": karara_baglananlar,
                    "status_counts": durum_sayilari,
                    "balances": bakiyeler,
                    "annual": _yillik_ozet(calisan, yil),
                    "my_requests": kendi_talepleri,
                    # "İzin taleplerim" kartı yalnız son kayıtları gösterir; toplam sayı
                    # sunucudan gelir ki arayüz "Tümünü gör" gerekip gerekmediğini
                    # tahmin etmesin.
                    "my_requests_count": (
                        calisan.leave_requests.filter(deleted_at__isnull=True).count()
                        if calisan is not None
                        else 0
                    ),
                    "pending_requests": onay_bekleyen,
                    "pending_count": len(onay_bekleyen),
                    # Panel üst özet kartları (her değer kapsam süzgecinden geçer).
                    "ozet": {
                        "toplam_calisan": (
                            sum(1 for _ in kapsamdaki_calisanlar(user))
                            if user_has_permission(user, "employee.read")
                            else None
                        ),
                        "bugun_izinde": len(bugun_izinde),
                        "bugun_izinde_kisiler": [
                            {
                                "employee_no": t.employee.employee_no,
                                "employee": _calisan_etiketi(t.employee),
                                "bitis": t.end_date.isoformat(),
                            }
                            for t in bugun_izinde[:5]
                        ],
                        "yaklasan_7g": sum(
                            1 for t in yaklasanlar if t["durum_kodu"] == "yaklasiyor"
                        ),
                        "bekleyen_onay": len(onay_bekleyen),
                        "kritik_bakiye": len(kritik_bakiyeler),
                    },
                    "kritik_bakiyeler": kritik_bakiyeler,
                    "bugun": bugun.isoformat(),
                }
            }
        )


class DepartmentListView(TestUiView):
    """Departman ağacı (kiracı içi; mutasyon yetkisi servis katmanında)."""

    def get(self, request):  # noqa: ANN001
        acik_alanlar = list(FormFieldDefinition.objects.filter(
            tenant_id=request.user.tenant_id,
            form_code="employee_onboarding",
            deleted_at__isnull=True,
            is_visible=True,
            is_public=True,
        ).order_by("sort_order", "created_at"))
        calisanlar = list(Employee.objects.filter(
            tenant_id=request.user.tenant_id,
            deleted_at__isnull=True,
        ).select_related("user", "department", "position").prefetch_related(
            "personal_data",
            Prefetch(
                "manager_relations",
                queryset=EmployeeManager.objects.filter(
                    relation_type="primary", effective_to__isnull=True, deleted_at__isnull=True
                ).select_related("manager_employee__user"),
                to_attr="departman_yonetici_iliskileri",
            ),
        ))
        kayitlar = list(
            Department.objects.filter(deleted_at__isnull=True)
            .select_related("parent", "manager_employee")
            .annotate(
                # P1-B performans: birim başına ayrı COUNT (N+1) yerine tek sorgu.
                employee_count_annotated=Count(
                    "employees",
                    filter=Q(employees__deleted_at__isnull=True),
                    distinct=True,
                )
            )
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
                        "employee_count": d.employee_count_annotated,
                    }
                        for d in kayitlar
                    ],
                "employees": [_departman_calisan_ozeti(e, acik_alanlar) for e in calisanlar],
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


def _form_alani_yanit(alan: FormFieldDefinition) -> dict:
    etiket = alan.label_override or {
        "first_name": "Ad", "last_name": "Soyad", "email": "Kurumsal e-posta",
        "password": "Parola", "hire_date": "İşe giriş tarihi", "department_id": "Birim",
        "employee_no": "Çalışan numarası", "status": "Durum", "position_id": "Unvan",
        "manager_employee_id": "Bağlı olduğu yönetici", "employment_type": "İstihdam türü",
        "working_model": "Çalışma modeli", "workplace_id": "İşyeri", "contract_type": "Sözleşme türü",
        "contract_start_date": "Sözleşme başlangıç tarihi", "contract_end_date": "Sözleşme bitiş tarihi",
        "probation_end_date": "Deneme süresi bitiş tarihi", "birth_date": "Doğum tarihi",
        "national_id": "Kimlik numarası", "citizenship": "Vatandaşlık",
        "personal_email": "Kişisel e-posta", "personal_phone": "Kişisel telefon",
        "address": "Adres", "emergency_contact_phone": "Acil durum telefonu",
        "emergency_contact_relation": "Acil durum yakını", "bank_name": "Banka adı",
        "iban": "IBAN", "work_permit_type": "Çalışma izni",
        "work_permit_valid_until": "Çalışma izni geçerlilik tarihi", "blood_type": "Kan grubu",
        "health_notes": "Sağlık notları", "disability_status": "Engellilik durumu",
        "biometric_ref": "Biyometrik referans",
    }.get(alan.field_code, alan.field_code.removeprefix("custom_").replace("_", " ").title())
    return {
        "id": str(alan.pk),
        "code": alan.field_code,
        "label": etiket,
        "field_type": alan.field_type,
        "options": alan.options or [],
        "layout_width": alan.layout_width,
        "is_required": alan.is_required,
        "is_visible": alan.is_visible,
        "is_public": alan.is_public,
        "section_label": alan.section_label,
        "helper_text": alan.helper_text,
        "sort_order": alan.sort_order,
        "is_custom": alan.field_code.startswith("custom_"),
        "active": alan.deleted_at is None and alan.is_visible,
        "deleted": alan.deleted_at is not None,
        "can_toggle": alan.lock_level != "A",
        "lock_level": alan.lock_level,
    }


class EmployeeFormFieldListView(TestUiView):
    """Yeni çalışan formuna tenant'a özel alan ekleme/düzenleme/kapatma."""

    def _izin(self, request):  # noqa: ANN001
        if not user_has_permission(request.user, "employee.form.manage"):
            raise TestUiAuthorizationError("Çalışan formu alanlarını yönetme izniniz yok.")

    def get(self, request):  # noqa: ANN001
        try:
            self._izin(request)
        except TestUiAuthorizationError as hata:
            return TestUiErrors.cevap(hata)
        return Response({
            "data": [
                _form_alani_yanit(alan)
                for alan in FormFieldDefinition.objects.filter(
                    tenant_id=request.user.tenant_id,
                    form_code="employee_onboarding",
                ).order_by("sort_order", "created_at")
            ]
        })

    def post(self, request):  # noqa: ANN001
        try:
            self._izin(request)
            veri = request.data
            etiket = str(veri.get("label") or "").strip()
            tip = str(veri.get("field_type") or "text").strip().lower()
            try:
                genislik = int(veri.get("layout_width") or 1)
            except (TypeError, ValueError):
                raise LeaveError("Alan genişliği 1 ile 5 arasında olmalıdır.") from None
            if genislik not in range(1, 6):
                raise LeaveError("Alan genişliği 1 ile 5 arasında olmalıdır.")
            if not etiket or len(etiket) > 190:
                raise LeaveError("Alan etiketi 1-190 karakter olmalıdır.")
            if tip not in OZEL_FORM_TIPLERI:
                raise LeaveError("Alan tipi text, number, date veya select olmalıdır.")
            secenekler = veri.get("options") or []
            if isinstance(secenekler, str):
                secenekler = [satir.strip() for satir in secenekler.splitlines() if satir.strip()]
            if tip == "select":
                if not isinstance(secenekler, list) or not secenekler:
                    raise LeaveError("Seçim alanı için en az bir seçenek girilmelidir.")
                secenekler = list(dict.fromkeys(str(x).strip() for x in secenekler if str(x).strip()))
                if not secenekler:
                    raise LeaveError("Seçim alanı için en az bir seçenek girilmelidir.")
            else:
                secenekler = []
            kok = slugify(etiket, allow_unicode=False).replace("-", "_")[:40].strip("_")
            if not kok:
                raise LeaveError("Alan etiketi geçerli bir alan adı üretmiyor.")
            kod = "custom_" + kok
            if FormFieldDefinition.objects.filter(
                tenant_id=request.user.tenant_id, form_code="employee_onboarding",
                field_code=kod, deleted_at__isnull=True,
            ).exists():
                raise LeaveError("Bu etiketle bir özel alan zaten var.")
            alan = FormFieldDefinition.objects.create(
                tenant_id=request.user.tenant_id,
                form_code="employee_onboarding",
                field_code=kod,
                section_code="custom",
                section_label=str(veri.get("section_label") or "Özel bilgiler").strip()[:100],
                sort_order=int(veri.get("sort_order") or 0),
                is_visible=bool(veri.get("is_visible", True)),
                is_public=bool(veri.get("is_public", False)),
                is_required=bool(veri.get("is_required", False)),
                label_override=etiket,
                helper_text=str(veri.get("helper_text") or "").strip()[:300],
                field_type=tip,
                options=secenekler,
                layout_width=genislik,
                lock_level="B",
                data_class="K2",
                created_by=request.user,
            )
            from apps.audit.services import log_action  # noqa: PLC0415
            log_action(
                tenant_id=request.user.tenant_id, action="employee_form.field.create",
                user=request.user, table_name="form_field_definitions", record_id=alan.pk,
                changes={"field_code": kod, "field_type": tip},
            )
        except (LeaveError, TestUiAuthorizationError, ValueError) as hata:
            return TestUiErrors.cevap(hata)
        return Response({"data": _form_alani_yanit(alan)}, status=status.HTTP_201_CREATED)


class EmployeeFormFieldUpdateView(TestUiView):
    def post(self, request, field_id: str):  # noqa: ANN001
        try:
            if not user_has_permission(request.user, "employee.form.manage"):
                raise TestUiAuthorizationError("Çalışan formu alanlarını yönetme izniniz yok.")
            alan = FormFieldDefinition.objects.filter(
                pk=_uuid(field_id, "field_id"), tenant_id=request.user.tenant_id,
                form_code="employee_onboarding",
            ).first()
            if alan is None:
                raise LeaveError("Özel form alanı bulunamadı.")
            veri = request.data
            if alan.deleted_at is not None and veri.get("active") is not True:
                raise LeaveError("Pasif özel alanı düzenlemek için önce aktifleştirin.")
            if "active" in veri:
                if alan.lock_level == "A" and not bool(veri["active"]):
                    raise LeaveError("Sistem zorunlu alanı pasifleştirilemez.")
                if bool(veri["active"]):
                    alan.deleted_at = None
                    alan.is_visible = True
                else:
                    alan.is_visible = False
            if "label" in veri:
                etiket = str(veri.get("label") or "").strip()
                if not etiket or len(etiket) > 190:
                    raise LeaveError("Alan etiketi 1-190 karakter olmalıdır.")
                alan.label_override = etiket
            if "is_required" in veri:
                if alan.data_class == "K3" and bool(veri["is_required"]):
                    raise LeaveError("Özel nitelikli kişisel veri alanı zorunlu yapılamaz.")
                if alan.lock_level == "A" and not bool(veri["is_required"]):
                    raise LeaveError("Sistem zorunlu alanı opsiyonel yapılamaz.")
                alan.is_required = bool(veri["is_required"])
            if "is_visible" in veri:
                alan.is_visible = bool(veri["is_visible"])
            if "is_public" in veri:
                alan.is_public = bool(veri["is_public"])
            if "helper_text" in veri:
                alan.helper_text = str(veri.get("helper_text") or "").strip()[:300]
            if "layout_width" in veri:
                try:
                    alan.layout_width = int(veri["layout_width"])
                except (TypeError, ValueError):
                    raise LeaveError("Alan genişliği 1 ile 5 arasında olmalıdır.") from None
                if alan.layout_width not in range(1, 6):
                    raise LeaveError("Alan genişliği 1 ile 5 arasında olmalıdır.")
            if "options" in veri:
                if alan.field_type != "select":
                    raise LeaveError("Seçenekler yalnız seçim alanında kullanılabilir.")
                secenekler = veri["options"]
                if isinstance(secenekler, str):
                    secenekler = [x.strip() for x in secenekler.splitlines() if x.strip()]
                secenekler = list(dict.fromkeys(str(x).strip() for x in secenekler if str(x).strip()))
                if not secenekler:
                    raise LeaveError("Seçim alanında en az bir seçenek kalmalıdır.")
                alan.options = secenekler
            alan.save(update_fields=["label_override", "is_required", "is_visible", "is_public", "helper_text", "options", "layout_width", "deleted_at", "updated_at"])
        except (LeaveError, TestUiAuthorizationError, ValueError) as hata:
            return TestUiErrors.cevap(hata)
        return Response({"data": _form_alani_yanit(alan)})


class EmployeeFormFieldDeleteView(TestUiView):
    def post(self, request, field_id: str):  # noqa: ANN001
        try:
            if not user_has_permission(request.user, "employee.form.manage"):
                raise TestUiAuthorizationError("Çalışan formu alanlarını yönetme izniniz yok.")
            alan = FormFieldDefinition.objects.filter(
                pk=_uuid(field_id, "field_id"), tenant_id=request.user.tenant_id,
                form_code="employee_onboarding", field_code__startswith="custom_",
            ).first()
            if alan is None:
                raise LeaveError("Özel form alanı bulunamadı.")
            alan.deleted_at = timezone.now()
            alan.save(update_fields=["deleted_at", "updated_at"])
        except (LeaveError, TestUiAuthorizationError, ValueError) as hata:
            return TestUiErrors.cevap(hata)
        return Response({"data": {"deleted": True}})


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
        arama = str(request.query_params.get("q") or "").strip()
        # Kapsamı yalnızca **kendi kaydı** olan kullanıcı (ör. `employee`) şirket
        # dizinini görür: PII'siz kimlik bilgisi + unvan + birim ve kendi kaydı tam.
        # Böylece organizasyon şeması görünür ama başkalarının izin/bakiye detayları
        # görünmez (docs/ORGANIZATION.md §5 "kişisel veri içermez" ilkesi).
        if not kapsam.tenant_wide and not kapsam.unit_ids:
            # Yalnız kendi kaydı (bildirim: "normal bir çalışan sadece kendini
            # görsün; diğer çalışanları ve numaralarını görmesine gerek yok").
            kayitlar = (
                [{**_calisan_ozeti(kendi), "kendi_kaydim": True}]
                if kendi is not None
                and (
                    request.query_params.get("include_departed") == "1"
                    or kendi.status in ("active", "suspended")
                )
                else []
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
        calisan_sorgusu = kapsamdaki_calisanlar(request.user)
        if request.query_params.get("for_leave") == "1":
            # İzin formu organizasyon kapsamındaki tüm çalışanları değil,
            # yalnızca aktörün kendisini ve aktif birincil bağlılarını gösterir.
            calisan_sorgusu = calisan_sorgusu.filter(
                pk__in=direct_reports(request.user), status="active"
            )
        if request.query_params.get("include_departed") != "1":
            calisan_sorgusu = calisan_sorgusu.filter(
                status__in=("active", "suspended")
            )
        if arama:
            filtre = (
                Q(employee_no__icontains=arama)
                | Q(department__name__icontains=arama)
                | Q(position__name__icontains=arama)
                | Q(user__user_roles__role__name__icontains=arama)
            )
            # Ad/e-posta yalnızca hassas veri okuma yetkisi olan kapsamlı
            # kullanıcılarda arama alanına girer; yetkisiz kullanıcıya PII
            # üzerinden sonuç sızdırılmaz.
            if user_has_permission(request.user, "employee.sensitive.read"):
                filtre |= (
                    Q(user__first_name__icontains=arama)
                    | Q(user__last_name__icontains=arama)
                    | Q(user__email__icontains=arama)
                )
            calisan_sorgusu = calisan_sorgusu.filter(filtre).distinct()
        calisanlar = list(calisan_sorgusu)
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
                    "query": arama,
                    "total": len(calisanlar),
                },
            }
        )

    @transaction.atomic
    def post(self, request):  # noqa: ANN001
        """
        Yeni çalışan + **zorunlu giriş hesabı** — tek atomik işlemde.

        İş kuralı (bildirim): giriş hesabı opsiyonel değildir. Zorunlu alanlar:
        `email`, `password`, `first_name`, `last_name`, `department_id`,
        `hire_date`. Hesap açılamazsa **yarım çalışan kaydı bırakılmaz**:
        sarmalayıcı `transaction.atomic` sayesinde kullanıcı + çalışan + kişisel
        veri ya tümü yazılır ya hiçbiri. Parola hiçbir koşulda loglanmaz/denetim
        kaydına yazılmaz.
        """
        veri = request.data
        try:
            # Kayıt işlemleri **savepoint** içinde: bir hata yakalandığında
            # (aşağıdaki `except`) yarım kullanıcı/çalışan kaydı KALMAZ.
            with transaction.atomic():
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
                # Giriş hesabı **zorunludur** (bildirim: "giriş hesabı opsiyonel olmasın"):
                # kurumsal e-posta + parola + ad + soyad olmadan çalışan açılmaz.
                eksik = [
                    etiket
                    for alan, etiket in (
                        ("email", "kurumsal e-posta"),
                        ("password", "parola"),
                        ("first_name", "ad"),
                        ("last_name", "soyad"),
                    )
                    if not str(veri.get(alan) or "").strip()
                ]
                if eksik:
                    raise LeaveError(
                        "Giriş hesabı zorunludur; eksik alanlar: " + ", ".join(eksik) + "."
                    )
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
                        raise LeaveError(
                            "Seçilen yönetici bulunamadı (veya bu kiracıya ait değil)."
                        )
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
                _zorunlu_kisisel_form_alanlarini_dogrula(request.user.tenant_id, veri)
                custom_fields = veri.get("custom_fields")
                custom_values = _dogrula_ozel_form_degerleri(
                    request.user, request.user.tenant_id, custom_fields
                )
                if custom_values:
                    pii["custom_fields"] = custom_values
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


class EmployeeLifecycleView(TestUiView):
    """
    Çalışan yaşam döngüsü işlemleri — **merkezi lifecycle servisi** üzerinden (P1-B).

    * POST `/employees/<id>/suspend`   → suspend_employee (`employee.suspend`)
    * POST `/employees/<id>/restore`   → restore_employee (`employee.restore`)
    * POST `/employees/<id>/terminate` → terminate_employee (`employee.terminate`)

    Gerekçe zorunludur; geçersiz geçiş 409, izin/kapsam reddi 403 döner.
    """

    ISLEM_IZIN = {
        "suspend": "employee.suspend",
        "restore": "employee.restore",
        "terminate": "employee.terminate",
    }

    def post(self, request, employee_id: str, islem: str):  # noqa: ANN001
        if islem not in self.ISLEM_IZIN:
            return Response(
                {"error": {"code": "not_found", "message": "Bilinmeyen işlem.", "detail": ""}},
                status=status.HTTP_404_NOT_FOUND,
            )
        try:
            calisan = _kapsamdaki_calisan(request.user, employee_id)
        except Http404:
            raise
        try:
            if islem == "suspend":
                sonuc = suspend_employee(
                    actor=request.user,
                    employee=calisan,
                    reason=str(request.data.get("reason") or ""),
                )
            elif islem == "restore":
                sonuc = restore_employee(
                    actor=request.user,
                    employee=calisan,
                    reason=str(request.data.get("reason") or ""),
                )
            elif islem == "terminate":
                sonuc = terminate_employee(
                    actor=request.user,
                    employee=calisan,
                    termination_date=_tarih(
                        request.data.get("termination_date"), "termination_date"
                    ),
                    termination_reason_code=str(
                        request.data.get("termination_reason_code") or ""
                    ).strip(),
                    reason=str(request.data.get("reason") or ""),
                    termination_note=str(request.data.get("termination_note") or ""),
                )
            else:
                raise LifecycleStateError("Arşivleme bu çalışan akışında kullanılmıyor.")
        except (
            LifecycleStateError,
            LifecycleValidationError,
            OrganizationError,
            OrganizationAuthorizationError,
            LeaveError,
        ) as hata:
            return TestUiErrors.cevap(hata)
        return Response({"data": _calisan_ozeti(sonuc)})


class EmployeeHistoryView(TestUiView):
    """
    `Yaşananlar` — lazy partial (P1-B sonrası performans paketi).

    * Sayfalı: `?page=N` (varsayılan 1; sayfa 20 olay) — tüm geçmiş tek seferde
      yüklenmez.
    * Sensitive alan maskesi (§11): timeline olay detayları K0/K1 değerlerini
      açıkça içerir; K2 değerleri **maskeli**, K3/K4 değerleri **asla** dönmez.
      Tam/maskesiz değer yalnız `employee.sensitive.read` ∧ kapsam sahibine
      döner — frontend masking güvenlik kontrolü değildir; karar backend'dedir.
    * İzin hakkı/bakiye olaylarının **değerleri** (değişim, gerekçe, işlemi yapan)
      yalnız `leave.balance.read` ∧ kapsam sahibine döner; aksi halde maskelenir
      (§5: izin yoksa hassas değer yanıta konmaz).
    * Çalışan **kendi** timeline'ını görebilir: `employee.history.read` yoksa yalnız
      kendi çalışan kaydı için okuma açılır (kapsam `_kapsamdaki_calisan` ile).
      Başka çalışanın kaydı izinsiz okunamaz.
    * Scope dışı çalışan → 404 (varlık sızdırılmaz).
    """

    #: K2 sınıfı değerler için maskeleme (timeline detayında uygulanır).
    MASKED = "***"
    #: Bakiye değeri taşıyan olay tipleri (`leave.balance.read` kapısı).
    BAKIYE_TIPLERI = ("leave_entitlement", "leave_balance")

    def get(self, request, employee_id: str):  # noqa: ANN001
        try:
            calisan = _kapsamdaki_calisan(request.user, employee_id)
        except Http404:
            raise

        # §5: çalışan kendi izin/timeline olaylarını görebilir; başkasının kaydı
        # için `employee.history.read` gerekir (karar backend'de).
        kendi = user_employee(request.user)
        if not user_has_permission(request.user, "employee.history.read") and not (
            kendi is not None and kendi.pk == calisan.pk
        ):
            return TestUiErrors.cevap(
                TestUiAuthorizationError(
                    "Geçmişi görmek için 'employee.history.read' izni gerekir."
                )
            )

        page = _sayfa_no(request)
        tum_olaylar = employee_history(calisan)
        olaylar, toplam_sayfa = timeline_sayfa(tum_olaylar, page=page)

        # K2 alanlar: maskesiz görüm yalnız `employee.sensitive.read` ∧ kapsam.
        sensitive_ok = user_has_permission(request.user, "employee.sensitive.read") and (
            object_in_org_scope(request.user, department_id=calisan.department_id)
        )
        if sensitive_ok:
            for olay in olaylar:
                detay = olay.get("detail") or {}
                if isinstance(detay.get("Değişiklikler"), dict):
                    detay["Değişiklikler"] = _audit_pii_degerlerini_coz(
                        detay["Değişiklikler"]
                    )
        if not sensitive_ok:
            for olay in olaylar:
                detay = olay.get("detail") or {}
                for anahtar in list(detay.keys()):
                    if any(
                        k in anahtar
                        for k in ("iban", "phone", "address", "national", "emergency", "bank")
                    ):
                        detay[anahtar] = self.MASKED
                if isinstance(detay.get("Değişiklikler"), dict):
                    detay["Değişiklikler"] = {
                        alan: {"before": self.MASKED, "after": self.MASKED}
                        for alan in detay["Değişiklikler"]
                    }

        # İzin hakkı/bakiye değerleri: `leave.balance.read` ∧ kapsam yoksa gizlenir.
        bakiye_ok = _bakiye_degerleri_gorunur(request.user, calisan)
        if not bakiye_ok:
            for olay in olaylar:
                # Karar olayındaki "Karar sonrası kalan" (snapshot) değeri de
                # yetkisiz yanıta **konmaz** (§6).
                olay.pop("karar_sonrasi_kalan", None)
                olay.pop("karar_sonrasi_kalan_snapshot_at", None)
                if olay.get("type") not in self.BAKIYE_TIPLERI:
                    continue
                detay = olay.get("detail") or {}
                for anahtar in list(detay.keys()):
                    detay[anahtar] = self.MASKED
                olay["summary"] = self.MASKED

        return Response(
            {
                "data": {
                    "employee_no": calisan.employee_no,
                    "events": olaylar,
                    "page": page,
                    "total_pages": toplam_sayfa,
                    "sensitive_visible": sensitive_ok,
                }
            }
        )


class EmployeeAssignmentChangeView(TestUiView):
    """Departman/pozisyon değişikliği — atama geçmişi yazar (OD60; `employee.update`)."""

    def post(self, request, employee_id: str):  # noqa: ANN001
        try:
            calisan = _kapsamdaki_calisan(request.user, employee_id)
        except Http404:
            raise
        birim = pozisyon = None
        try:
            if request.data.get("department_id"):
                birim = Department.objects.get(
                    pk=_uuid(request.data["department_id"], "department_id"),
                    tenant_id=request.user.tenant_id,
                    deleted_at__isnull=True,
                )
            if request.data.get("position_id"):
                pozisyon = Position.objects.get(
                    pk=_uuid(request.data["position_id"], "position_id"),
                    tenant_id=request.user.tenant_id,
                    deleted_at__isnull=True,
                )
            efektif = (
                _tarih(request.data.get("effective_from"), "effective_from")
                if request.data.get("effective_from")
                else None
            )
            _, ozet = change_assignment(
                actor=request.user,
                employee=calisan,
                department=birim,
                position=pozisyon,
                effective_from=efektif,
                reason=str(request.data.get("reason") or ""),
            )
        except (Department.DoesNotExist, Position.DoesNotExist):
            hata_mesaj = LeaveError(
                "Hedef birim/pozisyon bulunamadı (veya başka kiracıya ait)."
            )
            return TestUiErrors.cevap(hata_mesaj)
        except (
            LifecycleValidationError,
            OrganizationError,
            OrganizationAuthorizationError,
            LeaveError,
        ) as hata:
            return TestUiErrors.cevap(hata)
        return Response({"data": ozet})


class EmployeeTermsChangeView(TestUiView):
    """İstihdam koşulu değişikliği — yeni koşul satırı (OD60; `employee.update`)."""

    def post(self, request, employee_id: str):  # noqa: ANN001
        try:
            calisan = _kapsamdaki_calisan(request.user, employee_id)
        except Http404:
            raise
        isyeri = None
        try:
            if request.data.get("workplace_id"):
                from apps.organization.models import Workplace  # noqa: PLC0415

                isyeri = Workplace.objects.get(
                    pk=_uuid(request.data["workplace_id"], "workplace_id"),
                    tenant_id=request.user.tenant_id,
                    deleted_at__isnull=True,
                )
            efektif = (
                _tarih(request.data.get("effective_from"), "effective_from")
                if request.data.get("effective_from")
                else None
            )
            _, ozet = change_employment_terms(
                actor=request.user,
                employee=calisan,
                employment_type=request.data.get("employment_type"),
                working_model=request.data.get("working_model"),
                workplace=isyeri,
                contract_type=request.data.get("contract_type"),
                contract_start_date=(
                    _tarih(request.data["contract_start_date"], "contract_start_date")
                    if request.data.get("contract_start_date")
                    else None
                ),
                contract_end_date=(
                    _tarih(request.data["contract_end_date"], "contract_end_date")
                    if request.data.get("contract_end_date")
                    else None
                ),
                probation_end_date=(
                    _tarih(request.data["probation_end_date"], "probation_end_date")
                    if request.data.get("probation_end_date")
                    else None
                ),
                effective_from=efektif,
                reason=str(request.data.get("reason") or ""),
            )
        except (
            LifecycleValidationError,
            OrganizationError,
            OrganizationAuthorizationError,
            LeaveError,
        ) as hata:
            return TestUiErrors.cevap(hata)
        return Response({"data": ozet})


class EmployeeManagerChangeView(TestUiView):
    """Birincil yönetici değişikliği (mevcut altyapı; `employee.manager.change`)."""

    def post(self, request, employee_id: str):  # noqa: ANN001
        try:
            calisan = _kapsamdaki_calisan(request.user, employee_id)
        except Http404:
            raise
        try:
            yonetici = Employee.objects.get(
                pk=_uuid(request.data.get("manager_employee_id"), "manager_employee_id"),
                tenant_id=request.user.tenant_id,
                deleted_at__isnull=True,
            )
            iliski = change_manager(
                actor=request.user,
                employee=calisan,
                manager_employee=yonetici,
                effective_from=(
                    _tarih(request.data["effective_from"], "effective_from")
                    if request.data.get("effective_from")
                    else None
                ),
                reason=str(request.data.get("reason") or ""),
            )
        except Employee.DoesNotExist:
            hata_mesaj = LeaveError(
                "Hedef yönetici bulunamadı (veya başka kiracıya ait)."
            )
            return TestUiErrors.cevap(hata_mesaj)
        except (
            LifecycleValidationError,
            OrganizationError,
            OrganizationAuthorizationError,
            LeaveError,
        ) as hata:
            return TestUiErrors.cevap(hata)
        return Response(
            {"data": {"effective_from": iliski.effective_from.isoformat()}}
        )


class EmployeeMoveView(TestUiView):

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


class BalanceListView(TestUiView):
    """
    **İzin Bakiyeleri** ekranı: kapsamdaki çalışanların dönem bakiyeleri.

    Kaynak `leave_balances` (dönem + tür); türetilen "kalan" `available_days`
    üretilen kolonundan gelir — arayüz hesap yapmaz. Filtreler **sunucuda**
    uygulanır (kapsam dışına çıkılamaz).
    """

    def get(self, request):  # noqa: ANN001
        if not user_has_permission(request.user, "leave.balance.read"):
            return TestUiErrors.cevap(
                LeaveAuthorizationError("İzin bakiyelerini görmek için yetki/kapsam gerekir.")
            )
        yil = period_year_for()
        arama = str(request.query_params.get("q") or "").strip().lower()
        departman = str(request.query_params.get("department_id") or "").strip()
        tur = str(request.query_params.get("leave_type_id") or "").strip()
        durum = str(request.query_params.get("durum") or "").strip()

        satirlar: list[dict] = []
        for calisan in kapsamdaki_calisanlar(request.user):
            etiket = _calisan_etiketi(calisan)
            birim = calisan.department.name if calisan.department_id else None
            if departman and str(calisan.department_id) != departman:
                continue
            if arama and arama not in " ".join(
                filter(
                    None,
                    [
                        calisan.employee_no,
                        etiket,
                        birim,
                        calisan.user.email if calisan.user_id else "",
                    ],
                )
            ).lower():
                continue
            bakiyeler = _bakiye_listesi(calisan, yil)
            if tur:
                bakiyeler = [b for b in bakiyeler if b["leave_type_id"] == tur]
            if durum in ("dusuk", "tukendi"):
                # "tukendi" → kalan ≤ 0; "dusuk" → kalan ≤ 3 gün (eşik sunucuda).
                esik = Decimal("0.0") if durum == "tukendi" else Decimal("3.0")
                bakiyeler = [b for b in bakiyeler if Decimal(b["available_days"]) <= esik]
            yonetilebilir = can_manage_balances(request.user, calisan)
            for bakiye in bakiyeler:
                satirlar.append(
                    {
                        "employee_id": str(calisan.pk),
                        "employee_no": calisan.employee_no,
                        "employee": etiket,
                        "user_email": calisan.user.email if calisan.user_id else None,
                        "department": birim,
                        "department_id": (
                            str(calisan.department_id) if calisan.department_id else None
                        ),
                        "kendi_kaydim": calisan.user_id is not None
                        and str(calisan.user_id) == str(request.user.pk),
                        "can_adjust": yonetilebilir,
                        **bakiye,
                    }
                )
        return Response(
            {
                "data": satirlar,
                "meta": {
                    "period_year": yil,
                    "can_manage": user_has_permission(request.user, "leave.balance.manage"),
                    "kayit_sayisi": len(satirlar),
                    "self_only": not (
                        user_org_scope(request.user).tenant_wide
                        or user_org_scope(request.user).unit_ids
                    ),
                },
            }
        )


class BalanceAdjustView(TestUiView):
    """
    Bakiye düzeltmesi / hakediş tanımlama — **servis katmanı** üzerinden.

    Arayüz doğrudan bakiye yazmaz: `adjust_leave_balance` (gerekçe zorunlu,
    defter kaydı + denetim) veya `accrue_leave_entitlements` (idempotent)
    çağrılır; ikisi de `leave.balance.manage` ∧ kapsam ister.
    """

    def post(self, request):  # noqa: ANN001
        veri = request.data
        islem = str(veri.get("islem") or "duzeltme")
        try:
            calisan = _kapsamdaki_calisan(request.user, veri.get("employee_id"))
        except Http404:
            raise
        yil = period_year_for()
        if islem == "hakedis":
            from apps.leave.services import accrue_leave_entitlements  # noqa: PLC0415

            try:
                sonuclar = accrue_leave_entitlements(
                    actor=request.user,
                    tenant=request.user.tenant,
                    period_year=yil,
                    employees=[calisan],
                )
            except (LeaveError, LeaveAuthorizationError) as hata:
                return TestUiErrors.cevap(hata)
            return Response(
                {
                    "data": _bakiye_listesi(calisan, yil),
                    "meta": {
                        "islem": "hakedis",
                        "sonuclar": sonuclar,
                        "employee_no": calisan.employee_no,
                        "period_year": yil,
                    },
                }
            )
        try:
            tur = LeaveType.objects.filter(
                pk=_uuid(veri.get("leave_type_id"), "leave_type_id"),
                tenant_id=request.user.tenant_id,
                deleted_at__isnull=True,
            ).first()
            if tur is None:
                raise LeaveError("İzin türü seçilmelidir.")
            try:
                delta = Decimal(str(veri.get("delta_days")))
            except (InvalidOperation, TypeError) as hata:
                raise LeaveError("Miktar sayı olmalıdır (ör. 2.0 veya -1.5).") from hata
            bakiye = adjust_leave_balance(
                actor=request.user,
                employee=calisan,
                leave_type=tur,
                period_year=int(veri.get("period_year") or yil),
                delta_days=delta,
                reason=str(veri.get("reason") or ""),
            )
        except (LeaveError, LeaveAuthorizationError) as hata:
            return TestUiErrors.cevap(hata)
        return Response(
            {
                "data": _bakiye_ozeti(bakiye),
                "meta": {"islem": "duzeltme", "employee_no": calisan.employee_no},
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
                    "hierarchy_level": rol.hierarchy_level,
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
        "is_time_based": tur.is_time_based,
        "workday_minutes": tur.workday_minutes,
        "workday_hours": (
            str((Decimal(tur.workday_minutes) / Decimal("60")).normalize())
            if tur.is_time_based and tur.workday_minutes else None
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
                is_time_based=bool(veri.get("is_time_based", False)),
                workday_minutes=(int(veri["workday_minutes"]) if veri.get("workday_minutes") not in (None, "") else None),
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


class LeaveTypeActivateView(TestUiView):
    """Pasif izin türünü yeniden aktifleştirir."""

    def post(self, request, leave_type_id: str):  # noqa: ANN001
        tur = LeaveType.objects.filter(
            pk=_uuid(leave_type_id, "leave_type_id"), deleted_at__isnull=True
        ).first()
        if tur is None:
            raise Http404("izin_turu_yok")
        try:
            activate_leave_type(
                actor=request.user, leave_type=tur, reason=str(request.data.get("reason") or "")
            )
        except Exception as hata:  # noqa: BLE001
            return TestUiErrors.cevap(hata)
        return Response({"data": _izin_turu_ozeti(tur)})


class LeaveTypeUpdateView(TestUiView):
    """İzin türü ayarlarını, özellikle saat tabanlı gün uzunluğunu günceller."""

    def post(self, request, leave_type_id: str):  # noqa: ANN001
        tur = LeaveType.objects.filter(pk=_uuid(leave_type_id, "leave_type_id"), deleted_at__isnull=True).first()
        if tur is None:
            raise Http404("izin_turu_yok")
        try:
            update_leave_type(
                actor=request.user, leave_type=tur,
                is_time_based=(bool(request.data["is_time_based"]) if "is_time_based" in request.data else None),
                workday_minutes=(int(request.data["workday_minutes"]) if request.data.get("workday_minutes") not in (None, "") else None),
                workday_hours=(request.data.get("workday_hours") if request.data.get("workday_hours") not in (None, "") else None),
            )
        except (TypeError, ValueError, LeaveError, LeaveAuthorizationError) as hata:
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
                hierarchy_level=(int(request.data["hierarchy_level"]) if request.data.get("hierarchy_level") not in (None, "") else 30),
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
                    "hierarchy_level": rol.hierarchy_level,
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
                hierarchy_level=(int(request.data["hierarchy_level"]) if request.data.get("hierarchy_level") not in (None, "") else None),
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
                    "aciklama": izin_aciklamasi(izin.code),
                    "secili": izin.code in secili,
                    "aktor_sahip": izin.code in aktor_izinleri,
                    # Kilit nedeni **açıkça** döner (bildirim: "checkbox disabled
                    # ise nedenini belirle"). Kural (docs/AUTHORIZATION.md §4.3):
                    # aktör **sahip olmadığı** izni veremez ama **kaldırabilir**.
                    # Bu yüzden yalnızca "ekleme" yönü kilitlidir; zaten seçili
                    # izinler her zaman kaldırılabilir.
                    "ekleme_kilitli": izin.code not in aktor_izinleri,
                    "kaldirma_kilitli": False,
                    "kilit_nedeni": (
                        None
                        if izin.code in aktor_izinleri
                        else "Yetkiniz yok: yalnızca kendinizde olan yetkileri "
                        "verebilirsiniz (kaldırma serbesttir)."
                    ),
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
                    # Neden düzenlenemediği **açıkça** bildirilir (keyfi UI sınırı yok).
                    "can_edit_reason": (
                        None
                        if user_has_permission(request.user, "role.manage")
                        else "Rol düzenlemek için 'role.manage' izni gerekir."
                    )
                    or (
                        "Kendi rolünüzü düzenleyemezsiniz (yetki yükseltme yasağı)."
                        if UserRole.objects.filter(
                            user=request.user, role=rol, deleted_at__isnull=True
                        ).exists()
                        else None
                    ),
                    "platform_izinleri_listelenmez": True,
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
                            "is_time_based": t.is_time_based,
                            "workday_minutes": t.workday_minutes,
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
                    "employee_form_fields": _calisan_form_alani_ozet(request.user),
                    "employee_form_fields_admin": (
                        _calisan_form_yonetim_ozeti(request.user)
                        if user_has_permission(request.user, "employee.form.manage")
                        else []
                    ),
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
                    "can_manage_employee_form": user_has_permission(
                        request.user, "employee.form.manage"
                    ),
                },
            }
        )


# --- Çalışan formu özel alanları -------------------------------------------

OZEL_FORM_TIPLERI = {"text", "number", "date", "select"}


def _calisan_form_alani_ozet(user) -> list[dict]:  # noqa: ANN001
    """Yeni çalışan ekranında kullanılabilecek aktif özel alan tanımları."""
    try:
        # tenant_context açık bir transaction tuttuğu için hata doğrudan
        # yakalanırsa transaction "aborted" durumda kalır. İç atomic blok,
        # sorgu hatasını savepoint'e geri alır ve middleware çıkışını bozmaz.
        with transaction.atomic():
            return [
                {
                    "id": str(alan.pk),
                    "code": alan.field_code,
                    "label": alan.label_override or alan.field_code.removeprefix("custom_").replace("_", " ").title(),
                    "field_type": alan.field_type,
                    "options": alan.options if alan.field_type == "select" else [],
                    "layout_width": alan.layout_width,
                    "is_required": alan.is_required,
                    "is_visible": alan.is_visible,
                    "is_custom": alan.field_code.startswith("custom_"),
                    "section_label": alan.section_label,
                    "helper_text": alan.helper_text,
                }
                for alan in FormFieldDefinition.objects.filter(
                    tenant_id=user.tenant_id,
                    form_code="employee_onboarding",
                    field_code__startswith="custom_",
                    deleted_at__isnull=True,
                    is_visible=True,
                ).order_by("sort_order", "created_at")
            ]
    except DatabaseError:
        # Migration uygulanırken eski veritabanı ile arayüz tamamen kilitlenmesin;
        # özel alanlar geçici olarak boş görünür, standart çalışan sekmesi açılır.
        return []


def _calisan_form_yonetim_ozeti(user) -> list[dict]:  # noqa: ANN001
    """Yönetici listesi: katalog + özel alanlar, aktif ve pasif kayıtlar."""
    try:
        with transaction.atomic():
            return [
                _form_alani_yanit(alan)
                for alan in FormFieldDefinition.objects.filter(
                    tenant_id=user.tenant_id,
                    form_code="employee_onboarding",
                ).filter(
                    Q(field_code__in=(*PII_ALANLARI, *K3_ALANLARI))
                    | Q(field_code__startswith="custom_")
                ).filter(Q(deleted_at__isnull=True) | ~Q(field_code__startswith="custom_")).order_by(
                    "sort_order", "created_at"
                )
            ]
    except DatabaseError:
        return []


def _aktif_ozel_form_alanlari(tenant_id):  # noqa: ANN001
    return list(
        FormFieldDefinition.objects.filter(
            tenant_id=tenant_id,
            form_code="employee_onboarding",
            field_code__startswith="custom_",
            deleted_at__isnull=True,
            is_visible=True,
        ).order_by("sort_order", "created_at")
    )


def _dogrula_ozel_form_degerleri(
    user, tenant_id, values, *, enforce_required: bool = True
) -> dict:  # noqa: ANN001
    """Özel alanları tanıma, tip, seçenek ve zorunluluk açısından doğrular."""
    if values in (None, ""):
        values = {}
    if not isinstance(values, dict):
        raise LeaveError("custom_fields bir nesne olmalıdır.")
    if values and not user_has_permission(user, "employee.sensitive.read"):
        raise TestUiAuthorizationError("Özel çalışan bilgilerini kaydetmek için hassas veri izni gerekir.")
    alanlar = _aktif_ozel_form_alanlari(tenant_id)
    tanimli = {alan.field_code: alan for alan in alanlar}
    bilinmeyen = set(values) - set(tanimli)
    if bilinmeyen:
        raise LeaveError("Tanımsız veya pasif özel form alanı gönderildi.")
    sonuc = {}
    for alan in alanlar:
        ham = values.get(alan.field_code, "")
        bos = ham is None or (isinstance(ham, str) and not ham.strip())
        if enforce_required and alan.is_required and bos:
            raise LeaveError(f"'{alan.label_override or alan.field_code}' alanı zorunludur.")
        if bos:
            continue
        if alan.field_type == "select":
            if not isinstance(ham, str) or ham not in (alan.options or []):
                raise LeaveError(f"'{alan.label_override or alan.field_code}' için geçersiz seçenek.")
            sonuc[alan.field_code] = ham.strip()
        elif alan.field_type == "number":
            try:
                sayi = Decimal(str(ham).strip())
            except (InvalidOperation, ValueError):
                raise LeaveError(f"'{alan.label_override or alan.field_code}' sayı olmalıdır.") from None
            sonuc[alan.field_code] = str(sayi)
        elif alan.field_type == "date":
            sonuc[alan.field_code] = _tarih(str(ham).strip(), alan.field_code).isoformat()
        else:
            sonuc[alan.field_code] = str(ham).strip()
    return sonuc


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


def _zorunlu_kisisel_form_alanlarini_dogrula(tenant_id, values) -> None:  # noqa: ANN001
    """Yeni çalışan kaydında zorunlu yapılan katalog alanlarını kontrol eder."""
    alanlar = FormFieldDefinition.objects.filter(
        tenant_id=tenant_id,
        form_code="employee_onboarding",
        field_code__in=(*PII_ALANLARI, *K3_ALANLARI),
        deleted_at__isnull=True,
        is_visible=True,
        is_required=True,
    )
    for alan in alanlar:
        deger = values.get(alan.field_code)
        if deger is None or (isinstance(deger, str) and not deger.strip()):
            raise LeaveError(f"'{_form_alani_yanit(alan)['label']}' alanı zorunludur.")

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
        "custom_fields": kayit.custom_fields or {},
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
    eski_degerler = {}
    eski_degerler["custom_fields"] = dict(getattr(kayit, "custom_fields", {}) or {})
    for alan in (*PII_ALANLARI, *K3_ALANLARI):
        if alan in veri and kayit is not None:
            eski_degerler[alan] = _sifreli_oku(kayit, alan) if alan in K3_ALANLARI else getattr(kayit, alan, None)
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
    if "custom_fields" in veri:
        kayit.custom_fields = dict(veri["custom_fields"] or {})
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
            "ozel_alanlar": sorted((veri.get("custom_fields") or {}).keys()),
            "alan_degisiklikleri": _pii_degisiklikleri(eski_degerler, kayit, veri),
        },
    )


def _pii_degisiklikleri(eski_degerler: dict, kayit, veri) -> dict:  # noqa: ANN001
    """Timeline için kişisel alan değişimlerinin eski/yeni güvenli metni."""
    sonuc = {}
    etiketler = {
        "first_name": "Ad", "last_name": "Soyad",
        "personal_phone": "Telefon numarası", "personal_email": "Kişisel e-posta",
        "address": "Adres", "national_id": "Kimlik numarası", "citizenship": "Vatandaşlık",
        "bank_name": "Banka", "iban": "IBAN", "birth_date": "Doğum tarihi",
        "emergency_contact_phone": "Acil durum telefonu",
        "emergency_contact_relation": "Acil durum yakını", "work_permit_type": "Çalışma izni",
        "work_permit_valid_until": "Çalışma izni geçerlilik tarihi",
    }
    for alan in PII_ALANLARI:
        if alan not in veri:
            continue
        eski = eski_degerler.get(alan)
        yeni = getattr(kayit, alan, None)
        if hasattr(eski, "isoformat"):
            eski = eski.isoformat()
        if hasattr(yeni, "isoformat"):
            yeni = yeni.isoformat()
        eski, yeni = ("" if eski is None else str(eski)), ("" if yeni is None else str(yeni))
        if eski != yeni:
            sonuc[etiketler.get(alan, alan)] = {
                "before": _audit_pii_degeri(eski),
                "after": _audit_pii_degeri(yeni),
            }
    # K3 değerleri kişisel veri denetim kaydına yazılmaz; ancak alanın
    # güncellendiği, eski/yeni değerler gizlenerek zaman çizelgesinde görünür.
    for alan in K3_ALANLARI:
        if alan not in veri:
            continue
        eski = eski_degerler.get(alan)
        yeni = _sifreli_oku(kayit, alan)
        eski, yeni = ("" if eski is None else str(eski)), ("" if yeni is None else str(yeni))
        if eski != yeni:
            sonuc["Kan grubu" if alan == "blood_type" else alan] = {
                "before": "[gizli]", "after": "[gizli]"
            }
    eski_ozel = eski_degerler.get("custom_fields", {})
    yeni_ozel = getattr(kayit, "custom_fields", {}) or {}
    ozel_etiketler = {
        alan.field_code: (alan.label_override or alan.field_code)
        for alan in FormFieldDefinition.objects.filter(
            tenant_id=kayit.tenant_id,
            field_code__in=set(eski_ozel) | set(yeni_ozel),
        )
    }
    for kod in set(eski_ozel) | set(yeni_ozel):
        if eski_ozel.get(kod) != yeni_ozel.get(kod):
            sonuc[ozel_etiketler.get(kod, kod)] = {
                "before": _audit_pii_degeri(eski_ozel.get(kod)),
                "after": _audit_pii_degeri(yeni_ozel.get(kod)),
            }
    return sonuc


def _audit_pii_degeri(deger) -> dict:  # noqa: ANN001
    """PII değişim değerini audit tablosunda açık metin bırakmadan saklar."""
    metin = "" if deger is None else str(deger)
    return {"__encrypted__": encrypt_str(metin)}


def _audit_pii_degerlerini_coz(deger):  # noqa: ANN001
    """Yetkili timeline yanıtı için şifreli değişim değerlerini çözer."""
    if isinstance(deger, dict):
        if set(deger) == {"__encrypted__"}:
            try:
                return decrypt_str(str(deger["__encrypted__"]))
            except SecretDecryptionError:
                return "[çözülemedi]"
        return {anahtar: _audit_pii_degerlerini_coz(deger2) for anahtar, deger2 in deger.items()}
    if isinstance(deger, list):
        return [_audit_pii_degerlerini_coz(deger2) for deger2 in deger]
    return deger


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
            veri = dict(request.data)
            if "custom_fields" in veri:
                veri["custom_fields"] = _dogrula_ozel_form_degerleri(
                    request.user,
                    request.user.tenant_id,
                    veri["custom_fields"],
                    enforce_required=False,
                )
            _pii_yaz(request.user, calisan, veri)
            # Kişisel formdaki ad/soyad, çalışan listesinin kaynak hesabında da
            # tutulur. Böylece detaydan yapılan değişiklik listeye anında yansır.
            if calisan.user_id and any(alan in veri for alan in ("first_name", "last_name")):
                if "first_name" in veri:
                    calisan.user.first_name = str(veri.get("first_name") or "").strip()
                if "last_name" in veri:
                    calisan.user.last_name = str(veri.get("last_name") or "").strip()
                calisan.user.save(update_fields=["first_name", "last_name", "updated_at"])
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
            # İzin hakları bölümü: bakiye düzenleme yetkisi/kapsamı (karar backend'de).
            "bakiye_okunabilir": user_has_permission(request.user, "leave.balance.read"),
            "bakiye_yonetilebilir": can_manage_balances(request.user, calisan),
            # İzin hakları: bakiye kaydı OLMAYAN türler de satır olarak döner
            # (bildirim: yeni çalışanda "hak tanımla" hiç görünmüyordu).
            "izin_haklari": (
                _izin_haklari_listesi(calisan, yil)
                if user_has_permission(request.user, "leave.balance.read")
                else []
            ),
            "sonraki_hakedis": next_accrual_date(calisan.hire_date).isoformat(),
            "sonraki_hakedis_metin": next_accrual_date(calisan.hire_date).strftime("%d.%m.%Y"),
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
            "assignable_roles": [],
            # P1-B: yaşam döngüsü türetilmiş değerleri + aksiyon yetkileri.
            "lifecycle": _lifecycle_ozeti(request.user, calisan),
            "can_reset_password": bool(
                calisan.user_id and user_has_permission(request.user, "user.manage")
            ),
            "can_update_email": bool(
                calisan.user_id and user_has_permission(request.user, "user.manage")
            ),
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


class EmployeePasswordResetView(TestUiView):
    """Yetkili yönetici, kapsamındaki çalışan hesabına yeni parola belirler."""

    def post(self, request, employee_id: str):  # noqa: ANN001
        try:
            if not user_has_permission(request.user, "user.manage"):
                raise TestUiAuthorizationError("Parola sıfırlamak için 'user.manage' izni gerekir.")
            calisan = _kapsamdaki_calisan(request.user, employee_id)
            if calisan.user_id is None:
                raise LeaveError("Bu çalışana bağlı bir giriş hesabı yok.")
            parola = str(request.data.get("password") or "")
            if not parola:
                raise LeaveError("Yeni parola zorunludur.")
            from django.contrib.auth.password_validation import validate_password  # noqa: PLC0415
            from django.core.exceptions import ValidationError  # noqa: PLC0415
            try:
                validate_password(parola, user=calisan.user)
            except ValidationError as hata:
                raise LeaveError("Parola kuralları: " + " ".join(hata.messages)) from hata
            calisan.user.set_password(parola)
            calisan.user.save(update_fields=["password", "password_changed_at", "updated_at"])
            from apps.audit.services import log_action  # noqa: PLC0415
            log_action(
                tenant_id=calisan.tenant_id,
                action="user.password.reset",
                user=request.user,
                table_name="users",
                record_id=calisan.user_id,
                changes={"employee_id": str(calisan.pk)},
            )
        except (LeaveError, OrganizationError, TestUiAuthorizationError) as hata:
            return TestUiErrors.cevap(hata)
        return Response({"data": {"updated": True}})


class EmployeeEmailUpdateView(TestUiView):
    """Yetkili yönetici, kapsamındaki çalışan hesabının kurumsal e-postasını değiştirir."""

    @transaction.atomic
    def post(self, request, employee_id: str):  # noqa: ANN001
        try:
            if not user_has_permission(request.user, "user.manage"):
                raise TestUiAuthorizationError("E-posta güncellemek için 'user.manage' izni gerekir.")
            calisan = _kapsamdaki_calisan(request.user, employee_id)
            if calisan.user_id is None:
                raise LeaveError("Bu çalışana bağlı bir giriş hesabı yok.")
            eposta = str(request.data.get("email") or "").strip().lower()
            if not eposta or "@" not in eposta:
                raise LeaveError("Geçerli bir kurumsal e-posta adresi girin.")
            from django.contrib.auth import get_user_model  # noqa: PLC0415

            user_modelu = get_user_model()
            if user_modelu.objects.filter(
                tenant_id=calisan.tenant_id, email=eposta
            ).exclude(pk=calisan.user_id).exists():
                raise LeaveError("Bu e-posta ile kayıtlı başka bir kullanıcı var.")
            eski = calisan.user.email
            if eski == eposta:
                return Response({"data": {"email": eposta, "updated": False}})
            calisan.user.email = eposta
            calisan.user.save(update_fields=["email", "updated_at"])
            from apps.audit.services import log_action  # noqa: PLC0415

            log_action(
                tenant_id=calisan.tenant_id,
                action="user.email.update",
                user=request.user,
                table_name="users",
                record_id=calisan.user_id,
                changes={
                    "employee_id": str(calisan.pk),
                    "alan_degisiklikleri": {
                        "Kurumsal e-posta": {"before": eski, "after": eposta}
                    },
                },
            )
        except (LeaveError, OrganizationError, TestUiAuthorizationError) as hata:
            return TestUiErrors.cevap(hata)
        return Response({"data": {"email": eposta, "updated": True}})


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
            "employee__department", "leave_type", "employee", "decision_snapshot"
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
                start_time=_saat(veri.get("start_time"), "start_time") if veri.get("start_time") else None,
                end_time=_saat(veri.get("end_time"), "end_time") if veri.get("end_time") else None,
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


class EmployeeEntitlementView(TestUiView):
    """
    İzin hakkı tanımlama/düzeltme — **mutlak gün** ile, servis katmanı üzerinden.

    Arayüz bakiye **yazmaz**: `set_leave_entitlement` çağrılır (ilk kez → hakediş
    defter kaydı; sonrasında → gerekçeli düzeltme). Gerekçe zorunludur; sonuç
    sunucudan döner (`durum`), böylece "kaydettim" ile "yazılacak bir şey yoktu"
    karışmaz.
    """

    def post(self, request, employee_id: str):  # noqa: ANN001
        calisan = _kapsamdaki_calisan(request.user, employee_id)
        veri = request.data
        tur_yil = period_year_for()
        yil = int(veri.get("period_year") or tur_yil)
        try:
            tur = LeaveType.objects.filter(
                pk=_uuid(veri.get("leave_type_id"), "leave_type_id"),
                tenant_id=request.user.tenant_id,
                deleted_at__isnull=True,
            ).first()
            if tur is None:
                raise LeaveError("İzin türü seçilmelidir.")
            if not tur.uses_balance:
                raise LeaveError(
                    "Yalnız bakiye kullanan izin türleri için hak tanımlanır."
                )
            try:
                miktar = Decimal(str(veri.get("hours" if tur.is_time_based else "days")).replace(",", "."))
            except (InvalidOperation, TypeError, ValueError) as hata:
                raise LeaveError(
                    "Serbest zaman hakkı saat, diğer izin hakları gün olarak sayı olmalıdır."
                ) from hata
            sonuc = set_leave_entitlement(
                actor=request.user,
                employee=calisan,
                leave_type=tur,
                period_year=yil,
                days=None if tur.is_time_based else miktar,
                hours=miktar if tur.is_time_based else None,
                reason=str(veri.get("reason") or ""),
            )
        except (LeaveError, LeaveAuthorizationError) as hata:
            return TestUiErrors.cevap(hata)
        mesajlar = {
            "hakedis_tanimlandi": "İzin hakkı başarıyla tanımlandı.",
            "duzeltme": "İzin bakiyesi başarıyla güncellendi.",
            "degisiklik_yok": "Değişiklik yok: girilen değer mevcut hakkın aynısı.",
        }
        return Response(
            {
                "data": {
                    "durum": sonuc["durum"],
                    "mesaj": mesajlar.get(sonuc["durum"], "İşlem tamamlandı."),
                    "delta": sonuc["delta"],
                    "hak_toplam": sonuc["hak_toplam"],
                    "employee_no": calisan.employee_no,
                },
                "meta": {"izin_haklari": _izin_haklari_listesi(calisan, yil)},
            }
        )


class OvertimeListView(TestUiView):
    """
    Fazla mesai: kendi kayıtlarım (çalışan) / kapsamımdaki kayıtlar (yönetici) + bildirim.

    * `GET` → izin `overtime.request.read`; satırlar `SELF` ∨ kapsam ile süzülür
      (kapsam dışı çalışanın kaydı **hiç dönmez**; varlık sızdırılmaz).
    * `POST` → izin `overtime.request.create` ve **yalnız kendi çalışan kaydı** için
      (V1 sınırı; başka çalışan adına bildirim açılamaz). Süre sunucuda hesaplanır.
    """

    def get(self, request):  # noqa: ANN001
        user = request.user
        durum = str(request.query_params.get("status") or "").strip()
        kendi = overtime_user_employee(user)
        kayitlar = _gorunur_fazla_mesailer(user)
        if durum in dict(OvertimeStatus.choices):
            kayitlar = [t for t in kayitlar if t.status == durum]
        satirlar = [_fazla_mesai_ozeti(t, user) for t in kayitlar]
        benim = [s for s in satirlar if s["kendi_kaydim"]]
        kural_okuma = bool(
            user_has_permission(user, "overtime.rule.read")
            or user_has_permission(user, "overtime.request.create")
        )
        kurallar = []
        if kural_okuma:
            kural_queryset = (
                list_overtime_rules(actor=user)
                if user_has_permission(user, "overtime.rule.read")
                else OvertimeRule.objects.filter(
                    tenant_id=user.tenant_id,
                    deleted_at__isnull=True,
                ).order_by("-effective_from", "min_minutes", "code")
            )
            kurallar = [
                {
                    "id": str(kural.pk),
                    "code": kural.code,
                    "name": kural.name,
                    "min_minutes": kural.min_minutes,
                    "max_minutes": kural.max_minutes,
                    "multiplier": str(kural.multiplier),
                    "compensation_type": kural.compensation_type,
                    "compensation_type_label": kural.get_compensation_type_display(),
                    "effective_from": kural.effective_from.isoformat(),
                    "effective_until": (
                        kural.effective_until.isoformat()
                        if kural.effective_until
                        else None
                    ),
                }
                for kural in kural_queryset
            ]
        return Response(
            {
                "data": satirlar,
                "meta": {
                    "can_create": kendi is not None
                    and can_create_overtime_for(user, kendi),
                    "employee_id": str(kendi.pk) if kendi is not None else None,
                    "employee_no": kendi.employee_no if kendi is not None else None,
                    "kendi_kayitlarim": benim,
                    "durum_sayilari": {
                        kod: sum(1 for s in satirlar if s["status"] == kod)
                        for kod in dict(OvertimeStatus.choices)
                    },
                    "karar_bekleyen": [s for s in satirlar if s["can_decide"]],
                    "max_minutes": OVERTIME_MAX_MINUTES,
                    "kayit_sayisi": len(satirlar),
                    # Form varsayılanı: bugünün tarihi sunucudan gelir (istemci saati
                    # farklı olabilir; gelecek tarih zaten reddedilir).
                    "today": timezone.localdate().isoformat(),
                    "can_read_rules": kural_okuma,
                    "can_manage_rules": user_has_permission(user, "overtime.rule.manage"),
                    "kurallar": kurallar,
                },
            }
        )

    def post(self, request):  # noqa: ANN001
        user = request.user
        kendi = overtime_user_employee(user)
        if kendi is None:
            return TestUiErrors.cevap(
                OvertimeValidationError(
                    "Bu kullanıcıya bağlı bir çalışan kaydı yok; fazla mesai bildirimi "
                    "açılamaz."
                )
            )
        veri = request.data
        try:
            rule_id = veri.get("overtime_rule_id")
            if not rule_id:
                raise OvertimeValidationError("Fazla mesai kuralı seçilmelidir.")
            overtime_rule = (
                OvertimeRule.objects.filter(
                    pk=_uuid(rule_id, "overtime_rule_id"),
                    tenant_id=kendi.tenant_id,
                    deleted_at__isnull=True,
                )
                .first()
            )
            if overtime_rule is None:
                raise OvertimeValidationError("Seçilen fazla mesai kuralı bulunamadı.")
            talep = create_overtime_request(
                actor=user,
                employee=kendi,
                work_date=_tarih(veri.get("work_date"), "work_date"),
                start_time=_saat(veri.get("start_time"), "start_time"),
                end_time=_saat(veri.get("end_time"), "end_time"),
                description=str(veri.get("description") or ""),
                # İstemci süre gönderirse **yalnızca doğrulanır** (yazılmaz).
                client_total_minutes=veri.get("total_minutes"),
                overtime_rule=overtime_rule,
            )
        except (OvertimeError, OvertimeAuthorizationError, OvertimeValidationError) as hata:
            return TestUiErrors.cevap(hata)
        return Response(
            {"data": _fazla_mesai_ozeti(talep, user)}, status=status.HTTP_201_CREATED
        )


class OvertimeRuleCreateView(TestUiView):
    """Yetkili tenant yöneticisi/İK için fazla mesai kuralı oluşturur."""

    def post(self, request):  # noqa: ANN001
        veri = request.data
        try:
            min_minutes = int(veri.get("min_minutes"))
            max_raw = veri.get("max_minutes")
            max_minutes = None if max_raw in (None, "") else int(max_raw)
            effective_until_raw = veri.get("effective_until")
            kural = create_overtime_rule(
                actor=request.user,
                code=str(veri.get("code") or ""),
                name=str(veri.get("name") or ""),
                min_minutes=min_minutes,
                max_minutes=max_minutes,
                multiplier=str(veri.get("multiplier") or ""),
                compensation_type=str(veri.get("compensation_type") or ""),
                effective_from=_tarih(veri.get("effective_from"), "effective_from"),
                effective_until=(
                    None
                    if effective_until_raw in (None, "")
                    else _tarih(effective_until_raw, "effective_until")
                ),
            )
        except (TypeError, ValueError, LeaveError, OvertimeError, OvertimeAuthorizationError) as hata:
            return TestUiErrors.cevap(hata)
        return Response(
            {
                "data": {
                    "id": str(kural.pk),
                    "code": kural.code,
                    "name": kural.name,
                }
            },
            status=status.HTTP_201_CREATED,
        )


class OvertimeRuleUpdateView(TestUiView):
    """Canlı fazla mesai kuralını günceller; geçmiş talepler snapshot kullanır."""

    def post(self, request, rule_id: str):  # noqa: ANN001
        kural = OvertimeRule.objects.filter(
            pk=_uuid(rule_id, "rule_id"), tenant_id=request.user.tenant_id, deleted_at__isnull=True
        ).first()
        if kural is None:
            raise Http404("fazla_mesai_kurali_yok")
        veri = request.data
        try:
            max_raw = veri.get("max_minutes")
            updated = update_overtime_rule(
                actor=request.user, rule=kural, code=str(veri.get("code") or ""),
                name=str(veri.get("name") or ""), min_minutes=int(veri.get("min_minutes")),
                max_minutes=None if max_raw in (None, "") else int(max_raw),
                multiplier=str(veri.get("multiplier") or ""),
                compensation_type=str(veri.get("compensation_type") or ""),
                effective_from=_tarih(veri.get("effective_from"), "effective_from"),
                effective_until=(None if veri.get("effective_until") in (None, "")
                                 else _tarih(veri.get("effective_until"), "effective_until")),
            )
        except (TypeError, ValueError, LeaveError, OvertimeError, OvertimeAuthorizationError) as hata:
            return TestUiErrors.cevap(hata)
        return Response({"data": {"id": str(updated.pk), "code": updated.code, "name": updated.name}})


class OvertimeRuleDeleteView(TestUiView):
    """Fazla mesai kuralını geçmiş kayıtları koruyarak soft delete eder."""

    def post(self, request, rule_id: str):  # noqa: ANN001
        kural = OvertimeRule.objects.filter(
            pk=_uuid(rule_id, "rule_id"), tenant_id=request.user.tenant_id, deleted_at__isnull=True
        ).first()
        if kural is None:
            raise Http404("fazla_mesai_kurali_yok")
        try:
            delete_overtime_rule(actor=request.user, rule=kural, reason=str(request.data.get("reason") or ""))
        except (LeaveError, OvertimeError, OvertimeAuthorizationError) as hata:
            return TestUiErrors.cevap(hata)
        return Response({"data": {"id": str(kural.pk), "deleted": True}})


class OvertimeDecisionView(TestUiView):
    """Fazla mesai kararı (onay/ret) — izin + adım sahipliği + farklı aktör sunucuda."""

    def post(self, request, overtime_id: str):  # noqa: ANN001
        talep = _fazla_mesai_getir(request.user, overtime_id)
        karar = str(request.data.get("decision", "")).strip()
        if karar not in (OvertimeStatus.APPROVED, OvertimeStatus.REJECTED):
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
            sonuc = decide_overtime_request(
                actor=request.user,
                talep=talep,
                decision=karar,
                reason=str(request.data.get("reason", "")).strip(),
            )
        except (OvertimeError, OvertimeAuthorizationError, OvertimeValidationError) as hata:
            return TestUiErrors.cevap(hata)
        return Response({"data": _fazla_mesai_ozeti(sonuc, request.user)})


class OvertimeCancelView(TestUiView):
    """Onay bekleyen fazla mesai bildirimini iptal eder (gerekçe zorunlu)."""

    def post(self, request, overtime_id: str):  # noqa: ANN001
        talep = _fazla_mesai_getir(request.user, overtime_id)
        try:
            sonuc = cancel_overtime_request(
                actor=request.user,
                talep=talep,
                reason=str(request.data.get("reason", "")).strip(),
            )
        except (OvertimeError, OvertimeAuthorizationError, OvertimeValidationError) as hata:
            return TestUiErrors.cevap(hata)
        return Response({"data": _fazla_mesai_ozeti(sonuc, request.user)})


__all__ = [
    "ApprovalStep",
    "DashboardView",
    "DepartmentDeleteView",
    "DepartmentListView",
    "DepartmentMoveView",
    "EmployeeBalanceView",
    "EmployeeEntitlementView",
    "EmployeeListView",
    "EmployeeMoveView",
    "LeaveRequestCancelView",
    "LeaveRequestDecisionView",
    "LeaveRequestListView",
    "LookupView",
    "MeView",
    "OvertimeCancelView",
    "OvertimeDecisionView",
    "OvertimeListView",
    "TestUiView",
    "kapsamdaki_calisanlar",
]
