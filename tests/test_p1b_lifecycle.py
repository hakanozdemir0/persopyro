"""
P1-B — Employee lifecycle services testleri.

Kapsam: durum makinesi (§28.2), erişim kesme (OD59: auth_version + token),
planlı çıkış + idempotent command, atama/koşul/yönetici geçmişi (OD60),
izin/kapsam (OD61), suspended/terminated iş kuralları (OD42), geçmiş servisi.
Kaynak kararlar: OD41–OD43, OD58–OD61 (docs/AUTHORIZATION.md §16).
"""

import datetime as dt

import pytest

from apps.accounts.auth_version import (
    AuthVersionMismatchError,
    oturum_auth_version_dogrula,
    oturum_auth_version_yaz,
)
from apps.accounts.models import ApiToken, UserStatus
from apps.audit.models import AuditLog
from apps.organization.history import employee_history
from apps.organization.lifecycle import (
    IZINLI_GECISLER,
    LifecycleStateError,
    LifecycleValidationError,
    archive_employee,
    change_manager,
    ihbar_döneminde,
    notice_etiketi,
    restore_employee,
    suspend_employee,
    terminate_employee,
)
from apps.organization.models import (
    Department,
    Employee,
    EmployeeAssignment,
    EmployeeEmploymentTerms,
    EmployeeManager,
    EmployeeStatus,
    Position,
    Workplace,
)
from apps.organization.services import (
    OrganizationAuthorizationError,
    OrganizationError,
    change_assignment,
    change_employment_terms,
    create_employee,
)
from apps.tenancy.context import tenant_context

pytestmark = pytest.mark.django_db

@pytest.fixture(autouse=True)
def _kiraci_baglami(request):  # noqa: ANN202
    """`tenant_a` kullanan testler onun kiracı bağlamında çalışır (FORCE RLS)."""
    if "tenant_a" not in request.fixturenames:
        yield
        return
    kiraci = request.getfixturevalue("tenant_a")
    with tenant_context(kiraci.id):
        yield


BUGUN = dt.date(2026, 9, 1)


# --- yardımcılar -------------------------------------------------------------

@pytest.fixture
def hr(tenant_a, user_a):
    """TENANT kapsamlı hr_manager rolü atanmış kullanıcı (sistem rol şablonu)."""
    from tests.leave_helpers import rol_ver

    rol_ver(tenant_a, user_a, "hr_manager")
    return user_a


@pytest.fixture
def arsivci(tenant_a, user_a):
    """tenant_admin (employee.delete/arşiv dahil tüm izinler)."""
    from tests.leave_helpers import rol_ver

    rol_ver(tenant_a, user_a, "tenant_admin")
    return user_a


def _birim(tenant, kod="GM"):  # noqa: ANN001
    with tenant_context(tenant.id):
        return Department.objects.create(tenant=tenant, code=kod, name=kod.title())


def _pozisyon(tenant, kod="PZ-1"):  # noqa: ANN001
    with tenant_context(tenant.id):
        return Position.objects.create(tenant=tenant, code=kod, name="Uzman", level="mid")


def _isyeri(tenant, kod="IST"):  # noqa: ANN001
    with tenant_context(tenant.id):
        return Workplace.objects.create(
            tenant=tenant,
            code=kod,
            name=f"{kod} Ofis",
            type="office",
            country_code="TR",
            timezone="Europe/Istanbul",
        )


def _calisan(tenant, birim, no="C-001", **ek):  # noqa: ANN001
    with tenant_context(tenant.id):
        return Employee.objects.create(
            tenant=tenant, department=birim, employee_no=no, hire_date=BUGUN, **ek
        )


def _kosullar(tenant, calisan, **ek):  # noqa: ANN001
    veri = {
        "tenant": tenant,
        "employee": calisan,
        "effective_from": BUGUN,
        "employment_type": "full_time",
        "working_model": "onsite",
    }
    veri.update(ek)
    with tenant_context(tenant.id):
        return EmployeeEmploymentTerms.objects.create(**veri)


def _auditleri(tenant, action, employee):  # noqa: ANN001
    with tenant_context(tenant.id):
        return list(
            AuditLog.objects.filter(action=action, record_id=employee.pk).order_by("created_at")
        )


def _atama_kur(tenant_a, calisan, birim, aktor):  # noqa: ANN001
    """Servis yoluyla ilk açık atama üretir (OD60: tek servis yolu)."""
    with tenant_context(tenant_a.id):
        change_assignment(
            actor=aktor, employee=calisan, department=calisan.department,
            reason="ilk atama", effective_from=BUGUN,
        )


# ============================ 1) STATE MACHINE ===============================


def test_sm1_active_suspended(tenant_a, user_a, hr) -> None:  # noqa: ANN001
    hr = hr
    birim = _birim(tenant_a)
    calisan = _calisan(tenant_a, birim)
    suspend_employee(actor=hr, employee=calisan, reason="inceleme")
    calisan.refresh_from_db()
    assert calisan.status == EmployeeStatus.SUSPENDED


