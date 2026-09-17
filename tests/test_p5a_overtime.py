"""
P5a V1 — fazla mesai (overtime): talep + onay temeli testleri.

Kapsam (kullanıcı bildirimi §5–§10 + §13/11–22):

  * active çalışan **kendi** bildirimini açar; başka çalışan adına açamaz.
  * `total_minutes` **sunucuda** hesaplanır (istemci değeri yalnız doğrulanır) ve
    DB `CHECK`'i sunucu hesabıyla uyuşmayan değeri reddeder.
  * Geçersiz saat aralığı (negatif/0) reddedilir; aşırı süreye üst sınır vardır.
  * Çalışan yalnız kendi kayıtlarını; yönetici yalnız kapsamındakileri görür.
  * Self-approval yasaktır; onay/ret iki adımlı akışla ilerler (OD39), iptal
    gerekçe ister, karara bağlanan kayıt geri alınamaz.
  * Kiracı izolasyonu (RLS + kapsam) korunur; denetim kayıtları yazılır.

Testler hem **servis** hem **HTTP** yolunu dener (arayüz servisleri çağırır; güvenlik
kararı servis + RLS katmanındadır).
"""

from __future__ import annotations

from datetime import date, time, timedelta
from pathlib import Path

import pytest
from django.db import IntegrityError, connection, transaction
from django.utils import timezone

from apps.leave.models import ApprovalStep, ApprovalStepStatus
from apps.overtime.models import (
    OVERTIME_MAX_MINUTES,
    CompensationType,
    OvertimeRequest,
    OvertimeRule,
    OvertimeStatus,
)
from apps.overtime.services import (
    OvertimeAuthorizationError,
    OvertimeStateError,
    OvertimeValidationError,
    calculate_total_minutes,
    cancel_overtime_request,
    create_overtime_request,
    decide_overtime_request,
)
from apps.tenancy.context import tenant_context
from tests.leave_helpers import kadro
from tests.testui_helpers import get, giris, post

PAROLA = "Gizli-Parola-123"
SABLON = Path(__file__).resolve().parents[1] / "apps/testui/templates/testui/app.html"


@pytest.fixture
def lot(tenant_a, user_a):  # noqa: ANN001, ANN201
    """Ortak kadro: admin (TENANT), finans yöneticisi (UNIT), personel, kardeş (İK)."""
    with tenant_context(tenant_a.id):
        veri = kadro(tenant_a, user_a)
        OvertimeRule.objects.create(
            tenant=tenant_a,
            code="FM_TEST",
            name="Test kuralı",
            min_minutes=0,
            max_minutes=None,
            multiplier="1.500",
            compensation_type=CompensationType.PAY,
            effective_from=date(2020, 1, 1),
        )
        return veri


def _giris_yap(client, lot, anahtar: str):  # noqa: ANN001, ANN202
    """Kadro anahtarindaki kullaniciyla oturum acar (kisa yol)."""
    kullanici = lot[anahtar]
    yanit = giris(client, kullanici.tenant, kullanici.email, PAROLA)
    assert yanit.status_code == 200
    return yanit


def test_overtime_onay_ve_red_uygulama_ici_onay_kullanir() -> None:
    """Onay/red consequential işlemleri ham tarayıcı diyaloğu olmadan onay ister."""
    govde = SABLON.read_text(encoding="utf-8")
    baslangic = govde.index("async function fmKarar")
    bitis = govde.index("\n  // --- Onaylar", baslangic)
    fonksiyon = govde[baslangic:bitis]
    assert 'await onayModali(' in fonksiyon
    assert 'Fazla mesai bildirimini onayla' in fonksiyon
    assert 'Fazla mesai bildirimini reddet' in fonksiyon
    assert "confirm(" not in fonksiyon


