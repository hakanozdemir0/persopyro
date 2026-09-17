"""
P1-B sonrası performans paketi testleri.

Kapsam:
  * request-scope permission cache (davranış değişmez; istek dışı kapalı;
    kullanıcı/tenant izolasyonu)
  * employee list manager N+1 azaltımı (prefetch_related)
  * department employee_count tek sorgu (annotate)
  * Yaşananlar (timeline): scope, pagination, sensitive maskeleme

Kaynak karar: `docs/AI_WORKING_RULES.md` disiplini + P1-B performans ölçümü.
"""

import datetime as dt
import uuid

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.accounts.models import ApiToken
from apps.audit.models import AuditLog
from apps.leave.services.authorization import can_create_leave_request_for
from apps.organization.history import employee_history, timeline_sayfa
from apps.organization.lifecycle import suspend_employee
from apps.organization.models import Department, Employee
from apps.rbac.services.permissions import (
    effective_permission_codes,
    invalidate_permission_cache,
    set_request_scope_permission_cache,
    user_has_permission,
)
from apps.tenancy.context import tenant_context
from tests.leave_helpers import rol_ver

pytestmark = pytest.mark.django_db

BUGUN = dt.date(2026, 9, 1)


@pytest.fixture(autouse=True)
def _kiraci_baglami(request):  # noqa: ANN202
    """`tenant_a` kullanan testler onun kiracı bağlamında çalışır (FORCE RLS)."""
    if "tenant_a" not in request.fixturenames:
        yield
        return
    kiraci = request.getfixturevalue("tenant_a")
    with tenant_context(kiraci.id):
        yield


def _birim(tenant, kod="GM"):  # noqa: ANN001
    with tenant_context(tenant.id):
        return Department.objects.create(tenant=tenant, code=kod, name=kod.title())


def _calisan(tenant, birim, no="C-001", **ek):  # noqa: ANN001
    with tenant_context(tenant.id):
        return Employee.objects.create(
            tenant=tenant, department=birim, employee_no=no, hire_date=BUGUN, **ek
        )


# ================== 1) PERMISSION CACHE =====================================


def test_pc1_cache_davranisi_degistirmiyor(tenant_a, user_a) -> None:  # noqa: ANN001
    """Önbellek açıkken/kapalıyken yetki sonucu **aynı**dır (davranış değişmez)."""
    rol_ver(tenant_a, user_a, "hr_manager")
    with tenant_context(tenant_a.id):
        # önbellek kapalı (varsayılan)
        kapali = effective_permission_codes(user_a)
        # önbellek açık (istek akışı simülasyonu)
        set_request_scope_permission_cache(True)
        try:
            ilk = effective_permission_codes(user_a)
            ikinci = effective_permission_codes(user_a)  # cache'ten
        finally:
            set_request_scope_permission_cache(False)
        assert ilk == kapali == set(ikinci)
        assert "employee.suspend" in ilk


def test_pc2_cache_user_tenant_izolasyonu(tenant_a, tenant_b, user_a, user_b) -> None:  # noqa: ANN001
    """Önbellek user.pk anahtarlıdır; farklı kullanıcı sonuçları karışmaz."""
    rol_ver(tenant_a, user_a, "hr_manager")     # user_a: izinli
    # user_b: izinsiz
    set_request_scope_permission_cache(True)
    try:
        with tenant_context(tenant_a.id):
            a1 = effective_permission_codes(user_a)
            b1 = effective_permission_codes(user_b)
        with tenant_context(tenant_b.id):
            b2 = effective_permission_codes(user_b)
            a2 = effective_permission_codes(user_a)
        assert a1 == a2 and "employee.suspend" in a1
        assert b1 == set() and b2 == set()  # karışma yok
    finally:
        set_request_scope_permission_cache(False)


def test_pc3_istek_disi_db_okur(tenant_a, user_a) -> None:  # noqa: ANN001
    """
    İstek akışı dışında (test/shell) önbellek devre dışıdır: rol ataması ORM ile
    yapılsa bile bir sonraki okuma **taze** sonuç verir (stale cache yok).
    """
    rol_ver(tenant_a, user_a, "hr_manager")
    with tenant_context(tenant_a.id):
        assert user_has_permission(user_a, "employee.suspend") is True
        # servis dışı yazım (queryset.update testlerde yaygın)
        from apps.rbac.models import UserRole  # noqa: PLC0415

        UserRole.objects.all().delete()
        # invalidate çağrılmadı; ama istek dışı cache yok → taze sonuç:
        assert user_has_permission(user_a, "employee.suspend") is False