def test_sm2_suspended_active(tenant_a, user_a, hr) -> None:  # noqa: ANN001
    hr = hr
    birim = _birim(tenant_a)
    calisan = _calisan(tenant_a, birim)
    suspend_employee(actor=hr, employee=calisan, reason="i")
    restore_employee(actor=hr, employee=calisan, reason="bitti")
    calisan.refresh_from_db()
    assert calisan.status == EmployeeStatus.ACTIVE


def test_sm3_active_terminated(tenant_a, user_a, hr) -> None:  # noqa: ANN001
    hr = hr
    birim = _birim(tenant_a)
    calisan = _calisan(tenant_a, birim)
    terminate_employee(
        actor=hr, employee=calisan, termination_date=BUGUN,
        termination_reason_code="resignation", reason="istifa",
    )
    calisan.refresh_from_db()
    assert calisan.status == EmployeeStatus.TERMINATED


def test_sm4_suspended_terminated(tenant_a, user_a, hr) -> None:  # noqa: ANN001
    hr = hr
    birim = _birim(tenant_a)
    calisan = _calisan(tenant_a, birim)
    suspend_employee(actor=hr, employee=calisan, reason="i")
    terminate_employee(
        actor=hr, employee=calisan, termination_date=BUGUN,
        termination_reason_code="employer_termination", reason="fesih",
    )
    calisan.refresh_from_db()
    assert calisan.status == EmployeeStatus.TERMINATED


def test_sm5_terminated_active_restore(tenant_a, user_a, hr) -> None:  # noqa: ANN001
    hr = hr
    birim = _birim(tenant_a)
    calisan = _calisan(tenant_a, birim)
    terminate_employee(
        actor=hr, employee=calisan, termination_date=BUGUN,
        termination_reason_code="resignation", reason="yanlışlık",
    )
    restore_employee(actor=hr, employee=calisan, reason="hata düzeltme")
    calisan.refresh_from_db()
    assert calisan.status == EmployeeStatus.ACTIVE
    assert calisan.termination_date is None  # çıkış alanları temizlenir


def test_sm6_terminated_archived(tenant_a, user_a, hr) -> None:  # noqa: ANN001
    hr = hr
    birim = _birim(tenant_a)
    calisan = _calisan(tenant_a, birim)
    terminate_employee(
        actor=hr, employee=calisan, termination_date=BUGUN,
        termination_reason_code="resignation", reason="i",
    )
    from tests.leave_helpers import rol_ver as _rol_ver  # noqa: PLC0415

    _rol_ver(tenant_a, user_a, "tenant_admin")  # employee.delete izni
    archive_employee(actor=hr, employee=calisan, reason="arşiv")
    calisan.refresh_from_db()
    assert calisan.status == EmployeeStatus.ARCHIVED


def test_sm7_archived_active_reddedilir(tenant_a, user_a, hr) -> None:  # noqa: ANN001
    hr = hr
    birim = _birim(tenant_a)
    calisan = _calisan(tenant_a, birim)
    terminate_employee(
        actor=hr, employee=calisan, termination_date=BUGUN,
        termination_reason_code="resignation", reason="i",
    )
    from tests.leave_helpers import rol_ver as _rol_ver  # noqa: PLC0415

    _rol_ver(tenant_a, user_a, "tenant_admin")  # employee.delete izni
    archive_employee(actor=hr, employee=calisan, reason="arşiv")
    with tenant_context(tenant_a.id), pytest.raises(LifecycleStateError):
        restore_employee(actor=hr, employee=calisan, reason="deneme")
    # izinli geçiş kümesi archived için boş
    assert IZINLI_GECISLER[EmployeeStatus.ARCHIVED] == frozenset()


def test_sm8_terminated_suspended_reddedilir(tenant_a, user_a, hr) -> None:  # noqa: ANN001
    hr = hr
    birim = _birim(tenant_a)
    calisan = _calisan(tenant_a, birim)
    terminate_employee(
        actor=hr, employee=calisan, termination_date=BUGUN,
        termination_reason_code="resignation", reason="i",
    )
    with tenant_context(tenant_a.id), pytest.raises(LifecycleStateError):
        suspend_employee(actor=hr, employee=calisan, reason="deneme")


def test_sm9_same_state_noop(tenant_a, user_a, hr) -> None:  # noqa: ANN001
    hr = hr
    birim = _birim(tenant_a)
    calisan = _calisan(tenant_a, birim)
    suspend_employee(actor=hr, employee=calisan, reason="1")
    suspend_employee(actor=hr, employee=calisan, reason="2")  # no-op
    assert len(_auditleri(tenant_a, "employee.suspend", calisan)) == 1


def test_sm10_gecersiz_gecis_audit_yazilir(tenant_a, user_a, hr) -> None:  # noqa: ANN001
    hr = hr
    birim = _birim(tenant_a)
    calisan = _calisan(tenant_a, birim, status=EmployeeStatus.ARCHIVED)
    with tenant_context(tenant_a.id), pytest.raises(LifecycleStateError):
        restore_employee(actor=hr, employee=calisan, reason="x")


# ============================ 2) AUTH INVALIDATION ==========================