def test_overtime_olusturma_uygulama_ici_onay_kullanir() -> None:
    """Yeni bildirim gönderilmeden önce uygulama içi confirmation gösterilir."""
    govde = SABLON.read_text(encoding="utf-8")
    baslangic = govde.index("async function fmGonder")
    bitis = govde.index("\n  async function fmIptal", baslangic)
    fonksiyon = govde[baslangic:bitis]
    assert 'await onayModali(' in fonksiyon
    assert '"Fazla mesai bildirimi oluştur"' in fonksiyon
    assert '"Bildirimi oluştur"' in fonksiyon
    assert "if (!onay) return;" in fonksiyon
    assert fonksiyon.index("if (!onay) return;") < fonksiyon.index('PERSOPYRO_UI.cagir("/test-ui/api/overtime"')
    assert "confirm(" not in fonksiyon


def _olustur(
    lot,
    *,
    actor=None,
    employee=None,
    baslangic: time = time(18, 0),
    bitis: time = time(20, 0),
    aciklama: str = "Ay sonu kapanış çalışması",
    work_date=None,
) -> OvertimeRequest:
    """Servis üzerinden fazla mesai bildirimi açar (varsayılan: personel, bugün)."""
    actor = actor or lot["personel_user"]
    employee = employee or lot["personel"]
    with tenant_context(employee.tenant_id):
        return create_overtime_request(
            actor=actor,
            employee=employee,
            work_date=work_date or timezone.localdate(),
            start_time=baslangic,
            end_time=bitis,
            description=aciklama,
        )


# --- 11) çalışan kendi kaydını oluşturur --------------------------------------


def test_aktif_calisan_kendi_bildirimini_acar(lot) -> None:  # noqa: ANN001
    talep = _olustur(lot, baslangic=time(18, 0), bitis=time(20, 30))
    assert talep.status == OvertimeStatus.PENDING
    assert talep.total_minutes == 150
    assert talep.employee_id == lot["personel"].pk
    assert talep.created_by_id == lot["personel_user"].pk
    assert talep.compensation_type == ""  # kural motoru yok → karşılık yazılmaz

    # Onay adımı **birincil yöneticiden** kurulur (arayüz tahmin etmez).
    with tenant_context(lot["personel"].tenant_id):
        adimlar = list(ApprovalStep.objects.filter(subject_id=talep.pk).order_by("step_no"))
    assert len(adimlar) == 2
    assert adimlar[0].approver_user_id == lot["yonetici_user"].pk
    assert adimlar[1].approver_role_code == "hr_manager"


# --- 12) başka çalışan adına oluşturulamaz -----------------------------------


def test_baska_calisan_adina_bildirim_acilamaz(lot, csrf_client) -> None:  # noqa: ANN001
    """Yönetici bile olsa **başka çalışan adına** bildirim açılamaz (V1 sınırı)."""
    with tenant_context(lot["personel"].tenant_id), pytest.raises(OvertimeAuthorizationError):
        create_overtime_request(
            actor=lot["yonetici_user"],  # kapsamı var ama kendi kaydı değil
            employee=lot["personel"],
            work_date=timezone.localdate(),
            start_time=time(9, 0),
            end_time=time(11, 0),
            description="Başkası adına bildirim denemesi",
        )

    # HTTP yolu: uç yalnız **kendi** kaydını kullanır (employee_id yok sayılır).
    client = csrf_client
    _giris_yap(client, lot, "personel_user")
    yanit = post(
        client,
        "/test-ui/api/overtime",
        {
            "employee_id": str(lot["kardes"].pk),  # başka çalışan kimliği gönderilse bile
            "overtime_rule_id": str(OvertimeRule.objects.get(code="FM_TEST").pk),
            "work_date": timezone.localdate().isoformat(),
            "start_time": "09:00",
            "end_time": "11:00",
            "description": "HTTP üzerinden başkası adına deneme",
        },
    )
    assert yanit.status_code == 201
    assert yanit.json()["data"]["employee_id"] == str(lot["personel"].pk)


# --- 13) süre sunucuda hesaplanır --------------------------------------------