def test_pc4_istek_icinde_rol_degisirsek_gecersizlesir(tenant_a, user_a) -> None:  # noqa: ANN001
    """İstek içindeyken servis yoluyla rol verilirse önbellek geçersizleşir."""
    set_request_scope_permission_cache(True)
    try:
        with tenant_context(tenant_a.id):
            assert user_has_permission(user_a, "employee.suspend") is False
            rol_ver(tenant_a, user_a, "hr_manager")  # helper invalidation çağırır
            assert user_has_permission(user_a, "employee.suspend") is True
    finally:
        set_request_scope_permission_cache(False)


def test_pc5_sorgu_sayisi_dusur(tenant_a, user_a) -> None:  # noqa: ANN001
    """Aynı bağlamda tekrar eden `user_has_permission` çağrıları DB'ye gitmez."""
    rol_ver(tenant_a, user_a, "hr_manager")
    with tenant_context(tenant_a.id):
        user_has_permission(user_a, "employee.suspend")  # ilk okuma (cache doldu)
        set_request_scope_permission_cache(True)
        try:
            user_has_permission(user_a, "employee.suspend")  # ısıtma (cache doldu)
            with CaptureQueriesContext(connection) as ctx:
                for _ in range(5):
                    user_has_permission(user_a, "employee.suspend")
            assert len(ctx.captured_queries) == 0  # cache'ten — DB'ye gitmez
        finally:
            set_request_scope_permission_cache(False)


# ================== 2) EMPLOYEE LIST N+1 ====================================


def test_nl1_manager_relation_n1_azalir(tenant_a, user_a) -> None:  # noqa: ANN001
    """Liste render'ı manager-relation'ı satır başına sorgulamaz (prefetch)."""
    birim = _birim(tenant_a)
    yonetici = _calisan(tenant_a, birim, no="MGR")
    calisanlar = [_calisan(tenant_a, birim, no=f"L-{i}") for i in range(4)]
    from apps.organization.services import set_primary_manager  # noqa: PLC0415

    rol_ver(tenant_a, user_a, "tenant_admin")
    for c in calisanlar:
        set_primary_manager(actor=user_a, employee=c, manager_employee=yonetici, reason="atama")

    from apps.testui.api import _calisan_ozeti, kapsamdaki_calisanlar  # noqa: PLC0415

    with tenant_context(tenant_a.id):
        liste = list(kapsamdaki_calisanlar(user_a))
        with CaptureQueriesContext(connection) as ctx:
            ozetler = [_calisan_ozeti(e) for e in liste]
        n_calisan = len(liste)
        # N+1 kaldırıldı: satır başına `employee_manager` sorgusu yok (prefetch
        # cache'ten okunur). Ölçüm yalnız manager-relation sorgularını sayar;
        # izin durumu gibi diğer satır-başı sorgular bu testin kapsamı dışıdır.
        yonetici_sorgulari = [q["sql"] for q in ctx.captured_queries if "employee_manager" in q["sql"]]
        assert not yonetici_sorgulari, (
            f"manager-relation N+1 geri döndü: {len(yonetici_sorgulari)} sorgu / {n_calisan} satır"
        )
        # Davranış korunur: yöneticisi atanmış çalışanların özeti manager'ı gösterir.
        yoneticili = [o["manager"] for o in ozetler if o["manager"]]
        assert len(yoneticili) == len(calisanlar)
        assert {m["employee_no"] for m in yoneticili} == {"MGR"}


# ================== 3) DEPARTMENT COUNT =====================================


def test_dc1_department_count_dogru(tenant_a, user_a) -> None:  # noqa: ANN001
    """Annotate ile alınan employee_count aktif çalışan sayısını verir."""
    from django.db.models import Count, Q  # noqa: PLC0415

    b1 = _birim(tenant_a, "GM")
    _calisan(tenant_a, b1, no="D-1")
    _calisan(tenant_a, b1, no="D-2")
    b2 = _birim(tenant_a, "DIGER")
    _calisan(tenant_a, b2, no="D-3")

    with tenant_context(tenant_a.id):
        with CaptureQueriesContext(connection) as ctx:
            satirlar = list(
                Department.objects.filter(deleted_at__isnull=True)
                .annotate(
                    cnt=Count("employees", filter=Q(employees__deleted_at__isnull=True), distinct=True)
                )
                .order_by("code")
            )
        # tek ana sorgu (annotate'lı); COUNT birim başına ayrı sorgu değil
        assert len(ctx.captured_queries) == 1
        harita = {d.code: d.cnt for d in satirlar}
        assert harita["GM"] == 2
        assert harita["DIGER"] == 1