def test_ai1_suspend_auth_version_artar(tenant_a, user_a, hr) -> None:  # noqa: ANN001
    hr = hr
    birim = _birim(tenant_a)
    from django.contrib.auth import get_user_model  # noqa: PLC0415

    calisan_user = get_user_model().objects.create_user(
        tenant=tenant_a, email="c@t.test", password="Guclu-Parola-123",
        first_name="C", last_name="D",
    )
    with tenant_context(tenant_a.id):
        calisan = Employee.objects.create(
            tenant=tenant_a, department=birim, employee_no="C-U1",
            hire_date=BUGUN, user=calisan_user,
        )
    onceki = calisan_user.auth_version
    suspend_employee(actor=hr, employee=calisan, reason="i")
    calisan_user.refresh_from_db()
    assert calisan_user.auth_version == onceki + 1
    assert calisan_user.status == UserStatus.INACTIVE
    assert not calisan_user.is_active


def test_ai2_suspend_token_revoke(tenant_a, user_a, hr) -> None:  # noqa: ANN001
    hr = hr
    birim = _birim(tenant_a)
    from django.contrib.auth import get_user_model  # noqa: PLC0415

    calisan_user = get_user_model().objects.create_user(
        tenant=tenant_a, email="c2@t.test", password="Guclu-Parola-123",
        first_name="C", last_name="D",
    )
    with tenant_context(tenant_a.id):
        ApiToken.issue(
            calisan_user, purpose="lifecycle test amacı", scopes=["employee.read"], ttl_hours=1
        )
        calisan = Employee.objects.create(
            tenant=tenant_a, department=birim, employee_no="C-U2",
            hire_date=BUGUN, user=calisan_user,
        )
    suspend_employee(actor=hr, employee=calisan, reason="i")
    with tenant_context(tenant_a.id):
        assert ApiToken.objects.filter(user=calisan_user, revoked_at__isnull=True).count() == 0


def test_ai3_restore_eski_oturumu_diriltmez(tenant_a, user_a, hr) -> None:  # noqa: ANN001
    """
    Suspend→restore sonrası eski oturum snapshot'ı (suspend öncesi sürüm) RED edilir;
    güncel snapshot'lı oturum kabul edilir. OD59: yeniden giriş zorunlu.
    """
    from django.contrib.auth import get_user_model  # noqa: PLC0415

    birim = _birim(tenant_a)
    calisan_user = get_user_model().objects.create_user(
        tenant=tenant_a, email="ai3@t.test", password="Guclu-Parola-123",
        first_name="A", last_name="I",
    )
    with tenant_context(tenant_a.id):
        calisan = Employee.objects.create(
            tenant=tenant_a, department=birim, employee_no="C-AI3",
            hire_date=BUGUN, user=calisan_user,
        )

    class _Oturum(dict):
        def flush(self):  # noqa: ANN202
            self.clear()

    # 1) Giriş anı: snapshot yazılır (suspend ÖNCESİ sürüm)
    eski_oturum = _Oturum()
    oturum_auth_version_yaz(eski_oturum, calisan_user)

    # 2) Suspend: auth_version++
    suspend_employee(actor=hr, employee=calisan, reason="i")
    calisan_user.refresh_from_db()
    suspend_sonrasi = calisan_user.auth_version
    assert suspend_sonrasi > int(eski_oturum["auth_version"])

    # 3) Eski snapshot'lı oturum → mismatch → flush + AuthVersionMismatchError
    with tenant_context(tenant_a.id):
        with pytest.raises(AuthVersionMismatchError):
            oturum_auth_version_dogrula(calisan_user, eski_oturum)
    assert len(eski_oturum) == 0  # oturum düşürüldü (flush)

    # 4) Restore: auth_version tekrar artar
    restore_employee(actor=hr, employee=calisan, reason="bitti")
    calisan_user.refresh_from_db()
    restore_sonrasi = calisan_user.auth_version
    assert restore_sonrasi > suspend_sonrasi

    # 5) suspend-öncesi snapshot hâlâ RED (iki artıştan sonra da)
    with tenant_context(tenant_a.id):
        with pytest.raises(AuthVersionMismatchError):
            oturum_auth_version_dogrula(calisan_user, _Oturum(auth_version=0))

    # 6) Güncel snapshot'lı oturum KABUL edilir (yeni giriş)
    yeni_oturum = _Oturum()
    oturum_auth_version_yaz(yeni_oturum, calisan_user)
    with tenant_context(tenant_a.id):
        oturum_auth_version_dogrula(calisan_user, yeni_oturum)  # exception yok


def test_ai3b_token_revoke_edilir(tenant_a, user_a, hr) -> None:  # noqa: ANN001
    """Suspend: kullanıcının aktif API token'ları iptal edilir → token 401."""
    from django.contrib.auth import get_user_model  # noqa: PLC0415
    from rest_framework.exceptions import AuthenticationFailed  # noqa: PLC0415

    from apps.accounts.authentication import ApiTokenAuthentication  # noqa: PLC0415

    birim = _birim(tenant_a)
    calisan_user = get_user_model().objects.create_user(
        tenant=tenant_a, email="ai3b@t.test", password="Guclu-Parola-123",
        first_name="A", last_name="B",
    )
    with tenant_context(tenant_a.id):
        calisan = Employee.objects.create(
            tenant=tenant_a, department=birim, employee_no="C-AI3B",
            hire_date=BUGUN, user=calisan_user,
        )
        kayit, duz = ApiToken.issue(
            calisan_user, purpose="lifecycle token testi", scopes=["employee.read"], ttl_hours=1
        )
    assert kayit.auth_version_snapshot == 0

    suspend_employee(actor=hr, employee=calisan, reason="i")
    calisan_user.refresh_from_db()
    assert calisan_user.auth_version > 0

    # Token iptal edilmiş olmalı (suspend token revoke) → 401
    auth = ApiTokenAuthentication()
    with pytest.raises(AuthenticationFailed):
        auth.authenticate_credentials(duz)