def test_toplam_sure_sunucuda_hesaplanir_ve_istemci_degeri_dogrulanir(lot) -> None:  # noqa: ANN001
    # 18:00 → 20:30 = 150 dk (istemci ne derse desin yazılan değer budur).
    talep = _olustur(lot, baslangic=time(18, 0), bitis=time(20, 30))
    assert talep.total_minutes == 150

    # İstemci farklı bir süre gönderirse **reddedilir** (sessiz kabul yok).
    with tenant_context(lot["personel"].tenant_id), pytest.raises(OvertimeValidationError):
        create_overtime_request(
            actor=lot["personel_user"],
            employee=lot["personel"],
            work_date=timezone.localdate(),
            start_time=time(8, 0),
            end_time=time(9, 0),
            description="Süre uyuşmazlığı denemesi",
            client_total_minutes=600,
        )

    # İstemci doğru süreyi gönderirse kabul edilir (yalnız doğrulama amaçlı).
    with tenant_context(lot["personel"].tenant_id):
        ikinci = create_overtime_request(
            actor=lot["personel_user"],
            employee=lot["personel"],
            work_date=timezone.localdate(),
            start_time=time(8, 0),
            end_time=time(9, 0),
            description="İstemci doğru süre gönderdi",
            client_total_minutes=60,
        )
    assert ikinci.total_minutes == 60

    # DB guard'ı: sunucu hesabıyla uyuşmayan `total_minutes` **veritabanında** reddedilir.
    with (
        tenant_context(lot["personel"].tenant_id),
        pytest.raises(IntegrityError),
        transaction.atomic(),
    ):
        with connection.cursor() as cur:
            cur.execute(
                "UPDATE hr.overtime_requests SET total_minutes = 99 WHERE id = %s",
                [str(talep.pk)],
            )


# --- 14) geçersiz saat aralığı ve aşırı süre ----------------------------------


def test_gecersiz_saat_ve_asiri_sure_reddedilir(lot) -> None:  # noqa: ANN001
    with pytest.raises(OvertimeValidationError):
        calculate_total_minutes(start_time=time(20, 0), end_time=time(18, 0))
    with pytest.raises(OvertimeValidationError):
        calculate_total_minutes(start_time=time(18, 0), end_time=time(18, 0))

    # Gece yarısını aşan aralık tek satırda ifade edilemez (iki bildirim gerekir).
    with tenant_context(lot["personel"].tenant_id), pytest.raises(OvertimeValidationError):
        _olustur(lot, baslangic=time(22, 0), bitis=time(1, 0))

    # Aşırı süre (16 saat üstü) reddedilir.
    assert OVERTIME_MAX_MINUTES == 960
    with pytest.raises(OvertimeValidationError):
        calculate_total_minutes(start_time=time(0, 0), end_time=time(23, 59))

    # Gelecek tarih reddedilir.
    with tenant_context(lot["personel"].tenant_id), pytest.raises(OvertimeValidationError):
        _olustur(lot, work_date=timezone.localdate() + timedelta(days=1))

    # Açıklama zorunlu ve anlamlı uzunlukta olmalıdır.
    with tenant_context(lot["personel"].tenant_id), pytest.raises(OvertimeValidationError):
        _olustur(lot, aciklama="kısa")

    # Çakışan aralık (aynı gün) reddedilir.
    _olustur(lot, baslangic=time(9, 0), bitis=time(11, 0))
    with tenant_context(lot["personel"].tenant_id), pytest.raises(OvertimeValidationError):
        _olustur(lot, baslangic=time(10, 0), bitis=time(12, 0))


# --- 15-17) görünürlük: kendi / kapsam / kapsam dışı -------------------------


def test_calisan_yalniz_kendi_kayitlarini_gorur(lot, csrf_client) -> None:  # noqa: ANN001
    _olustur(lot)  # personel
    _olustur(lot, actor=lot["kardes_user"], employee=lot["kardes"], aciklama="İK bildirimi")

    client = csrf_client
    _giris_yap(client, lot, "personel_user")
    veri = get(client, "/test-ui/api/overtime").json()
    kimlikler = {s["employee_id"] for s in veri["data"]}
    assert kimlikler == {str(lot["personel"].pk)}
    assert len(veri["meta"]["kendi_kayitlarim"]) == 1