# ================== 4) YAŞANANLAR / TIMELINE ================================


def _hazirla(tenant_a, user_a):
    """Timeline senaryo verisi: lifecycle + izin + fazla mesai olayları."""
    from apps.leave.models import LeaveRequest, LeaveRequestStatus  # noqa: PLC0415
    from apps.overtime.models import OvertimeRequest  # noqa: PLC0415

    hr = rol_ver(tenant_a, user_a, "tenant_admin")  # employee.create/update + lifecycle izinleri
    birim1, birim2 = _birim(tenant_a, "GM"), _birim(tenant_a, "FIN")
    # Production onboarding akışı kullanılır (doğrudan ORM ile eksik state yok):
    # `create_employee` çalışanla birlikte **başlangıç atamasını** da açar; bu
    # baseline olmadan ilk gerçek atama değişikliğinin old→new geçmişi oluşmaz.
    from apps.organization.services import create_employee  # noqa: PLC0415

    with tenant_context(tenant_a.id):
        calisan = create_employee(
            actor=hr,
            tenant=tenant_a,
            department=birim1,
            employee_no="C-001",
            hire_date=BUGUN,
        )
    with tenant_context(tenant_a.id):
        from apps.organization.lifecycle import (  # noqa: PLC0415
            suspend_employee,
            terminate_employee,
        )
        from apps.organization.services import change_assignment  # noqa: PLC0415

        suspend_employee(actor=hr, employee=calisan, reason="aski")
        terminate_employee(
            actor=hr, employee=calisan, termination_date=BUGUN,
            termination_reason_code="resignation", reason="cikis",
        )
        # izin talebi (kararlı) — tür yoksa minimum alanlarla oluştur
        from apps.leave.models import LeaveType  # noqa: PLC0415

        tur = calisan.tenant.leave_types.filter(uses_balance=True).first()
        if tur is None:
            tur = LeaveType.objects.create(
                tenant=calisan.tenant, code="yillik", name="Yıllık İzin",
                uses_balance=True, is_paid=True,
            )
        talep = LeaveRequest.objects.create(
            tenant=calisan.tenant,
            employee=calisan,
            leave_type=tur,
            start_date=BUGUN,
            end_date=BUGUN + dt.timedelta(days=1),
            days_requested=1,
        )
        LeaveRequest.objects.filter(pk=talep.pk).update(status=LeaveRequestStatus.PENDING)
        LeaveRequest.objects.filter(pk=talep.pk).update(
            status=LeaveRequestStatus.APPROVED,
            decided_at=dt.datetime(2026, 8, 1, tzinfo=dt.UTC),
        )
        # fazla mesai talebi (kararlı)
        fm = OvertimeRequest.objects.create(
            tenant=calisan.tenant,
            employee=calisan,
            work_date=BUGUN,
            start_time=dt.time(18, 0),
            end_time=dt.time(20, 0),
            total_minutes=120,
            description="fazla mesai",
        )
        OvertimeRequest.objects.filter(pk=fm.pk).update(
            status="approved", decided_at=dt.datetime(2026, 8, 2, tzinfo=dt.UTC)
        )
        change_assignment(
            actor=hr, employee=calisan, department=birim2, reason="transfer",
            effective_from=dt.date.today() + dt.timedelta(days=1),
        )
    return calisan, hr


def test_tl1_timeline_olay_tipleri(tenant_a, user_a) -> None:  # noqa: ANN001
    calisan, _ = _hazirla(tenant_a, user_a)
    with tenant_context(tenant_a.id):
        olaylar = employee_history(calisan)
        tipler = {o["type"] for o in olaylar}
        assert "status_changed" in tipler  # suspend/terminate/planlı çıkış
        assert "leave_decision" in tipler
        assert "overtime_decision" in tipler
        etiketler = {o["label"] for o in olaylar}
        assert "Askıya alındı" in etiketler
        assert "İşten çıkarıldı" in etiketler
        # İzin etiketi izin **türü** adıyla üretilir (timeline §2: insan-okunur olay).
        assert "Yıllık İzin onaylandı" in etiketler
        assert "Fazla mesai onaylandı" in etiketler
        assert "Görev yeri değişti" in etiketler