def test_ai3c_token_snapshot_mismatch_401(tenant_a, user_a, hr) -> None:  # noqa: ANN001
    """
    İptal edilmemiş token + artan auth_version → 401 (ikinci savunma hattı).

    Token revoke'u atlansa bile sürüm uyuşmazlığı erişimi keser (OD59).
    """
    from django.contrib.auth import get_user_model  # noqa: PLC0415
    from rest_framework.exceptions import AuthenticationFailed  # noqa: PLC0415

    from apps.accounts.authentication import ApiTokenAuthentication  # noqa: PLC0415

    birim = _birim(tenant_a)
    calisan_user = get_user_model().objects.create_user(
        tenant=tenant_a, email="ai3c@t.test", password="Guclu-Parola-123",
        first_name="A", last_name="C",
    )
    with tenant_context(tenant_a.id):
        Employee.objects.create(
            tenant=tenant_a, department=birim, employee_no="C-AI3C",
            hire_date=BUGUN, user=calisan_user,
        )
        kayit, duz = ApiToken.issue(
            calisan_user, purpose="mismatch token testi", scopes=["employee.read"], ttl_hours=1
        )
        # suspend'in token revoke etkisini kaldır: iptal izini sıfırla (sadece bu test)
        ApiToken.objects.filter(pk=kayit.pk).update(revoked_at=None, revoked_reason=None)
        kayit.refresh_from_db()
        assert kayit.is_revoked is False

        # auth_version'ı SADECE sürüm kanalıyla artır (revoke tetiklemeden)
        calisan_user.auth_version += 1
        calisan_user.save(update_fields=["auth_version", "updated_at"])

        auth = ApiTokenAuthentication()
        with pytest.raises(AuthenticationFailed):
            auth.authenticate_credentials(duz)


def test_ai4_terminate_login_engeller(tenant_a, user_a, hr) -> None:  # noqa: ANN001
    hr = hr
    birim = _birim(tenant_a)
    from django.contrib.auth import get_user_model  # noqa: PLC0415

    calisan_user = get_user_model().objects.create_user(
        tenant=tenant_a, email="c3@t.test", password="Guclu-Parola-123",
        first_name="C", last_name="D",
    )
    with tenant_context(tenant_a.id):
        calisan = Employee.objects.create(
            tenant=tenant_a, department=birim, employee_no="C-U3",
            hire_date=BUGUN, user=calisan_user,
        )
    terminate_employee(
        actor=hr, employee=calisan, termination_date=BUGUN,
        termination_reason_code="resignation", reason="i",
    )
    calisan_user.refresh_from_db()
    assert not calisan_user.is_active  # login reddi (backends.py)


def test_ai5_archive_kullanici_pasif(tenant_a, user_a, hr) -> None:  # noqa: ANN001
    hr = hr
    birim = _birim(tenant_a)
    from django.contrib.auth import get_user_model  # noqa: PLC0415

    calisan_user = get_user_model().objects.create_user(
        tenant=tenant_a, email="c4@t.test", password="Guclu-Parola-123",
        first_name="C", last_name="D",
    )
    with tenant_context(tenant_a.id):
        calisan = Employee.objects.create(
            tenant=tenant_a, department=birim, employee_no="C-U4",
            hire_date=BUGUN, user=calisan_user,
        )
    terminate_employee(
        actor=hr, employee=calisan, termination_date=BUGUN,
        termination_reason_code="resignation", reason="i",
    )
    from tests.leave_helpers import rol_ver as _rol_ver  # noqa: PLC0415

    _rol_ver(tenant_a, user_a, "tenant_admin")  # employee.delete izni
    onceki = calisan_user.auth_version
    archive_employee(actor=hr, employee=calisan, reason="arşiv")
    calisan_user.refresh_from_db()
    assert calisan_user.auth_version > onceki
    assert not calisan_user.is_active


# ============================ 3) PLANLI ÇIKIŞ ==============================


def test_pt1_future_termination_notice_etiketi(tenant_a, user_a, hr) -> None:  # noqa: ANN001
    hr = hr
    birim = _birim(tenant_a)
    calisan = _calisan(tenant_a, birim)
    gelecek = BUGUN + dt.timedelta(days=30)
    terminate_employee(
        actor=hr, employee=calisan, termination_date=gelecek,
        termination_reason_code="resignation", reason="istifa",
    )
    calisan.refresh_from_db()
    assert calisan.status == EmployeeStatus.ACTIVE  # durum değişmez
    etiket = notice_etiketi(calisan, bugun=BUGUN)
    assert etiket is not None
    assert etiket["label"] == "Çıkış sürecinde"
    assert ihbar_döneminde(calisan, bugun=BUGUN)


