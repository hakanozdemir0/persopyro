"""
P5a V1 — panel yerleşimi + izin hakkı (entitlement) akışı testleri.

Kapsam (kullanıcı bildirimi §1–§3 + §13/1–10):

  * Panelde üç ana kart **aynı satırda eşit yükseklik** mantığıyla durur:
    `Bilgilerim` · `Yıllık izin bakiyem` · `İzin taleplerim`.
  * `Bilgilerim` alanları: ad soyad, çalışan no, unvan, departman, yönetici, işe giriş.
  * Ayrı "İzin Bakiyeleri" **sayfası kaldırıldı**; hak yönetimi çalışan detayına taşındı.
  * **Bildirilen hata:** yeni oluşturulmuş çalışana izin hakkı eklenemiyordu (arayüz
    yalnız var olan bakiye satırlarını listelediği için düğme hiç görünmüyordu).
    Artık satır kümesi bakiye kullanan aktif izin türleri üzerinden kurulur ve
    `set_leave_entitlement` servisi ile **gerekçeli** yazılır.
  * Yıllık (yıl dönümü) hakediş ile manuel başlangıç hakkı **çakışmaz**; aynı dönem
    için ikinci bir `accrual` kaydı oluşmaz (DB kısmi benzersizliği + servis kararı).

Testler gerçek HTTP yolunu kullanır (CSRF açık); arayüz hiçbir kararı kendi vermez.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from django.db import IntegrityError, connection, transaction

from apps.leave.models import BalanceEntryKind, LeaveBalance, LeaveBalanceEntry
from apps.leave.services import (
    accrue_leave_entitlements,
    next_accrual_date,
    set_leave_entitlement,
)
from apps.tenancy.context import tenant_context
from tests.leave_helpers import calisan, kadro
from tests.testui_helpers import get, giris, post

PAROLA = "Gizli-Parola-123"
KOK = Path(__file__).resolve().parents[1]
APP_HTML = KOK / "apps" / "testui" / "templates" / "testui" / "app.html"
BASE_HTML = KOK / "apps" / "testui" / "templates" / "testui" / "base.html"


@pytest.fixture
def lot(tenant_a, user_a):  # noqa: ANN001, ANN201
    with tenant_context(tenant_a.id):
        return kadro(tenant_a, user_a)


def _giris_yap(client, lot, anahtar: str = "admin"):  # noqa: ANN001, ANN202
    kullanici = lot[anahtar]
    yanit = giris(client, kullanici.tenant, kullanici.email, PAROLA)
    assert yanit.status_code == 200
    return yanit


def _yeni_calisan(lot):  # noqa: ANN001, ANN202
    """Kadro dışı, **bugün işe başlayan** çalışan (bildirilen senaryo)."""
    with tenant_context(lot["birimler"]["muhasebe"].tenant_id):
        return calisan(
            lot["birimler"]["muhasebe"].tenant,
            lot["birimler"]["muhasebe"],
            no="YENI-1",
            hire_date=date(2026, 9, 13),
        )


# --- 1) panel üç ana kart -----------------------------------------------------


def test_panel_uc_ana_kart_ayni_satirda_tanımlı() -> None:
    """(1) Üç ana kart aynı satırda (`.grid-esit`) ve doğru sırada tanımlıdır."""
    kaynak = APP_HTML.read_text(encoding="utf-8")
    assert '<div class="grid-esit">' in kaynak
    icerik = kaynak.split('<div class="grid-esit">', 1)[1].split("${fazlaMesaiSatiri}", 1)[0]
    # Kart sırası: Bilgilerim → Yıllık izin bakiyem → İzin taleplerim
    assert icerik.index("${bilgilerimKarti}") < icerik.index("${bakiyemKarti}")
    assert icerik.index("${bakiyemKarti}") < icerik.index("${taleplerimKarti}")
    for baslik in ("Bilgilerim", "Yıllık izin bakiyem", "İzin taleplerim"):
        assert baslik in kaynak, baslik
    # Ayrı "Oturum" kartı üç ana kartın **dışındadır** (panel kalabalıklaşmaz).
    assert "oturumKarti" in kaynak
    assert "${oturumKarti}" not in icerik


def test_panel_kartlari_esit_yukseklik_mantiginda() -> None:
    """(2) Eşit yükseklik: `stretch` + kart gövdesinin esnemesi (desktop düzeni)."""
    css = BASE_HTML.read_text(encoding="utf-8")
    assert ".grid-esit {" in css
    assert "align-items: stretch" in css
    assert ".grid-esit > .card { display: flex; flex-direction: column;" in css
    assert ".grid-esit > .card > .kart-govde { flex: 1 1 auto; }" in css
    # Üç kolon; dar ekranlarda tek kolona düşer (taşma yok).
    assert "repeat(3, minmax(0, 1fr))" in css
    assert ".grid-2, .grid-3, .grid-4, .grid-esit { grid-template-columns: minmax(0, 1fr); }" in css

    # Üç kartın her biri gövde sarmalayıcısı taşır → içerik az/çok olsa da dengeli.
    kaynak = APP_HTML.read_text(encoding="utf-8")
    assert kaynak.count('<div class="kart-govde">') >= 3
    # Alt notlar kartın dibine yaslanır (sınıf diğer sınıflarla birlikte yazılır:
    # `class="muted small kart-dip"` / `class="kart-dip action-bar"`).
    assert kaynak.count("kart-dip") >= 3
    assert ".kart-dip { margin-top: auto;" in css


def test_bilgilerim_karti_istenen_alanlari_gosterir(client, lot, csrf_client) -> None:  # noqa: ANN001
    """(1) Bilgilerim kartı alanları + işe giriş tarihi sunucudan gelir."""
    # Çalışan senaryosu: **personel** hesabıyla girilir (yönetici hesabında çalışan
    # kaydı yoktur; o durumda alanlar boş döner ve bu ayrı bir durumdur).
    _giris_yap(csrf_client, lot, "personel_user")
    veri = get(csrf_client, "/test-ui/api/dashboard").json()["data"]
    assert set(veri["profile"]) >= {
        "name",
        "employee_no",
        "position",
        "department",
        "hire_date",
    }
    assert veri["profile"]["hire_date"] == lot["personel"].hire_date.isoformat()
    # Yönetici bilgisi çalışan özetinden gelir (arayüz tahmin etmez).
    assert veri["employee"]["manager"]["employee_no"] == lot["yonetici"].employee_no

    kaynak = APP_HTML.read_text(encoding="utf-8")
    for alan in ("Ad Soyad", "Çalışan no", "Unvan", "Birim", "Yönetici", "İşe giriş"):
        assert f"<h3>{alan}</h3>" in kaynak, alan


# --- 3) bakiye: backend tek doğruluk kaynağı ---------------------------------


def test_yillik_bakiye_backend_kaynagindan_gelir(client, lot, csrf_client) -> None:  # noqa: ANN001
    """(3) Paneldeki hak/kullanılan/bekleyen/kalan `leave_balances` ile birebir aynı."""
    _giris_yap(csrf_client, lot, "personel_user")
    veri = get(csrf_client, "/test-ui/api/dashboard").json()["data"]
    yil = veri["period_year"]
    with tenant_context(lot["personel"].tenant_id):
        bakiye = LeaveBalance.objects.get(
            employee=lot["personel"], period_year=yil, leave_type__code="annual_leave"
        )
    assert Decimal(veri["annual"]["entitled_days"]) == bakiye.entitled_days
    assert Decimal(veri["annual"]["used_days"]) == bakiye.used_days
    assert Decimal(veri["annual"]["reserved_days"]) == bakiye.reserved_days
    # Türetilmiş değer DB'nin ürettiği kolondan gelir (arayüz hesap yapmaz).
    assert Decimal(veri["annual"]["available_days"]) == bakiye.available_days
    # Panel kartları da aynı değerleri taşır (tek kaynak).
    kendi = next(b for b in veri["balances"] if b["leave_type_code"] == "annual_leave")
    assert Decimal(kendi["available_days"]) == bakiye.available_days
    # "İzin taleplerim" kartı: son kayıtlar + toplam sayı sunucudan.
    assert veri["my_requests_count"] >= len(veri["my_requests"])
    assert {"bekleyen", "karar_bekleyen"} <= set(veri["fazla_mesai"])


# --- 4) ayrı "İzin Bakiyeleri" sayfası kaldırıldı ----------------------------


def test_ayri_izin_bakiyeleri_navigasyonu_yok(client, lot, csrf_client) -> None:  # noqa: ANN001
    """(4) Sekme ve yükleyici kaldırıldı; veri uçları (geri uyum) duruyor."""
    _giris_yap(csrf_client, lot, "personel_user")
    sayfa = csrf_client.get("/test-ui/").content.decode()
    assert 'data-tab="balances"' not in sayfa
    assert 'id="tab-balances"' not in sayfa
    assert 'data-tab="overtime"' in sayfa  # yerine gelen sekme
    kaynak = APP_HTML.read_text(encoding="utf-8")
    assert "balances: yukleBakiyeler" not in kaynak
    assert "overtime: yukleFazlaMesai" in kaynak
    # Backend servisleri KALDIRILMADI (uçlar hâlâ çalışır; regresyon).
    assert get(csrf_client, "/test-ui/api/balances").status_code == 200


# --- 5) çalışan detayında izin hakları --------------------------------------


def test_calisan_detayi_hak_satirlarini_listeler(client, lot, csrf_client) -> None:  # noqa: ANN001
    """(5) Hak satırları **bakiye kaydı olmayan** türleri de içerir."""
    _giris_yap(csrf_client, lot)
    yeni = _yeni_calisan(lot)
    veri = get(csrf_client, f"/test-ui/api/employees/{yeni.pk}").json()["data"]
    assert veri["bakiye_okunabilir"] is True
    assert veri["bakiye_yonetilebilir"] is True
    haklar = veri["izin_haklari"]
    kodlar = {h["leave_type_code"] for h in haklar}
    assert "annual_leave" in kodlar
    yillik = next(h for h in haklar if h["leave_type_code"] == "annual_leave")
    assert yillik["hakedis_var"] is False
    assert yillik["hak_toplam"] is None
    assert yillik["mesaj"]  # açık durum mesajı (hata değil)
    # Birden çok bakiye kullanan tür listelenir (yeni çalışanda hepsi "tanımsız").
    assert all(h["hakedis_tanimli"] is False for h in haklar)


# --- 6) BİLDİRİLEN HATA: yeni çalışana hak tanımlama ------------------------


def test_yeni_calisana_izin_hakki_tanimlanabilir(client, lot, csrf_client) -> None:  # noqa: ANN001
    """(6) Bildirilen hata: yeni çalışana hak tanımlanamıyordu → artık tanımlanıyor."""
    _giris_yap(csrf_client, lot)
    yeni = _yeni_calisan(lot)
    yil = date(2026, 1, 1).year
    with tenant_context(lot["personel"].tenant_id):
        tur = lot["turler"]["annual_leave"]
    yanit = post(
        csrf_client,
        f"/test-ui/api/employees/{yeni.pk}/entitlements",
        {
            "leave_type_id": str(tur.pk),
            "days": "14",
            "reason": "Yeni çalışan başlangıç hakkı",
        },
    )
    assert yanit.status_code == 200, yanit.content
    govde = yanit.json()
    assert govde["data"]["durum"] == "hakedis_tanimlandi"
    # Sayı karşılaştırması Decimal üzerinden (gösterim "14" veya "14.0" olabilir;
    # normalizasyon farkı bir hata değildir).
    assert Decimal(govde["data"]["hak_toplam"]) == Decimal("14.0")
    satir = next(
        h for h in govde["meta"]["izin_haklari"] if h["leave_type_code"] == "annual_leave"
    )
    assert Decimal(satir["hak_toplam"]) == Decimal("14.0")
    assert Decimal(satir["kalan"]) == Decimal("14.0")
    assert satir["hakedis_tanimli"] is True
    # Sonraki hakediş = işe giriş yıl dönümü (sunucu hesabı).
    assert satir["sonraki_hakedis_metin"] == "13.09.2027"

    # Defter kaydı yazıldı ve bakiye tek kaynak olarak güncellendi.
    with tenant_context(lot["personel"].tenant_id):
        bakiye = LeaveBalance.objects.get(
            employee=yeni, leave_type=tur, period_year=yil
        )
        assert bakiye.entitled_days == Decimal("14.0")
        assert bakiye.available_days == Decimal("14.0")
        assert LeaveBalanceEntry.objects.filter(
            balance=bakiye, entry_kind=BalanceEntryKind.ACCRUAL
        ).count() == 1


def test_ayni_deger_tekrar_yazilmaz(client, lot, csrf_client) -> None:  # noqa: ANN001
    """(6) Aynı değer ikinci kez gönderilirse hiçbir şey yazılmaz (yanıltıcı onay yok)."""
    _giris_yap(csrf_client, lot)
    yeni = _yeni_calisan(lot)
    with tenant_context(lot["personel"].tenant_id):
        tur = lot["turler"]["annual_leave"]
    govde = {
        "leave_type_id": str(tur.pk),
        "days": "14",
        "reason": "Başlangıç hakkı",
    }
    yol = f"/test-ui/api/employees/{yeni.pk}/entitlements"
    assert post(csrf_client, yol, govde).status_code == 200
    ikinci = post(csrf_client, yol, govde)
    assert ikinci.status_code == 200
    assert ikinci.json()["data"]["durum"] == "degisiklik_yok"
    with tenant_context(lot["personel"].tenant_id):
        assert (
            LeaveBalanceEntry.objects.filter(
                employee=yeni, entry_kind=BalanceEntryKind.ACCRUAL
            ).count()
            == 1
        )


# --- 7-9) yetki ve gerekçe kuralları ----------------------------------------


def test_hak_islemi_gerekce_ister(client, lot, csrf_client) -> None:  # noqa: ANN001
    """(7) Gerekçesiz/kısa gerekçeli işlem reddedilir (400)."""
    _giris_yap(csrf_client, lot)
    yeni = _yeni_calisan(lot)
    with tenant_context(lot["personel"].tenant_id):
        tur = lot["turler"]["annual_leave"]
    yol = f"/test-ui/api/employees/{yeni.pk}/entitlements"
    eksik = post(csrf_client, yol, {"leave_type_id": str(tur.pk), "days": "14", "reason": ""})
    assert eksik.status_code == 400
    kisa = post(csrf_client, yol, {"leave_type_id": str(tur.pk), "days": "14", "reason": "kısa"})
    assert kisa.status_code == 400
    # Yetkisiz kullanıcı (izin modülü yönetimi yok) → 403.
    _giris_yap(csrf_client, lot, "kardes_user")
    yasak = post(
        csrf_client,
        yol,
        {"leave_type_id": str(tur.pk), "days": "14", "reason": "Yetkisiz deneme"},
    )
    assert yasak.status_code in (403, 404)


def test_yetkisiz_kullanici_hak_duzenleyemez(client, lot, csrf_client) -> None:  # noqa: ANN001
    """(8) `leave.balance.manage` olmayan rol → 403 (kendi kaydı olsa bile)."""
    yeni = _yeni_calisan(lot)
    # Personel rolü: bakiye okuma var, yönetme yok.
    _giris_yap(csrf_client, lot, "personel_user")
    with tenant_context(lot["personel"].tenant_id):
        tur = lot["turler"]["annual_leave"]
    yanit = post(
        csrf_client,
        f"/test-ui/api/employees/{lot['personel'].pk}/entitlements",
        {"leave_type_id": str(tur.pk), "days": "30", "reason": "Kendi hakkımı artırıyorum"},
    )
    assert yanit.status_code == 403
    # Servis katmanı da reddeder (arayüz güvenlik kararı vermez).
    from apps.leave.services import LeaveAuthorizationError  # noqa: PLC0415

    with tenant_context(lot["personel"].tenant_id), pytest.raises(LeaveAuthorizationError):
        set_leave_entitlement(
            actor=lot["personel_user"],
            employee=lot["personel"],
            leave_type=tur,
            period_year=2026,
            days=Decimal("30"),
            reason="Yetkisiz servis denemesi",
        )
    assert yeni is not None


def test_kapsam_disi_calisanin_hakki_degistirilemez(client, lot, csrf_client) -> None:  # noqa: ANN001
    """(9) Yönetici kapsamı dışındaki çalışanın hakkını değiştiremez (404)."""
    _giris_yap(csrf_client, lot, "yonetici_user")  # Finans + alt ağaç
    with tenant_context(lot["personel"].tenant_id):
        tur = lot["turler"]["annual_leave"]
        # Kapsam dışı çalışanın mevcut hakkı (kadro kurulumundan) kayda alınır.
        onceki = LeaveBalance.objects.get(
            employee=lot["kardes"], leave_type=tur, period_year=2026
        )
        onceki_hak = onceki.entitled_days
        onceki_duzeltme = onceki.adjustment_days
    yanit = post(
        csrf_client,
        f"/test-ui/api/employees/{lot['kardes'].pk}/entitlements",  # İK birimi: kapsam dışı
        {"leave_type_id": str(tur.pk), "days": "30", "reason": "Kapsam dışı deneme"},
    )
    assert yanit.status_code == 404
    # Hiçbir değer değişmedi (ne hak ne düzeltme) → yazma gerçekleşmedi.
    with tenant_context(lot["personel"].tenant_id):
        sonra = LeaveBalance.objects.get(
            employee=lot["kardes"], leave_type=tur, period_year=2026
        )
    assert sonra.entitled_days == onceki_hak
    assert sonra.adjustment_days == onceki_duzeltme


# --- 10) yıllık hakediş ile çakışma yok -------------------------------------


def test_manuel_hak_ile_yillik_hakedis_cakismaz(client, lot, csrf_client) -> None:  # noqa: ANN001
    """(10) Manuel başlangıç hakkı + yıl dönümü hakedişi **çift kayıt üretmez**."""
    _giris_yap(csrf_client, lot)
    yeni = _yeni_calisan(lot)  # işe giriş 13.09.2026
    with tenant_context(lot["personel"].tenant_id):
        tur = lot["turler"]["annual_leave"]
        yonetim = lot["admin"]
    # 1) Manuel başlangıç hakkı: 14 gün (servis çağrısı kiracı bağlamında yapılır;
    # izin çözümlemesi bağlam olmadan fail-closed reddeder).
    with tenant_context(lot["personel"].tenant_id):
        set_leave_entitlement(
            actor=yonetim,
            employee=yeni,
            leave_type=tur,
            period_year=2026,
            days=Decimal("14.0"),
            reason="Başlangıç hakkı",
        )
    # 2) Aynı dönem için otomatik hakediş: politika değeri 14 → fark yok, yazım yok.
    with tenant_context(lot["personel"].tenant_id):
        sonuclar = accrue_leave_entitlements(
            actor=yonetim,
            tenant=lot["personel"].tenant,
            period_year=2026,
            employees=[yeni],
        )
    yillik_sonuc = next(s for s in sonuclar if s["leave_type"] == "annual_leave")
    assert yillik_sonuc["durum"] == "degisiklik_yok"
    with tenant_context(lot["personel"].tenant_id):
        bakiye = LeaveBalance.objects.get(employee=yeni, leave_type=tur, period_year=2026)
        assert bakiye.entitled_days == Decimal("14.0")
        assert bakiye.available_days == Decimal("14.0")
        # Dönem başına TEK hakediş kaydı (çift entitlement yok).
        assert (
            LeaveBalanceEntry.objects.filter(
                balance=bakiye, entry_kind=BalanceEntryKind.ACCRUAL
            ).count()
            == 1
        )

    # 3) Sonraki dönem (2027) kendi kaydını alır; ilk yıl dönümü 13.09.2027'dir.
    assert next_accrual_date(date(2026, 9, 13)) == date(2027, 9, 13)
    with tenant_context(lot["personel"].tenant_id):
        accrue_leave_entitlements(
            actor=yonetim,
            tenant=lot["personel"].tenant,
            period_year=2027,
            employees=[yeni],
        )
    with tenant_context(lot["personel"].tenant_id):
        yeni_bakiye = LeaveBalance.objects.get(employee=yeni, leave_type=tur, period_year=2027)
        assert yeni_bakiye.entitled_days == Decimal("14.0")
        assert (
            LeaveBalanceEntry.objects.filter(
                balance=yeni_bakiye, entry_kind=BalanceEntryKind.ACCRUAL
            ).count()
            == 1
        )
    # 4) DB kısmi benzersizliği: aynı dönem için ikinci `accrual` kaydı reddedilir.
    with (
        tenant_context(lot["personel"].tenant_id),
        pytest.raises(IntegrityError),
        transaction.atomic(),
    ):
        with connection.cursor() as cur:
            cur.execute(
                "INSERT INTO hr.leave_balance_entries "
                "(id, created_at, tenant_id, balance_id, employee_id, leave_type_id, "
                " period_year, entry_kind, delta_days, reason) "
                "VALUES (gen_random_uuid(), now(), %s, %s, %s, %s, 2026, 'accrual', 1.0, '')",
                [
                    str(lot["personel"].tenant_id),
                    str(bakiye.pk),
                    str(yeni.pk),
                    str(tur.pk),
                ],
            )


# --- regresyon ---------------------------------------------------------------


def test_regresyon_izin_akisi_ve_detay_ekrani_calismaya_devam_eder(  # noqa: ANN001
    client, lot, csrf_client
) -> None:
    """(23-27) İzin talebi açma/listeleme, detay ekranı, /admin/ ve oturum davranışı."""
    _giris_yap(csrf_client, lot, "personel_user")
    # İzin talebi akışı bozulmadı.
    with tenant_context(lot["personel"].tenant_id):
        tur = lot["turler"]["annual_leave"]
    olustur = post(
        csrf_client,
        "/test-ui/api/leave-requests",
        {
            "employee_id": str(lot["personel"].pk),
            "leave_type_id": str(tur.pk),
            "start_date": "2026-10-05",
            "end_date": "2026-10-06",
            "reason": "Regresyon kontrolü",
            "submit": True,
        },
    )
    assert olustur.status_code == 201, olustur.content
    assert get(csrf_client, "/test-ui/api/leave-requests?scope=pending").status_code == 200
    # Çalışan detayı (izin hakları + roller bölümleri) açılır.
    detay = get(csrf_client, f"/test-ui/api/employees/{lot['personel'].pk}")
    assert detay.status_code == 200
    assert {"izin_haklari", "roles", "annual"} <= set(detay.json()["data"])
    # Django Admin yolu yok (OD19).
    assert csrf_client.get("/admin/").status_code == 404
    # Oturum: çıkış sunucuda kapanır.
    ciks = csrf_client.post(
        "/api/v1/auth/logout", HTTP_X_CSRFTOKEN=_csrf_token(csrf_client)
    )
    assert ciks.status_code == 204
    assert get(csrf_client, "/test-ui/api/dashboard").status_code == 403


def _csrf_token(client) -> str:  # noqa: ANN001
    """Geçerli oturum çerezinden CSRF başlığı üretir."""
    return client.cookies["csrftoken"].value


def test_entitlement_servisi_politikasi_olmayan_tur_icin_de_calisir(lot) -> None:  # noqa: ANN001
    """Politikası olmayan tür için de hak tanımlanabilir (aksi halde akış kilitlenirdi)."""
    yeni = _yeni_calisan(lot)
    with tenant_context(lot["personel"].tenant_id):
        # `annual_leave_block` bakiye kullanır ve bu kiracıda politikası yoktur
        # (`kadro` yalnız `annual_leave` için politika kurar).
        tur = lot["turler"]["annual_leave_block"]
        assert not tur.policies.filter(deleted_at__isnull=True).exists()
        onceki = LeaveBalance.objects.filter(employee=yeni, leave_type=tur).count()
    assert onceki == 0
    with tenant_context(lot["personel"].tenant_id):
        sonuc = set_leave_entitlement(
            actor=lot["admin"],
            employee=yeni,
            leave_type=tur,
            period_year=2026,
            days=Decimal("5.0"),
            reason="Özel hak tanımı",
        )
    assert sonuc["durum"] == "hakedis_tanimlandi"
    assert Decimal(sonuc["hak_toplam"]) == Decimal("5.0")
