"""
M14 — izin modülü **akış** testleri: talep yaşam döngüsü, onay sırası, kapsam/yetki,
bakiye davranışı, yarış (idempotans), denetim, RLS ve platform erişim yasağı.

Kapsanan istek senaryoları: 8, 9, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 23, 24,
25, 26, 27.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from decimal import Decimal

import pytest
from django.conf import settings
from django.db import DatabaseError, connection, transaction
from django.utils import timezone

from apps.audit.models import AuditLog
from apps.leave.models import (
    ApprovalStep,
    ApprovalStepStatus,
    BalanceEntryKind,
    LeaveBalance,
    LeaveBalanceEntry,
    LeavePolicy,
    LeaveRequest,
    LeaveRequestStatus,
)
from apps.leave.services import (
    LeaveAuthorizationError,
    LeavePolicyError,
    LeaveStateError,
    can_approve_step,
    can_cancel_leave_request,
    can_create_leave_request_for,
    can_view_leave_request,
    cancel_leave_request,
    create_leave_request,
    current_step,
    decide_approval_step,
    mark_leave_taken,
    submit_leave_request,
    update_leave_policy,
)
from apps.rbac.models import UserRole
from apps.tenancy.context import tenant_context
from tests.leave_helpers import (
    LEAVE_TABLOLARI,
    adimlar,
    calisan,
    kadro,
    rol_ver,
    talep,
)


@pytest.fixture(autouse=True)
def _kiraci_baglami(request):  # noqa: ANN001, ANN202
    """`tenant_a` kullanan testler onun kiracı bağlamında çalışır (FORCE RLS)."""
    if "tenant_a" not in request.fixturenames:
        yield
        return
    kiracı = request.getfixturevalue("tenant_a")
    with tenant_context(kiracı.id):
        yield


def _bakiye(request: LeaveRequest) -> LeaveBalance:
    with tenant_context(request.tenant_id):
        return LeaveBalance.objects.get(employee_id=request.employee_id)


def _onayla(request: LeaveRequest, actor, **kw):  # noqa: ANN001, ANN202
    with tenant_context(request.tenant_id):
        return decide_approval_step(actor=actor, request=request, **kw)


# --- talep oluşturma ve görünürlük -------------------------------------------


def test_m14_calisan_kendi_talebini_olusturur(tenant_a, user_a) -> None:  # noqa: ANN001
    """(8) Çalışan kendi adına talep açar; görünürlük SELF ile sağlanır."""
    lot = kadro(tenant_a, user_a)
    request = talep(lot)
    assert request.status == LeaveRequestStatus.DRAFT
    assert request.days_requested == Decimal("3.0")
    assert can_create_leave_request_for(lot["personel_user"], lot["personel"]) is True
    assert can_view_leave_request(lot["personel_user"], request) is True
    with tenant_context(tenant_a.id):
        assert AuditLog.objects.filter(action="leave.request.create").count() == 1


def test_m14_baska_calisan_adina_talep_yetkisiz(tenant_a, user_a) -> None:  # noqa: ANN001
    """(9) Kapsamsız/izinsiz kullanıcı başka çalışan adına talep açamaz."""
    lot = kadro(tenant_a, user_a)
    yabanci = calisan(tenant_a, lot["birimler"]["gm"], no="GM-9")
    assert can_create_leave_request_for(lot["personel_user"], yabanci) is False
    with pytest.raises(LeaveAuthorizationError, match="leave.request.create"):
        with tenant_context(tenant_a.id):
            create_leave_request(
                actor=lot["personel_user"],
                employee=yabanci,
                leave_type=lot["turler"]["annual_leave"],
                start_date=timezone.localdate() + timedelta(days=5),
                end_date=timezone.localdate() + timedelta(days=6),
            )


# --- onay akışı ve bakiye davranışı ------------------------------------------


def test_m14_onay_bakiyeyi_bir_kez_tuketir(tenant_a, user_a) -> None:  # noqa: ANN001
    """(11) Onaya sunum rezerve eder; nihai onay bakiyeyi **bir kez** tüketir."""
    lot = kadro(tenant_a, user_a)
    request = talep(lot, gun=3, submit=True)

    # Onaya sunuldu: rezerve edildi, tüketilmedi.
    bakiye = _bakiye(request)
    assert bakiye.reserved_days == Decimal("3.0")
    assert bakiye.used_days == Decimal("0.0")
    assert bakiye.available_days == Decimal("11.0")
    with tenant_context(tenant_a.id):
        assert (
            LeaveBalanceEntry.objects.filter(entry_kind=BalanceEntryKind.RESERVATION).count() == 1
        )

    # 1. adım: birincil yönetici.
    adim_listesi = adimlar(request)
    assert [a.step_no for a in adim_listesi] == [1, 2]
    assert adim_listesi[0].approver_user_id == lot["yonetici_user"].pk
    assert adim_listesi[1].approver_role_code == "hr_manager"

    ara = _onayla(request, lot["yonetici_user"], decision=ApprovalStepStatus.APPROVED)
    assert ara.status == LeaveRequestStatus.PENDING
    bakiye.refresh_from_db()
    assert bakiye.used_days == Decimal("0.0")
    assert bakiye.reserved_days == Decimal("3.0")

    # 2. adım: kiracı geneli yetkili (tenant_admin ● TENANT).
    onayli = _onayla(request, lot["admin"], decision=ApprovalStepStatus.APPROVED)
    assert onayli.status == LeaveRequestStatus.APPROVED
    assert onayli.decided_at is not None
    assert onayli.decided_by_id == lot["admin"].pk

    bakiye.refresh_from_db()
    assert bakiye.reserved_days == Decimal("0.0")
    assert bakiye.used_days == Decimal("3.0")
    assert bakiye.available_days == Decimal("11.0")
    with tenant_context(tenant_a.id):
        assert LeaveBalanceEntry.objects.filter(entry_kind=BalanceEntryKind.USAGE).count() == 1
        assert AuditLog.objects.filter(action="leave.request.approve").count() == 1


def test_m14_red_bakiyeyi_tuketmez(tenant_a, user_a) -> None:  # noqa: ANN001
    """(12) Red: rezervasyon çözülür, kullanım yazılmaz (bakiye tüketilmez)."""
    lot = kadro(tenant_a, user_a)
    request = talep(lot, gun=4, submit=True)

    reddedilen = _onayla(
        request,
        lot["yonetici_user"],
        decision=ApprovalStepStatus.REJECTED,
        reason="Ekip kapasitesi yetersiz",
    )
    assert reddedilen.status == LeaveRequestStatus.REJECTED
    bakiye = _bakiye(request)
    bakiye.refresh_from_db()
    assert bakiye.used_days == Decimal("0.0")
    assert bakiye.reserved_days == Decimal("0.0")
    assert bakiye.available_days == Decimal("14.0")
    with tenant_context(tenant_a.id):
        assert LeaveBalanceEntry.objects.filter(entry_kind=BalanceEntryKind.USAGE).count() == 0
        assert (
            LeaveBalanceEntry.objects.filter(
                entry_kind=BalanceEntryKind.RESERVATION_RELEASE
            ).count()
            == 1
        )
        assert AuditLog.objects.filter(action="leave.request.reject").count() == 1


def test_m14_yetersiz_bakiye_rezerve_edilemez(tenant_a, user_a) -> None:  # noqa: ANN001
    """Bakiye yetersizse talep onaya sunulamaz (rezervasyon da yapılamaz)."""
    lot = kadro(tenant_a, user_a)
    request = talep(lot, gun=3)
    # Yıllık hakediş 14 gün; talebi 20 güne çıkar (istemci değeriyle değil, doğrudan kayıt).
    with tenant_context(tenant_a.id):
        LeaveRequest.objects.filter(pk=request.pk).update(days_requested=Decimal("20.0"))
    with pytest.raises(Exception, match="Yetersiz izin bakiyesi"):
        with tenant_context(tenant_a.id):
            submit_leave_request(actor=lot["personel_user"], request=request)


def test_m14_iptal_davranisi(tenant_a, user_a) -> None:  # noqa: ANN001
    """(13) İptal: taslak/rezervasyon/onaylı iade davranışı ve politika sınırı."""
    lot = kadro(tenant_a, user_a)

    # (a) Taslak iptali: bakiye etkisi yok.
    taslak = talep(lot)
    iptal = cancel_leave_request(
        actor=lot["personel_user"], request=taslak, reason="Planım değişti"
    )
    assert iptal.status == LeaveRequestStatus.CANCELED
    assert iptal.canceled_at is not None
    with tenant_context(tenant_a.id):
        assert LeaveBalanceEntry.objects.filter(leave_request__isnull=False).count() == 0

    # (b) Onay bekleyen iptali: rezervasyon çözülür, tüketim yok.
    bekleyen = talep(lot, gun=2, submit=True)
    bakiye = _bakiye(bekleyen)
    bakiye.refresh_from_db()
    assert bakiye.reserved_days == Decimal("2.0")
    cancel_leave_request(actor=lot["personel_user"], request=bekleyen, reason="Vazgeçtim")
    bakiye.refresh_from_db()
    assert bakiye.reserved_days == Decimal("0.0")
    assert bakiye.used_days == Decimal("0.0")

    # (c) Onaylı iptal: politika izin veriyorsa açık `refund` kaydı ile iade.
    onayli = talep(lot, gun=2, submit=True)
    _onayla(onayli, lot["yonetici_user"], decision=ApprovalStepStatus.APPROVED)
    _onayla(onayli, lot["admin"], decision=ApprovalStepStatus.APPROVED)
    bakiye = _bakiye(onayli)
    bakiye.refresh_from_db()
    assert bakiye.used_days == Decimal("2.0")
    cancel_leave_request(actor=lot["admin"], request=onayli, reason="Proje planı değişti")
    bakiye.refresh_from_db()
    assert bakiye.used_days == Decimal("0.0")
    with tenant_context(tenant_a.id):
        assert LeaveBalanceEntry.objects.filter(entry_kind=BalanceEntryKind.REFUND).count() == 1
        assert AuditLog.objects.filter(action="leave.request.cancel").count() == 3

    # (d) Politika iadeyi yasaklarsa onaylı talep iptal edilemez (fail-closed).
    with tenant_context(tenant_a.id):
        policy = LeavePolicy.objects.get(leave_type=lot["turler"]["annual_leave"])
    update_leave_policy(
        actor=lot["admin"],
        policy=policy,
        allow_refund_after_approval=False,
        reason="politika değişikliği",
    )
    onayli2 = talep(lot, gun=1, submit=True)
    _onayla(onayli2, lot["yonetici_user"], decision=ApprovalStepStatus.APPROVED)
    _onayla(onayli2, lot["admin"], decision=ApprovalStepStatus.APPROVED)
    with pytest.raises(LeavePolicyError, match="iadeye izin vermiyor"):
        cancel_leave_request(actor=lot["admin"], request=onayli2, reason="İptal denemesi")


def test_m14_ayni_talep_ikinci_kez_onaylanamaz(tenant_a, user_a) -> None:  # noqa: ANN001
    """(14) Nihai onaydan sonra tekrar karar verilemez (durum kapalı küme)."""
    lot = kadro(tenant_a, user_a)
    request = talep(lot, gun=2, submit=True)
    _onayla(request, lot["yonetici_user"], decision=ApprovalStepStatus.APPROVED)
    _onayla(request, lot["admin"], decision=ApprovalStepStatus.APPROVED)
    with pytest.raises(LeaveStateError, match="onay bekleyen"):
        _onayla(request, lot["admin"], decision=ApprovalStepStatus.APPROVED)
    bakiye = _bakiye(request)
    bakiye.refresh_from_db()
    assert bakiye.used_days == Decimal("2.0")


def test_m14_cift_tuketim_iki_katmanda_engellenir(tenant_a, user_a) -> None:  # noqa: ANN001
    """
    (15) Çift tüketim engeli — iki katman birlikte:

    * **Servis:** onaylanmış talep ikinci kez karara bağlanamaz (durum kapalı küme)
      → bakiye ikinci kez düşemez.
    * **Veritabanı:** olay defterinin `(leave_request_id, entry_kind)` benzersiz
      kısıtı aynı talep için ikinci `usage` kaydını reddeder → eşzamanlı iki onay
      denemesinde bile "çift tüketim" yazılamaz.

    NOT (ölçülen kısıt): Gerçek iki bağlantılı yarış testi (`transactional_db`) bu
    projenin test ortamında çalıştırılamıyor — `pytest-django` teardown'ında
    çalıştırdığı `flush` komutu `hr.permissions` / `hr.role_permissions` TRUNCATE
    sırasını çözemiyor ("cannot truncate a table referenced in a foreign key
    constraint"; ayrıca `hr.tenant_directory` gibi ham SQL tablolar Django tablo
    listesinde yok). Bu nedenle garanti, kısıtların **doğrudan** sınanmasıyla
    gösterilir; kilit mekanizması `select_for_update()` + durum kontrolüdür.
    """
    lot = kadro(tenant_a, user_a)
    request = talep(lot, gun=3, submit=True)
    _onayla(request, lot["yonetici_user"], decision=ApprovalStepStatus.APPROVED)
    _onayla(request, lot["admin"], decision=ApprovalStepStatus.APPROVED)

    # 1) Servis katmanı: ikinci onay denemesi reddedilir.
    with pytest.raises(LeaveStateError, match="onay bekleyen"):
        _onayla(request, lot["admin"], decision=ApprovalStepStatus.APPROVED)
    bakiye = _bakiye(request)
    bakiye.refresh_from_db()
    assert bakiye.used_days == Decimal("3.0")
    assert bakiye.reserved_days == Decimal("0.0")

    # 2) Veritabanı katmanı: aynı talep için ikinci kullanım kaydı yazılamaz.
    with (
        tenant_context(tenant_a.id),
        pytest.raises(DatabaseError, match="leave_balance_entries_request_uniq"),
        transaction.atomic(),
    ):
        LeaveBalanceEntry.objects.create(
            tenant_id=bakiye.tenant_id,
            balance=bakiye,
            employee_id=bakiye.employee_id,
            leave_type_id=bakiye.leave_type_id,
            period_year=bakiye.period_year,
            entry_kind=BalanceEntryKind.USAGE,
            delta_days=Decimal("3.0"),
            leave_request=request,
        )


def test_m14_adim_sirasi_atlanamaz(tenant_a, user_a) -> None:  # noqa: ANN001
    """(§8) Sonraki adım, önceki adım karara bağlanmadan karar veremez."""
    lot = kadro(tenant_a, user_a)
    request = talep(lot, gun=2, submit=True)
    adim_listesi = adimlar(request)
    ikinci = adim_listesi[1]
    with (
        tenant_context(tenant_a.id),
        pytest.raises(DatabaseError, match="sirasi"),
        transaction.atomic(),
    ):
        ApprovalStep.objects.filter(pk=ikinci.pk).update(
            status=ApprovalStepStatus.APPROVED, decided_at=timezone.now()
        )
    # Servis her zaman **en küçük** karar bekleyen adımı seçer → sıra atlanamaz.
    request.refresh_from_db()
    assert current_step(request).step_no == 1
    _onayla(request, lot["yonetici_user"], decision=ApprovalStepStatus.APPROVED)
    request.refresh_from_db()
    assert current_step(request).step_no == 2
    _onayla(request, lot["admin"], decision=ApprovalStepStatus.APPROVED)
    request.refresh_from_db()
    assert current_step(request) is None
    assert request.status == LeaveRequestStatus.APPROVED


def test_m14_override_gerekce_ve_izin_ister(tenant_a, user_a) -> None:  # noqa: ANN001
    """(§8) Override: izin + kayıtlı gerekçe; adımlar atlanır, bakiye bir kez tüketilir."""
    lot = kadro(tenant_a, user_a)
    request = talep(lot, gun=2, submit=True)
    with pytest.raises(LeaveAuthorizationError, match="override"):
        _onayla(
            request, lot["yonetici_user"], decision=ApprovalStepStatus.APPROVED, override=True,
            reason="acil durum",
        )
    with pytest.raises(Exception, match="gerekçe"):
        _onayla(
            request, lot["admin"], decision=ApprovalStepStatus.APPROVED, override=True, reason="x"
        )

    onayli = _onayla(
        request,
        lot["admin"],
        decision=ApprovalStepStatus.APPROVED,
        override=True,
        reason="İK acil onayı (yönetici ulaşılamıyor)",
    )
    assert onayli.status == LeaveRequestStatus.APPROVED
    with tenant_context(tenant_a.id):
        assert not ApprovalStep.objects.filter(
            subject_id=request.pk, status=ApprovalStepStatus.PENDING
        ).exists()
        assert LeaveBalanceEntry.objects.filter(entry_kind=BalanceEntryKind.USAGE).count() == 1
        assert AuditLog.objects.filter(action="leave.request.override").count() == 1


def test_m14_belge_zorunlu_tur_belgesiz_onaylanamaz(tenant_a, user_a) -> None:  # noqa: ANN001
    """(24) `requires_document` türünde belge yoksa onay **fail-closed** reddedilir."""
    lot = kadro(tenant_a, user_a)
    bugun = timezone.localdate()
    with tenant_context(tenant_a.id):
        request = create_leave_request(
            actor=lot["personel_user"],
            employee=lot["personel"],
            leave_type=lot["turler"]["sick_leave"],
            start_date=bugun,
            end_date=bugun + timedelta(days=1),
            reason="Raporlu hastalık izni",
            submit=True,
        )
    assert request.leave_type.requires_document is True

    # 1. adım (yönetici) belge beklemez; **nihai** onay belgesiz verilemez.
    _onayla(request, lot["yonetici_user"], decision=ApprovalStepStatus.APPROVED)
    with pytest.raises(LeaveStateError, match="belge zorunlu"):
        _onayla(request, lot["admin"], decision=ApprovalStepStatus.APPROVED)

    # DB guard'ı: servisi atlayan doğrudan yazım da reddedilir.
    with (
        tenant_context(tenant_a.id),
        pytest.raises(DatabaseError, match="Belge"),
        transaction.atomic(),
    ):
        LeaveRequest.objects.filter(pk=request.pk).update(
            status=LeaveRequestStatus.APPROVED,
            decided_at=timezone.now(),
            decided_by_id=lot["admin"].pk,
        )

    # Belge referansı verilirse (M16 `hr.documents` entegrasyon noktası) akış işler.
    with tenant_context(tenant_a.id):
        LeaveRequest.objects.filter(pk=request.pk).update(document_ref=uuid.uuid4())
        request.refresh_from_db()
    onayli = _onayla(request, lot["admin"], decision=ApprovalStepStatus.APPROVED)
    assert onayli.status == LeaveRequestStatus.APPROVED


def test_m14_kullanildi_isaretleme(tenant_a, user_a) -> None:  # noqa: ANN001
    """Onaylı talep izin dönemi geldiğinde 'kullanıldı' işaretlenir (bakiye etkisi yok)."""
    lot = kadro(tenant_a, user_a)
    bugun = timezone.localdate()
    with tenant_context(tenant_a.id):
        request = create_leave_request(
            actor=lot["personel_user"],
            employee=lot["personel"],
            leave_type=lot["turler"]["annual_leave"],
            start_date=bugun,
            end_date=bugun,
            reason="Tek gün izin",
            submit=True,
        )
    _onayla(request, lot["yonetici_user"], decision=ApprovalStepStatus.APPROVED)
    _onayla(request, lot["admin"], decision=ApprovalStepStatus.APPROVED)
    bakiye = _bakiye(request)
    bakiye.refresh_from_db()
    assert bakiye.used_days == Decimal("1.0")

    with tenant_context(tenant_a.id):
        isaretli = mark_leave_taken(actor=lot["admin"], request=request)
    assert isaretli.status == LeaveRequestStatus.TAKEN
    bakiye.refresh_from_db()
    assert bakiye.used_days == Decimal("1.0")
    with tenant_context(tenant_a.id):
        assert AuditLog.objects.filter(action="leave.request.taken").count() == 1


# --- kapsam ve yetki (organizasyon + RBAC) -----------------------------------


def test_m14_dept_manager_subtree_talebini_gorur_onaylar(tenant_a, user_a) -> None:  # noqa: ANN001
    """(16) dept_manager: UNIT alt ağacındaki talep → görür ve onaylar."""
    lot = kadro(tenant_a, user_a)
    request = talep(lot, gun=2, submit=True)
    adim = current_step(request)
    assert can_view_leave_request(lot["yonetici_user"], request) is True
    assert can_approve_step(lot["yonetici_user"], adim, request) is True
    onayli = _onayla(request, lot["yonetici_user"], decision=ApprovalStepStatus.APPROVED)
    assert onayli.status == LeaveRequestStatus.PENDING  # ikinci adım bekliyor


def test_m14_kardes_birim_talebi_deny(tenant_a, user_a) -> None:  # noqa: ANN001
    """(17) Sibling birimdeki yönetici kapsam dışındaki talebi göremez/onaylayamaz."""
    lot = kadro(tenant_a, user_a)
    request = talep(lot, gun=2, submit=True)
    adim = current_step(request)
    assert can_view_leave_request(lot["kardes_user"], request) is False
    assert can_approve_step(lot["kardes_user"], adim, request) is False
    with pytest.raises(LeaveAuthorizationError):
        _onayla(request, lot["kardes_user"], decision=ApprovalStepStatus.APPROVED)


def test_m14_permission_olmadan_manager_deny(tenant_a, user_a) -> None:  # noqa: ANN001
    """(18) Birincil yönetici olmak yetki üretmez: izin kodu yoksa onay DENY."""
    lot = kadro(tenant_a, user_a)
    # Yönetici kullanıcının `dept_manager` rolünü kaldır, yalnız `employee` bırak.
    with tenant_context(tenant_a.id):
        UserRole.objects.filter(user=lot["yonetici_user"]).delete()
    rol_ver(tenant_a, lot["yonetici_user"], "employee")

    request = talep(lot, gun=2, submit=True)
    adim = current_step(request)
    assert can_approve_step(lot["yonetici_user"], adim, request) is False
    with pytest.raises(LeaveAuthorizationError, match="leave.request.approve"):
        _onayla(request, lot["yonetici_user"], decision=ApprovalStepStatus.APPROVED)


def test_m14_kiraci_geneli_yetkili_rol_onaylar(tenant_a, user_a) -> None:  # noqa: ANN001
    """(19) TENANT kapsamlı yetkili rol (tenant_admin) talep akışını yürütür."""
    lot = kadro(tenant_a, user_a)
    request = talep(lot, gun=2, submit=True)
    adim = current_step(request)
    assert can_approve_step(lot["admin"], adim, request) is True
    _onayla(request, lot["admin"], decision=ApprovalStepStatus.APPROVED)
    onayli = _onayla(request, lot["admin"], decision=ApprovalStepStatus.APPROVED)
    assert onayli.status == LeaveRequestStatus.APPROVED


def test_m14_cross_tenant_talep_deny(tenant_a, tenant_b, user_a, user_b) -> None:  # noqa: ANN001
    """(20) Başka kiracının talebi görünmez ve onaylanamaz."""
    lot = kadro(tenant_a, user_a)
    with tenant_context(tenant_b.id):
        rol_ver(tenant_b, user_b, "tenant_admin")
    request = talep(lot, gun=2, submit=True)
    adim = current_step(request)

    with tenant_context(tenant_b.id):
        assert LeaveRequest.objects.filter(pk=request.pk).count() == 0
        assert can_view_leave_request(user_b, request) is False
        assert can_approve_step(user_b, adim, request) is False
        # RLS: talep başka kiracıda **görünmez** → servis nesneyi bulamaz (fail-closed).
        with pytest.raises(LeaveRequest.DoesNotExist):
            decide_approval_step(
                actor=user_b, request=request, decision=ApprovalStepStatus.APPROVED
            )


def test_m14_suresi_dolmus_ve_silinmis_atama_yetki_uretmez(tenant_a, user_a) -> None:  # noqa: ANN001
    """(23) Süresi geçmiş / silinmiş rol ataması onay yetkisi üretmez."""
    lot = kadro(tenant_a, user_a)
    request = talep(lot, gun=2, submit=True)
    adim = current_step(request)
    assert can_approve_step(lot["yonetici_user"], adim, request) is True

    with tenant_context(tenant_a.id):
        UserRole.objects.filter(user=lot["yonetici_user"]).update(expires_at=timezone.now())
    assert can_approve_step(lot["yonetici_user"], adim, request) is False

    with tenant_context(tenant_a.id):
        UserRole.objects.filter(user=lot["yonetici_user"]).update(
            expires_at=None, deleted_at=timezone.now()
        )
    assert can_approve_step(lot["yonetici_user"], adim, request) is False
    assert can_view_leave_request(lot["yonetici_user"], request) is False


def test_m14_self_kapsam_kendi_talebiyle_sinirli(tenant_a, user_a) -> None:  # noqa: ANN001
    """Çalışan (SELF) yalnızca kendi talebini iptal edebilir; başkasınınkini edemez."""
    lot = kadro(tenant_a, user_a)
    kendi = talep(lot, gun=2)
    assert can_cancel_leave_request(lot["personel_user"], kendi) is True
    assert can_cancel_leave_request(lot["kardes_user"], kendi) is False


# --- denetim, RLS, yetkiler --------------------------------------------------


def test_m14_denetim_kayitlari_pii_icermez(tenant_a, user_a) -> None:  # noqa: ANN001
    """(25) Akışın denetim olayları yazılır; serbest metin gerekçe **audit'e yazılmaz**."""
    lot = kadro(tenant_a, user_a)
    request = talep(lot, gun=2, submit=True)
    _onayla(
        request,
        lot["yonetici_user"],
        decision=ApprovalStepStatus.REJECTED,
        reason="Özel gerekçe: sağlık durumu K2",
    )
    with tenant_context(tenant_a.id):
        eylemler = set(AuditLog.objects.values_list("action", flat=True))
        assert {"leave.request.create", "leave.request.submit", "leave.request.reject"} <= eylemler
        red = AuditLog.objects.get(action="leave.request.reject")
        assert red.changes.get("has_reason") is True
        assert "reason" not in red.changes
        # Serbest metin gerekçe (K2 olabilir) hiçbir denetim kaydında yer almaz.
        for kayit in AuditLog.objects.all():
            assert "sağlık" not in str(kayit.changes)
            assert "reason" not in kayit.changes