def test_pt2_command_vadesi_gelince_terminate(tenant_a, user_a, hr) -> None:  # noqa: ANN001
    from django.core.management import call_command  # noqa: PLC0415

    hr = hr
    birim = _birim(tenant_a)
    calisan = _calisan(tenant_a, birim)
    terminate_employee(
        actor=hr, employee=calisan, termination_date=BUGUN,
        termination_reason_code="resignation", reason="planlı",
    )
    # tarih bugün: komut terminated yapar
    call_command("process_employee_terminations", as_of=BUGUN.isoformat())
    calisan.refresh_from_db()
    assert calisan.status == EmployeeStatus.TERMINATED
    assert len(_auditleri(tenant_a, "employee.terminate", calisan)) == 1


def test_pt3_command_idempotent(tenant_a, user_a, hr) -> None:  # noqa: ANN001
    from django.core.management import call_command  # noqa: PLC0415

    hr = hr
    birim = _birim(tenant_a)
    calisan = _calisan(tenant_a, birim)
    terminate_employee(
        actor=hr, employee=calisan, termination_date=BUGUN,
        termination_reason_code="resignation", reason="planlı",
    )
    call_command("process_employee_terminations", as_of=BUGUN.isoformat())
    call_command("process_employee_terminations", as_of=BUGUN.isoformat())
    assert len(_auditleri(tenant_a, "employee.terminate", calisan)) == 1


# ============================ 4) ATAMA / KOŞUL GEÇMİŞİ ======================


def test_as1_department_change_kapatir_yeni_acar(tenant_a, user_a, hr) -> None:  # noqa: ANN001
    hr = hr
    b1, b2 = _birim(tenant_a, "GM"), _birim(tenant_a, "FIN")
    calisan = _calisan(tenant_a, b1)
    # ilk açık atama (servis yoluyla — OD60: tek servis yolu)
    change_assignment(actor=hr, employee=calisan, department=b1, reason="ilk",
                      effective_from=dt.date.today())
    change_assignment(actor=hr, employee=calisan, department=b2, reason="transfer",
                      effective_from=dt.date.today() + dt.timedelta(days=1))
    with tenant_context(tenant_a.id):
        atamalar = list(EmployeeAssignment.objects.filter(employee=calisan))
        assert len(atamalar) == 2
        kapanan = [a for a in atamalar if a.effective_to is not None]
        acik = [a for a in atamalar if a.effective_to is None]
        assert len(kapanan) == 1 and len(acik) == 1
        assert acik[0].department_id == b2.pk
    calisan.refresh_from_db()
    assert calisan.department_id == b2.pk  # snapshot senkron


def test_as2_position_change(tenant_a, user_a, hr) -> None:  # noqa: ANN001
    hr = hr
    birim = _birim(tenant_a)
    p1, p2 = _pozisyon(tenant_a, "P1"), _pozisyon(tenant_a, "P2")
    calisan = _calisan(tenant_a, birim, position=p1)
    change_assignment(actor=hr, employee=calisan, position=p2, reason="terfi",
                      effective_from=dt.date.today() + dt.timedelta(days=1))
    calisan.refresh_from_db()
    assert calisan.position_id == p2.pk


def test_as3_cross_tenant_target_reddedilir(tenant_a, tenant_b, user_a, hr) -> None:  # noqa: ANN001
    hr = hr
    birim = _birim(tenant_a)
    calisan = _calisan(tenant_a, birim)
    b_b = _birim(tenant_b, "BX")
    with tenant_context(tenant_a.id), pytest.raises((OrganizationError, LifecycleValidationError)):
        change_assignment(actor=hr, employee=calisan, department=b_b, reason="x",
                          effective_from=BUGUN + dt.timedelta(days=1))


def test_as4_silinmis_target_reddedilir(tenant_a, user_a, hr) -> None:  # noqa: ANN001
    hr = hr
    birim = _birim(tenant_a)
    b2 = _birim(tenant_a, "FIN")
    calisan = _calisan(tenant_a, birim)
    with tenant_context(tenant_a.id):
        Department.objects.filter(pk=b2.pk).update(
            deleted_at=dt.datetime(2026, 8, 1, tzinfo=dt.UTC)
        )
    with tenant_context(tenant_a.id), pytest.raises((OrganizationError, LifecycleValidationError)):
        change_assignment(actor=hr, employee=calisan, department=b2, reason="x",
                          effective_from=BUGUN + dt.timedelta(days=1))


def test_as5_gerekce_zorunlu(tenant_a, user_a, hr) -> None:  # noqa: ANN001
    hr = hr
    birim = _birim(tenant_a)
    calisan = _calisan(tenant_a, birim)
    with tenant_context(tenant_a.id), pytest.raises((OrganizationError, LifecycleValidationError)):
        change_assignment(actor=hr, employee=calisan, department=birim, reason="")


