"""
Manuel test arayüzü — sunum etiketleri (yalnızca kolaylık; **yetki kararı değil**).

Bu modül hiçbir backend kodu/durumu değiştirmez: veritabanındaki Türkçe adları
(`hr.roles.name`, `TextChoices.label`) olduğu gibi kullanır, yalnızca kapsam tipi ve
izin istisnası gibi teknik alanlar için okunabilir Türkçe metin üretir.
"""

from __future__ import annotations

#: `hr.roles.scope_type` (OD5 §4.4 kapalı kümesi) → kullanıcı dostu Türkçe metin.
KAPSAM_TIPI_ETIKETLERI = {
    "TENANT": "Tüm kiracı (organizasyon kapsamı yok)",
    "UNIT": "Seçili birim + alt birimleri",
    "UNIT_DIRECT": "Yalnızca seçili birim",
    "SELF": "Yalnızca kendi kaydı",
    "SYS": "Platform (kiracı arayüzünde yönetilemez)",
}

#: `UserPermissionOverride.effect` → Türkçe metin.
ISTISNA_ETIKETLERI = {
    "allow": "Ek izin verildi",
    "deny": "İzin engellendi",
}


def kapsam_tipi_etiketi(scope_type: str | None) -> str:
    """Kapsam tipinin okunabilir adı."""
    return KAPSAM_TIPI_ETIKETLERI.get(str(scope_type or "").upper(), str(scope_type or "—"))


def kapsam_metni(*, scope_type: str | None, birim_adi: str | None) -> str:
    """
    Atama kapsamını tek satırda anlatır.

    Örnek: `UNIT` + "Finans" → "Finans + alt birimleri".
    Kapsam verilmemişse tipin genel anlamı döner (ör. TENANT → "Tüm kiracı").
    """
    tip = str(scope_type or "").upper()
    if birim_adi:
        if tip == "UNIT":
            return f"{birim_adi} + alt birimleri"
        if tip == "UNIT_DIRECT":
            return f"Yalnızca {birim_adi} (alt birimler hariç)"
        return f"{birim_adi} ({kapsam_tipi_etiketi(tip)})"
    return kapsam_tipi_etiketi(tip)


def istisna_etiketi(effect: str | None) -> str:
    """İzin istisnasının okunabilir adı."""
    return ISTISNA_ETIKETLERI.get(str(effect or ""), str(effect or "—"))


#: İzin kataloğu modülleri → kullanıcı dostu Türkçe başlık (yetki düzenleme ekranı).
MODUL_ETIKETLERI = {
    "tenant": "Kiracı",
    "user": "Kullanıcılar",
    "role": "Rol ve yetki yönetimi",
    "org": "Organizasyon (departman/unvan)",
    "employee": "Çalışanlar",
    "leave": "İzin",
    "shift": "Vardiya",
    "attendance": "Devam / puantaj",
    "document": "Evrak",
    "audit": "Denetim kaydı",
    "billing": "Abonelik / faturalama",
    "report": "Raporlar",
    "dataaccess": "Veri erişimi",
    "platform": "Platform (kiracı arayüzünde yönetilemez)",
}


def modul_etiketi(module: str | None) -> str:
    """İzin modülünün okunabilir adı."""
    anahtar = str(module or "").strip() or "diger"
    return MODUL_ETIKETLERI.get(anahtar, anahtar)


#: İzin modüllerinin kısa açıklaması (yetki ekranında başlığın altında gösterilir).
MODUL_ACIKLAMALARI = {
    "tenant": "Kiracı bilgileri ve kiracı ayarları.",
    "user": "Giriş hesaplarını görme ve hesap açma/güncelleme.",
    "role": "Rolleri görme; rol oluşturma, izin kümesi ve atama yönetimi.",
    "org": "Departman ağacı, unvan tanımları ve birim yönetimi.",
    "employee": "Çalışan kaydı ekleme/görme; hassas veri (K2/K3) erişimi.",
    "leave": "İzin türü/politika, talep, onay ve bakiye işlemleri.",
    "shift": "Vardiya tanımları ve planlama (M15 ile gelecek).",
    "attendance": "Devam/puantaj kayıtları (M15 ile gelecek).",
    "document": "Evrak yükleme, görme ve gizli evrak erişimi (M16 ile gelecek).",
    "audit": "Denetim kaydını görüntüleme.",
    "billing": "Abonelik/faturalama işlemleri.",
    "platform": "Platform operasyonları — kiracı arayüzünden yönetilemez (OD3).",
}


def modul_aciklamasi(module: str | None) -> str:
    """İzin modülünün kısa açıklaması."""
    return MODUL_ACIKLAMALARI.get(str(module or "").strip(), "")