def test_m14_yeni_tablolarda_rls_force_aktif(db) -> None:  # noqa: ANN001, ARG001
    """(26) Yedi yeni tabloda RLS + FORCE RLS ve `tenant_isolation` politikası vardır."""
    with connection.cursor() as cur:
        for tablo in LEAVE_TABLOLARI:
            cur.execute(
                "SELECT c.relrowsecurity, c.relforcerowsecurity FROM pg_class c "
                "JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname='hr' AND c.relname=%s",
                [tablo],
            )
            rls, force = cur.fetchone()
            assert (rls, force) == (True, True), f"hr.{tablo} RLS/FORCE eksik"
            cur.execute(
                "SELECT count(*) FROM pg_policies WHERE schemaname='hr' AND tablename=%s "
                "AND policyname='tenant_isolation'",
                [tablo],
            )
            assert cur.fetchone()[0] == 1, f"hr.{tablo} tenant_isolation politikası yok"
        cur.execute(
            "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
            "WHERE n.nspname='hr' AND c.relkind='r' AND c.relforcerowsecurity"
        )
        assert cur.fetchone()[0] >= 19


def test_m14_platform_rol_leave_tablolarina_erisemez(db) -> None:  # noqa: ANN001, ARG001
    """(27) `persopyro_platform` izin tablolarında hiçbir yetkiye sahip olamaz."""
    with connection.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", [settings.PLATFORM_DB_ROLE])
        if cur.fetchone() is None:
            pytest.skip(f"{settings.PLATFORM_DB_ROLE} rolü yok (süper kullanıcı oluşturur).")
        for tablo in LEAVE_TABLOLARI:
            for yetki in ("SELECT", "INSERT", "UPDATE", "DELETE"):
                cur.execute(
                    "SELECT has_table_privilege(%s, %s, %s)",
                    [settings.PLATFORM_DB_ROLE, f"hr.{tablo}", yetki],
                )
                assert cur.fetchone()[0] is False, f"platform hr.{tablo} {yetki}"


def test_m14_app_rolu_leave_yetkileri_minimum(db) -> None:  # noqa: ANN001, ARG001
    """(15/§15) Runtime rolü: soft-delete tablolarında DELETE yok; defter append-only."""
    with connection.cursor() as cur:
        for tablo in LEAVE_TABLOLARI:
            cur.execute(
                "SELECT has_table_privilege('persopyro_app', %s, 'DELETE'), "
                "has_table_privilege('persopyro_app', %s, 'TRUNCATE')",
                [f"hr.{tablo}", f"hr.{tablo}"],
            )
            sil, truncate = cur.fetchone()
            assert (sil, truncate) == (False, False), f"app rolü hr.{tablo} silme yetkisi almamalı"
        cur.execute(
            "SELECT has_table_privilege('persopyro_app', 'hr.leave_balance_entries', 'UPDATE')"
        )
        assert cur.fetchone()[0] is False, "bakiye defteri append-only olmalı (UPDATE verilmez)"