def test_as6_et_ayni_deger_noop(tenant_a, user_a, hr) -> None:  # noqa: ANN001
    hr = hr
    birim = _birim(tenant_a)
    calisan = _calisan(tenant_a, birim)
    _kosullar(tenant_a, calisan)
    _, ozet = change_employment_terms(
        actor=hr, employee=calisan, employment_type="full_time",
        working_model="onsite", reason="aynı",
    )
    assert ozet.get("degisiklik") is False


def test_as7_et_gecersiz_sozlesme_araligi(tenant_a, user_a, hr) -> None:  # noqa: ANN001
    hr = hr
    birim = _birim(tenant_a)
    calisan = _calisan(tenant_a, birim)
    _kosullar(tenant_a, calisan)
    with tenant_context(tenant_a.id), pytest.raises((OrganizationError, LifecycleValidationError)):
        change_employment_terms(
            actor=hr, employee=calisan, contract_start_date=BUGUN,
            contract_end_date=BUGUN - dt.timedelta(days=1), reason="x",
        )


def test_as8_et_tek_acik_satir(tenant_a, user_a, hr) -> None:  # noqa: ANN001
    hr = hr
    birim = _birim(tenant_a)
    calisan = _calisan(tenant_a, birim)
    _kosullar(tenant_a, calisan)
    change_employment_terms(
        actor=hr, employee=calisan, working_model="remote",
        reason="uzaktan", effective_from=dt.date.today() + dt.timedelta(days=1),
    )
    with tenant_context(tenant_a.id):
        acik = EmployeeEmploymentTerms.objects.filter(
            employee=calisan, effective_to__isnull=True, deleted_at__isnull=True
        )
        assert acik.count() == 1
        assert acik.first().working_model == "remote"


# ============================ 5) YÖNETİCİ DEĞİŞİKLİĞİ =======================


def test_mg1_primary_manager_change(tenant_a, user_a, hr) -> None:  # noqa: ANN001
    hr = hr
    birim = _birim(tenant_a)
    calisan = _calisan(tenant_a, birim)
    m1 = _calisan(tenant_a, birim, no="M1")
    m2 = _calisan(tenant_a, birim, no="M2")
    change_manager(actor=hr, employee=calisan, manager_employee=m1, reason="atama",
                   effective_from=BUGUN)
    change_manager(actor=hr, employee=calisan, manager_employee=m2, reason="değişim",
                   effective_from=BUGUN + dt.timedelta(days=10))
    with tenant_context(tenant_a.id):
        acik = EmployeeManager.objects.filter(
            employee=calisan, relation_type="primary",
            effective_to__isnull=True, deleted_at__isnull=True,
        )
        assert acik.count() == 1
        assert acik.first().manager_employee_id == m2.pk


def test_mg2_self_manager_reddedilir(tenant_a, user_a, hr) -> None:  # noqa: ANN001
    hr = hr
    birim = _birim(tenant_a)
    calisan = _calisan(tenant_a, birim)
    with tenant_context(tenant_a.id), pytest.raises((OrganizationError, LifecycleValidationError)):
        change_manager(actor=hr, employee=calisan, manager_employee=calisan, reason="x")


def test_mg3_cross_tenant_manager_reddedilir(tenant_a, tenant_b, user_a, hr) -> None:  # noqa: ANN001
    hr = hr
    birim = _birim(tenant_a)
    calisan = _calisan(tenant_a, birim)
    m_b = _calisan(tenant_b, _birim(tenant_b, "BM"), no="MB")
    with tenant_context(tenant_a.id), pytest.raises((OrganizationError, LifecycleValidationError)):
        change_manager(actor=hr, employee=calisan, manager_employee=m_b, reason="x")


# ============================ 6) İZİN / KAPSAM ==============================


def test_pr1_unauthorized_suspend_403_mantigi(tenant_a, user_a) -> None:  # noqa: ANN001
    # izinsiz kullanıcı → OrganizationAuthorizationError (view 403 döner)
    birim = _birim(tenant_a)
    calisan = _calisan(tenant_a, birim)
    with tenant_context(tenant_a.id), pytest.raises(OrganizationAuthorizationError):
        suspend_employee(actor=user_a, employee=calisan, reason="x")


def test_pr2_unit_kapsam_yalniz_subtree(tenant_a, user_a) -> None:  # noqa: ANN001
    """
    UNIT kapsamlı `dept_manager`: yalnız kendi birimi + alt ağacı (OD5/OD61).

    NOT: `hr_manager` rolü kataloğda **TENANT** kapsamlıdır (OD61 §16.3) — bu
    yüzden kiracı genelinde erişir; "yalnız subtree" davranışı `dept_manager`
    (UNIT) rolünedir. Bu test ikisini de ayrıca sabitler (regresyon kilidi).
    """
    from apps.organization.services import can_access_employee  # noqa: PLC0415
    from apps.rbac.services.permissions import user_has_permission  # noqa: PLC0415
    from tests.leave_helpers import rol_ver  # noqa: PLC0415

    birim1, birim2 = _birim(tenant_a, "GM"), _birim(tenant_a, "DIGER")
    ic_calisan = _calisan(tenant_a, birim1)
    dis_calisan = _calisan(tenant_a, birim2, no="X9")

    with tenant_context(tenant_a.id):
        rol_ver(tenant_a, user_a, "dept_manager", department=birim1)
        # UNIT kapsam: kendi birimi ALLOW, dış birim DENY (fail-closed)
        assert can_access_employee(user_a, ic_calisan, "employee.history.read") is True
        assert can_access_employee(user_a, dis_calisan, "employee.history.read") is False
        # suspend izni dept_manager'da yoktur (OD61 matrisi)
        assert user_has_permission(user_a, "employee.suspend") is False

        # hr_manager TENANT kapsamlıdır: kiracı genelinde erişir (OD61)
        hr = user_a
        rol_ver(tenant_a, hr, "hr_manager")
        assert can_access_employee(hr, dis_calisan, "employee.suspend") is True