def test_overtime_api_kurallari_yetkili_kullaniciya_doner(lot, csrf_client) -> None:  # noqa: ANN001
    client = csrf_client
    _giris_yap(client, lot, "admin")
    veri = get(client, "/test-ui/api/overtime").json()

    assert veri["meta"]["can_read_rules"] is True
    assert len(veri["meta"]["kurallar"]) == 1
    kural = veri["meta"]["kurallar"][0]
    assert kural["code"] == "FM_TEST"
    assert kural["name"] == "Test kuralı"
    assert kural["min_minutes"] == 0
    assert kural["max_minutes"] is None
    assert kural["multiplier"] == "1.500"
    assert kural["compensation_type"] == CompensationType.PAY
    assert kural["compensation_type_label"] == "Ücret"


def test_overtime_arayuzu_kurallari_gosterir() -> None:
    govde = SABLON.read_text(encoding="utf-8")
    assert "meta.kurallar" in govde
    assert "Fazla mesai kuralları" in govde
    assert "Onay sırasında eşleşen kuralın" in govde


def test_overtime_api_kural_secimini_zorunlu_tutar(lot, csrf_client) -> None:  # noqa: ANN001
    client = csrf_client
    _giris_yap(client, lot, "personel_user")
    kurallar = get(client, "/test-ui/api/overtime").json()["meta"]["kurallar"]
    assert [k["code"] for k in kurallar] == ["FM_TEST"]
    ortak = {
        "work_date": timezone.localdate().isoformat(),
        "start_time": "18:00",
        "end_time": "20:00",
        "description": "Kural seçim testi",
    }

    eksik = post(client, "/test-ui/api/overtime", ortak)
    assert eksik.status_code == 400
    assert "kuralı seçilmelidir" in eksik.json()["error"]["message"]

    kural = OvertimeRule.objects.get(code="FM_TEST")
    secili = post(
        client,
        "/test-ui/api/overtime",
        {**ortak, "overtime_rule_id": str(kural.pk)},
    )
    assert secili.status_code == 201
    assert secili.json()["data"]["overtime_rule_code"] == "FM_TEST"


def test_yonetici_kapsam_icini_gorur_kapsam_disini_gormez(lot, csrf_client) -> None:  # noqa: ANN001
    _olustur(lot)  # personel (Muhasebe → Finans alt ağacı: yöneticinin kapsamında)
    _olustur(lot, actor=lot["kardes_user"], employee=lot["kardes"], aciklama="İK bildirimi")

    client = csrf_client
    _giris_yap(client, lot, "yonetici_user")
    veri = get(client, "/test-ui/api/overtime").json()
    kimlikler = {s["employee_id"] for s in veri["data"]}
    assert str(lot["personel"].pk) in kimlikler
    assert str(lot["kardes"].pk) not in kimlikler  # kapsam dışı görünmez
    assert veri["meta"]["kendi_kayitlarim"] == []
    assert len(veri["meta"]["karar_bekleyen"]) == 1


def test_kapsam_disi_yonetici_karar_veremez(lot, csrf_client) -> None:  # noqa: ANN001
    talep = _olustur(lot)  # personel

    client = csrf_client
    _giris_yap(client, lot, "kardes_user")
    # Kapsam dışı kullanıcı için kayıt **yokmuş gibi** davranır (404; varlık sızdırılmaz).
    yanit = post(client, f"/test-ui/api/overtime/{talep.pk}/decision", {"decision": "approved"})
    assert yanit.status_code == 404


# --- 18) self-approval yasağı ------------------------------------------------


def test_kendi_bildirimini_onaylayamaz(lot) -> None:  # noqa: ANN001
    # Yöneticinin **kendi** bildirimi: birincil yöneticisi yok → adım İK'ya düşer.
    talep = _olustur(
        lot,
        actor=lot["yonetici_user"],
        employee=lot["yonetici"],
        aciklama="Yöneticinin kendi bildirimi",
    )
    with tenant_context(lot["yonetici"].tenant_id), pytest.raises(OvertimeAuthorizationError):
        decide_overtime_request(
            actor=lot["yonetici_user"], talep=talep, decision=OvertimeStatus.APPROVED
        )


# --- 19-20) onay ve ret ------------------------------------------------------