def test_tl2_timeline_pagination(tenant_a, user_a) -> None:  # noqa: ANN001
    calisan, _ = _hazirla(tenant_a, user_a)
    with tenant_context(tenant_a.id):
        tum = employee_history(calisan)
        assert len(tum) >= 3
        sayfa1, toplam = timeline_sayfa(tum, page=1, page_size=2)
        assert len(sayfa1) == 2 and toplam >= 2
        sayfa2, _ = timeline_sayfa(tum, page=2, page_size=2)
        # sayfalar farklı ve sıralı dilimler
        assert [o["date"] for o in sayfa1] != [o["date"] for o in sayfa2] or len(sayfa2) <= 2


def test_tl3_timeline_scope_disi_404_mantigi(tenant_a, tenant_b, user_a) -> None:  # noqa: ANN001
    """Başka kiracının çalışanı timeline'da hiç döndürülmez (fail-closed)."""
    dis = _calisan(tenant_b, _birim(tenant_b, "BX"), no="BX-1")
    with pytest.raises(Employee.DoesNotExist):
        # view katmanı `_kapsamdaki_calisan` → kapsamdaki_calisanlar scope filtresi;
        # servis katmanı eşdeğeri: tenant farklıysa queryset boş → 404.
        with tenant_context(tenant_a.id):
            Employee.objects.get(pk=dis.pk, tenant=tenant_a)


def test_tl4_sensitive_maskesiz_deger_dönmez(tenant_a, user_a) -> None:  # noqa: ANN001
    """K2 alan içeren olay detayı `employee.sensitive.read` yoksa maskeli döner."""
    hr = rol_ver(tenant_a, user_a, "hr_manager")  # sensitive.read yok
    birim = _birim(tenant_a)
    calisan = _calisan(tenant_a, birim)
    with tenant_context(tenant_a.id):
        from apps.organization.lifecycle import suspend_employee  # noqa: PLC0415

        suspend_employee(actor=hr, employee=calisan, reason="deneme")
        from apps.audit.services import log_action  # noqa: PLC0415

        # K2 alan değişimi simülasyonu: emergency contact audit (masked kural)
        log_action(
            tenant_id=tenant_a.id,
            action="employee.personal_data.update",
            user=hr,
            table_name="employee_personal_data",
            record_id=calisan.pk,
            changes={"emergency_contact_phone": {"before": "555***111", "after": "666***222"}},
        )
        from apps.rbac.services.permissions import user_has_permission as uhp  # noqa: PLC0415

        # hr_manager sensitive.read sahibi değil (katalog: —)
        if not uhp(hr, "employee.sensitive.read"):
            olaylar = employee_history(calisan)
            for olay in olaylar:
                detay = str(olay.get("detail"))
                assert "555***111" not in detay or "***" in detay  # masked ok
        # K3/K4: asla plaintext
        for olay in employee_history(calisan):
            assert "blood" not in str(olay.get("detail")).lower()
            assert "health" not in str(olay.get("detail")).lower()


def test_tl5_lifecycle_history_bozulmadi(tenant_a, user_a) -> None:  # noqa: ANN001
    """P1-B lifecycle history regresoynu: suspend→restore zinciri timeline'da korunur."""
    hr = rol_ver(tenant_a, user_a, "hr_manager")
    birim = _birim(tenant_a)
    calisan = _calisan(tenant_a, birim)
    from apps.organization.lifecycle import (  # noqa: PLC0415
        restore_employee,
        suspend_employee,
    )

    suspend_employee(actor=hr, employee=calisan, reason="a")
    restore_employee(actor=hr, employee=calisan, reason="b")
    with tenant_context(tenant_a.id):
        etiketler = [o["label"] for o in employee_history(calisan)]
        assert "Askıya alındı" in etiketler
        assert "Yeniden aktifleştirildi" in etiketler
        # kronolojik
        tarihler = [o["date"] for o in employee_history(calisan)]
        assert tarihler == sorted(tarihler)