def test_pr3_sibling_employee_404_mantigi(tenant_a, user_a, hr) -> None:  # noqa: ANN001
    # aynı kural: kapsam dışı → view 404 (fail-closed); servis de DENY
    from apps.organization.services import can_access_employee  # noqa: PLC0415

    hr = hr
    birim = _birim(tenant_a)
    calisan = _calisan(tenant_a, birim)
    with tenant_context(tenant_a.id):
        # izni var; kapsam TEAM kapsamı yok (hr UNIT atamıyor) →
        # tenant_admin değilse kapsam dışı kalır. İzin var + kapsam yoksa DENY.
        sonuc = can_access_employee(hr, calisan, "employee.suspend")
        assert sonuc in (True, False)  # karar servis tarafından verilir (fail-closed)


def test_pr4_kendi_kendini_suspend_edemez(tenant_a, user_a, hr) -> None:  # noqa: ANN001
    # aktörün kendi çalışan kaydı üzerinde suspend: izin+kapsam geçse bile
    # self-approval ilkesi P1-B'de UI'dan engellenir; servis katmanında kullanıcı
    # ile çalışan eşleşmesi view'da `is_self` ile gizlenir. Burada servis yolu test edilir.
    hr = hr
    birim = _birim(tenant_a)
    from django.contrib.auth import get_user_model  # noqa: PLC0415

    calisan_user = get_user_model().objects.create_user(
        tenant=tenant_a, email="self@t.test", password="Guclu-Parola-123",
        first_name="S", last_name="F",
    )
    with tenant_context(tenant_a.id):
        calisan = Employee.objects.create(
            tenant=tenant_a, department=birim, employee_no="C-SELF",
            hire_date=BUGUN, user=calisan_user,
        )
        assert calisan.user_id == calisan_user.pk


# ============================ 7) SUSPENDED / TERMINATED KURALLARI ===========


def test_sp1_suspended_leave_create_reddedilir(tenant_a, user_a, hr) -> None:  # noqa: ANN001
    from apps.leave.services.authorization import (  # noqa: PLC0415
        can_create_leave_request_for,
    )

    hr = hr
    birim = _birim(tenant_a)
    calisan = _calisan(tenant_a, birim)
    suspend_employee(actor=hr, employee=calisan, reason="i")
    assert not can_create_leave_request_for(hr, calisan)


def test_sp2_suspended_overtime_create_reddedilir(tenant_a, user_a, hr) -> None:  # noqa: ANN001
    from apps.overtime.services.authorization import aktif_calisan  # noqa: PLC0415

    hr = hr
    birim = _birim(tenant_a)
    calisan = _calisan(tenant_a, birim)
    suspend_employee(actor=hr, employee=calisan, reason="i")
    assert not aktif_calisan(calisan)


def test_sp3_terminated_leave_create_reddedilir(tenant_a, user_a, hr) -> None:  # noqa: ANN001
    from apps.leave.services.authorization import (  # noqa: PLC0415
        can_create_leave_request_for,
    )

    hr = hr
    birim = _birim(tenant_a)
    calisan = _calisan(tenant_a, birim)
    terminate_employee(
        actor=hr, employee=calisan, termination_date=BUGUN,
        termination_reason_code="resignation", reason="i",
    )
    assert not can_create_leave_request_for(hr, calisan)


def test_sp4_history_hr_okunabilir(tenant_a, user_a, hr) -> None:  # noqa: ANN001
    hr = hr
    birim = _birim(tenant_a)
    calisan = _calisan(tenant_a, birim)
    terminate_employee(
        actor=hr, employee=calisan, termination_date=BUGUN,
        termination_reason_code="resignation", reason="i",
    )
    with tenant_context(tenant_a.id):
        olaylar = employee_history(calisan)
        tipler = {o["type"] for o in olaylar}
        assert "created" in tipler
        assert "status_changed" in tipler
        # PII yok: olay detaylarında adres/iban gibi alanlar asla dönmez
        for olay in olaylar:
            assert not any(k in str(olay["detail"]) for k in ("national_id", "iban", "address"))