def test_onay_iki_adimda_tamamlanir(lot) -> None:  # noqa: ANN001
    talep = _olustur(lot)

    # 1. adım: birincil yönetici onaylar → talep `pending` kalır (sıra: İK).
    with tenant_context(lot["personel"].tenant_id):
        ara = decide_overtime_request(
            actor=lot["yonetici_user"], talep=talep, decision=OvertimeStatus.APPROVED
        )
        assert ara.status == OvertimeStatus.PENDING

    # 2. adım: kiracı geneli onay yetkisi olan aktör (İK/tenant admin) karara bağlar.
    with tenant_context(lot["personel"].tenant_id):
        sonuc = decide_overtime_request(
            actor=lot["admin"], talep=talep, decision=OvertimeStatus.APPROVED
        )
    assert sonuc.status == OvertimeStatus.APPROVED
    assert sonuc.decided_by_id == lot["admin"].pk
    assert sonuc.decided_at is not None
    assert sonuc.compensation_type_snapshot == CompensationType.PAY
    assert sonuc.multiplier_snapshot == 1.5

    with tenant_context(lot["personel"].tenant_id):
        adimlar = list(ApprovalStep.objects.filter(subject_id=talep.pk).order_by("step_no"))
    assert {a.status for a in adimlar} == {ApprovalStepStatus.APPROVED}


def test_ret_kalan_adimlari_kapatir(lot) -> None:  # noqa: ANN001
    talep = _olustur(lot)
    with tenant_context(lot["personel"].tenant_id):
        sonuc = decide_overtime_request(
            actor=lot["yonetici_user"],
            talep=talep,
            decision=OvertimeStatus.REJECTED,
            reason="Devam çizelgesiyle uyuşmuyor",
        )
    assert sonuc.status == OvertimeStatus.REJECTED
    with tenant_context(lot["personel"].tenant_id):
        adimlar = list(ApprovalStep.objects.filter(subject_id=talep.pk).order_by("step_no"))
    assert adimlar[0].status == ApprovalStepStatus.REJECTED
    assert adimlar[1].status == ApprovalStepStatus.CANCELED


# --- 21) iptal ve durum kuralları --------------------------------------------


def test_iptal_gerekce_ister_ve_karara_baglanan_kayit_iptal_edilemez(lot) -> None:  # noqa: ANN001
    talep = _olustur(lot)
    with tenant_context(lot["personel"].tenant_id):
        with pytest.raises(OvertimeValidationError):
            cancel_overtime_request(actor=lot["personel_user"], talep=talep, reason="kısa")
        iptal = cancel_overtime_request(
            actor=lot["personel_user"], talep=talep, reason="Sehven açıldı"
        )
    assert iptal.status == OvertimeStatus.CANCELED
    assert iptal.canceled_at is not None
    assert iptal.cancel_reason == "Sehven açıldı"

    # İptal edilen kayıt karara bağlanamaz.
    with tenant_context(lot["personel"].tenant_id), pytest.raises(OvertimeStateError):
        decide_overtime_request(
            actor=lot["yonetici_user"], talep=iptal, decision=OvertimeStatus.APPROVED
        )

    # Karara bağlanmış kayıt iptal edilemez (düzeltme ayrı iş kalemidir).
    ikinci = _olustur(lot, aciklama="İkinci bildirim", baslangic=time(9, 0), bitis=time(10, 0))
    with tenant_context(lot["personel"].tenant_id):
        decide_overtime_request(
            actor=lot["yonetici_user"], talep=ikinci, decision=OvertimeStatus.REJECTED
        )
        with pytest.raises(OvertimeStateError):
            cancel_overtime_request(
                actor=lot["personel_user"], talep=ikinci, reason="Geri almak istiyorum"
            )

    # DB guard'ı: `approved` durumundan `pending`'e dönüş yok (kapalı küme).
    with (
        tenant_context(lot["personel"].tenant_id),
        pytest.raises(IntegrityError),
        transaction.atomic(),
    ):
        with connection.cursor() as cur:
            cur.execute(
                "UPDATE hr.overtime_requests SET status = 'pending' WHERE id = %s",
                [str(ikinci.pk)],
            )


# --- 22) kiracı izolasyonu ---------------------------------------------------