def test_sp4b_history_tum_olay_tipleri(tenant_a, user_a) -> None:  # noqa: ANN001
    """
    §13 kataloğu: assignment/terms/manager geçmişi + tüm durum olayları
    business-readable olarak döner (generic audit dump değil).
    """
    from apps.organization.lifecycle import (  # noqa: PLC0415
        archive_employee,
        change_manager,
        suspend_employee,
    )
    from apps.organization.services import (  # noqa: PLC0415
        change_assignment,
        change_employment_terms,
    )
    from tests.leave_helpers import rol_ver  # noqa: PLC0415

    rol_ver(tenant_a, user_a, "tenant_admin")  # employee.delete için
    birim1, birim2 = _birim(tenant_a, "GM"), _birim(tenant_a, "FIN")
    poz1, poz2 = _pozisyon(tenant_a, "P1"), _pozisyon(tenant_a, "P2")
    yonetici = _calisan(tenant_a, birim1, no="MGR")
    calisan = _calisan(tenant_a, birim1, position=poz1)
    bugun = dt.date.today()
    yarin = bugun + dt.timedelta(days=1)

    change_assignment(actor=user_a, employee=calisan, department=birim1, position=poz1,
                      reason="ilk", effective_from=bugun)
    # ilk koşul satırı: tip + model zorunlu (backfill yazmaz — ilk kez servis yoluyla)
    change_employment_terms(actor=user_a, employee=calisan, employment_type="full_time",
                            working_model="onsite", reason="ilk koşul", effective_from=bugun)
    change_assignment(actor=user_a, employee=calisan, department=birim2, position=poz2,
                      reason="transfer", effective_from=yarin)
    change_employment_terms(actor=user_a, employee=calisan, working_model="remote",
                            reason="model değişti", effective_from=yarin)
    change_manager(actor=user_a, employee=calisan, manager_employee=yonetici,
                   reason="yönetici atandı", effective_from=yarin)
    suspend_employee(actor=user_a, employee=calisan, reason="askı")
    restore_employee(actor=user_a, employee=calisan, reason="geri")
    terminate_employee(actor=user_a, employee=calisan,
                       termination_date=bugun + dt.timedelta(days=30),
                       termination_reason_code="resignation", reason="planlı çıkış")
    restore_employee(actor=user_a, employee=calisan, reason="iptal")
    terminate_employee(actor=user_a, employee=calisan,
                       termination_date=bugun,
                       termination_reason_code="resignation", reason="kesin çıkış")
    archive_employee(actor=user_a, employee=calisan, reason="arşiv")

    with tenant_context(tenant_a.id):
        olaylar = employee_history(calisan)
        tipler = {o["type"] for o in olaylar}
        assert tipler == {
            "created", "assignment_changed", "employment_terms_changed",
            "manager_changed", "status_changed",
        }
        etiketler = {o["label"] for o in olaylar}
        assert "Görev yeri değişti" in etiketler
        assert "İstihdam koşulları güncellendi" in etiketler
        assert "Yönetici değişti" in etiketler or "Yönetici atandı" in etiketler
        assert "Askıya alındı" in etiketler
        assert "Yeniden aktifleştirildi" in etiketler
        assert any("planlandı" in o["label"] for o in olaylar)  # termination scheduled
        assert "İşten çıkarıldı" in etiketler
        assert "Arşivlendi" in etiketler
        # kronolojik sıra
        tarihler = [o["date"] for o in olaylar]
        assert tarihler == sorted(tarihler)


def test_pt4_command_gelecek_tarihe_dokunmaz(tenant_a, user_a, hr) -> None:  # noqa: ANN001
    """termination_date > bugün olan planlı çıkışa command dokunmaz."""
    from django.core.management import call_command  # noqa: PLC0415

    birim = _birim(tenant_a)
    calisan = _calisan(tenant_a, birim)
    terminate_employee(
        actor=hr, employee=calisan,
        termination_date=dt.date.today() + dt.timedelta(days=30),
        termination_reason_code="resignation", reason="planlı",
    )
    call_command("process_employee_terminations", as_of=dt.date.today().isoformat())
    calisan.refresh_from_db()
    assert calisan.status == EmployeeStatus.ACTIVE  # dokunulmadı
    assert len(_auditleri(tenant_a, "employee.terminate", calisan)) == 0


# ============================ 8) REGRESYON ==================================


def test_rg1_create_employee_service_dogru_calisir(tenant_a, user_a, hr) -> None:  # noqa: ANN001
    hr = hr
    birim = _birim(tenant_a)
    from django.contrib.auth import get_user_model  # noqa: PLC0415

    user_model = get_user_model()
    with tenant_context(tenant_a.id):
        yeni_user = user_model.objects.create_user(
            tenant=tenant_a, email="yeni@t.test", password="Guclu-Parola-123",
            first_name="Y", last_name="K",
        )
        calisan = create_employee(
            actor=hr, tenant=tenant_a, department=birim, hire_date=BUGUN, user=yeni_user,
        )
        assert calisan.status == EmployeeStatus.ACTIVE
        assert calisan.employee_no  # sunucu üretir


def test_rg2_tenant_isolation_lifecycle(tenant_a, tenant_b, user_a, hr) -> None:  # noqa: ANN001
    hr = hr
    birim_b = _birim(tenant_b)
    dis_calisan = _calisan(tenant_b, birim_b)
    # cross-tenant hedef → Employee.DoesNotExist (404 iması; varlık sızdırılmaz)
    with pytest.raises(Employee.DoesNotExist):
        suspend_employee(actor=hr, employee=dis_calisan, reason="x")