def test_kiraci_izolasyonu_korunur(
    tenant_a, tenant_b, user_a, user_b, csrf_client
) -> None:  # noqa: ANN001
    with tenant_context(tenant_a.id):
        lot_a = kadro(tenant_a, user_a)
    with tenant_context(tenant_b.id):
        lot_b = kadro(tenant_b, user_b)

    talep = _olustur(lot_a)

    # B kiracısı: kayıt görünmez (RLS) ve karar verilemez.
    with tenant_context(tenant_b.id):
        assert OvertimeRequest.objects.filter(pk=talep.pk).count() == 0

    with tenant_context(tenant_b.id), pytest.raises(OvertimeRequest.DoesNotExist):
        OvertimeRequest.objects.get(pk=talep.pk)

    # A kiracısı kendi kaydını görür.
    with tenant_context(tenant_a.id):
        assert OvertimeRequest.objects.filter(pk=talep.pk).count() == 1

    client = csrf_client
    assert giris(client, tenant_b, lot_b["admin"].email, PAROLA).status_code == 200
    assert get(client, f"/test-ui/api/overtime/{talep.pk}/decision").status_code in (404, 405)
    yanit = post(client, f"/test-ui/api/overtime/{talep.pk}/decision", {"decision": "approved"})
    assert yanit.status_code == 404


# --- denetim izi -------------------------------------------------------------


def test_denetim_kayitlari_yazilir(lot) -> None:  # noqa: ANN001
    from apps.audit.models import AuditLog  # noqa: PLC0415

    talep = _olustur(lot)
    with tenant_context(lot["personel"].tenant_id):
        decide_overtime_request(
            actor=lot["yonetici_user"], talep=talep, decision=OvertimeStatus.REJECTED
        )
    with tenant_context(lot["personel"].tenant_id):
        eylemler = set(
            AuditLog.objects.filter(record_id=talep.pk).values_list("action", flat=True)
        )
    assert "overtime.request.create" in eylemler
    assert "overtime.request.reject" in eylemler
    # Açıklama metni denetim kaydına **yazılmaz** (yalnız olay bilgisi).
    with tenant_context(lot["personel"].tenant_id):
        satir = AuditLog.objects.filter(
            record_id=talep.pk, action="overtime.request.create"
        ).first()
    assert "Ay sonu" not in str(satir.changes)


# --- HTTP: durum kodları -----------------------------------------------------


def test_http_durum_kodlari_ve_gecersiz_girdi(lot, csrf_client) -> None:  # noqa: ANN001
    client = csrf_client
    # Oturumsuz: uç yok sayılmaz ama kimlik gerekir (403 not_authenticated).
    assert get(client, "/test-ui/api/overtime").status_code == 403

    _giris_yap(client, lot, "personel_user")
    rule_id = str(OvertimeRule.objects.get(code="FM_TEST").pk)

    # Geçersiz saat → 400; süre sunucuda hesaplanır.
    kotu = post(
        client,
        "/test-ui/api/overtime",
        {
            "overtime_rule_id": rule_id,
            "work_date": timezone.localdate().isoformat(),
            "start_time": "20:00",
            "end_time": "18:00",
            "description": "Ters saat aralığı",
        },
    )
    assert kotu.status_code == 400
    assert "message" in kotu.json()["error"]

    # Gerekçesiz iptal → 400; doğru gerekçe → 200.
    veri = post(
        client,
        "/test-ui/api/overtime",
        {
            "overtime_rule_id": rule_id,
            "work_date": timezone.localdate().isoformat(),
            "start_time": "18:00",
            "end_time": "19:30",
            "description": "HTTP iptal denemesi",
        },
    )
    assert veri.status_code == 201
    kayit = veri.json()["data"]
    assert kayit["total_minutes"] == 90
    assert kayit["sure_metin"] == "1 sa 30 dk"
    kisa_gerekce = post(
        client, f"/test-ui/api/overtime/{kayit['id']}/cancel", {"reason": "x"}
    )
    assert kisa_gerekce.status_code == 400
    assert (
        post(
            client,
            f"/test-ui/api/overtime/{kayit['id']}/cancel",
            {"reason": "Sehven açıldı"},
        ).status_code
        == 200
    )

    # Karar yalnız geçerli değerlerle verilir.
    yanlis = post(client, f"/test-ui/api/overtime/{kayit['id']}/decision", {"decision": "pending"})
    assert yanlis.status_code in (400, 409)
