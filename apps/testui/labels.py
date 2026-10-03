"""
Manuel test arayüzü — sunum etiketleri (yalnızca kolaylık; **yetki kararı değil**).

Bu modül hiçbir backend kodu/durumu değiştirmez: veritabanındaki Türkçe adları
(`hr.roles.name`, `TextChoices.label`) olduğu gibi kullanır, yalnızca kapsam tipi ve
izin istisnası gibi teknik alanlar için okunabilir Türkçe metin üretir.
"""

from __future__ import annotations

#: `hr.roles.scope_type` (OD5 §4.4 kapalı kümesi) → kullanıcı dostu Türkçe metin.
KAPSAM_TIPI_ETIKETLERI = {
    "TENANT": "Şirket genelinde",
    "UNIT": "Atandığı departman ve alt departmanları",
    "UNIT_DIRECT": "Yalnızca atandığı departman",
    "SELF": "Yalnızca kendi hesabı ve çalışan kaydı",
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
    Kapsam verilmemişse tipin genel anlamı döner (ör. TENANT → "Şirket genelinde").
    """
    tip = str(scope_type or "").upper()
    if birim_adi:
        if tip == "UNIT":
            return f"{birim_adi} departmanı + alt departmanları"
        if tip == "UNIT_DIRECT":
            return f"Yalnızca {birim_adi} departmanı"
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
    "ui": "Arayüz başlıkları",
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
    "shift": "Vardiya şablonları ve gün/hafta bazlı planlama.",
    "attendance": "Devam/puantaj kayıtları (M15 ile gelecek).",
    "document": "Evrak yükleme, görme ve gizli evrak erişimi (M16 ile gelecek).",
    "audit": "Denetim kaydını görüntüleme.",
    "billing": "Abonelik/faturalama işlemleri.",
    "ui": "Ana sekmelerin ve alt sekmelerin ekranda görünüp görünmeyeceğini belirler. Bu izinler işlem yapma yetkisi vermez; işlem için ilgili modülün işlem izni de gerekir.",
    "platform": "Platform operasyonları — kiracı arayüzünden yönetilemez (OD3).",
}

# Yetki düzenleme ekranında teknik izin adının altında gösterilen açıklamalar.
# Backend kararını değiştirmez; yalnızca yöneticinin hangi kutuyu neden
# işaretlediğini daha kolay anlamasını sağlar.
IZIN_ACIKLAMALARI = {
    "ui.panel.view": "Ana özet panelini ve paneldeki bilgi kartlarını gösterir.",
    "ui.departments.view": "Departmanlar sekmesini gösterir.",
    "ui.employees.view": "Çalışanlar ana sekmesini gösterir.",
    "ui.employees.list": "Çalışan Ara / Liste alt sekmesini gösterir.",
    "ui.employees.new": "Yeni çalışan ekleme alt sekmesini gösterir.",
    "ui.employees.form": "Kişisel bilgi formu alanlarını yönetme alt sekmesini gösterir.",
    "ui.employees.exit": "İşten çıkış / çıkış süreci ve yeniden işe alım alt sekmesini gösterir.",
    "ui.employees.suspended": "Askıya alınan çalışanlar alt sekmesini gösterir.",
    "ui.leave.view": "İzin Talebi ana sekmesini gösterir.",
    "ui.leave.request": "İzin talebi oluşturma ve taleplerim bölümünü gösterir.",
    "ui.leave.policy": "İzin politikası; gün/saat karşılığı ve negatif bakiye ayarlarını gösterir.",
    "ui.leave.types": "İzin türleri tanımlarını ve aktif/pasif yönetimini gösterir.",
    "ui.leave.entitlements": "Çalışan detayındaki izin hakları ve bakiyeler bölümünü gösterir.",
    "ui.overtime.view": "Fazla Mesai ana sekmesini gösterir.",
    "ui.overtime.requests": "Fazla mesai bildirimi ve talepler bölümünü gösterir.",
    "ui.overtime.rules": "Fazla mesai kuralları bölümünü gösterir.",
    "ui.approvals.view": "Onaylar ana sekmesini gösterir.",
    "ui.roles.view": "Yetki Yönetimi ana sekmesini gösterir.",
    "tenant.read": "Şirket bilgilerinin görüntülenmesine izin verir.",
    "tenant.manage": "Şirket ayarlarının değiştirilmesine izin verir.",
    "user.read": "Kullanıcı hesaplarının görüntülenmesine izin verir.",
    "user.manage": "Kullanıcı hesabı oluşturma ve güncelleme işlemlerine izin verir.",
    "role.read": "Rollerin ve atanmış izinlerinin görüntülenmesine izin verir.",
    "role.manage": "Rol oluşturma, rol izinlerini değiştirme ve rol düzenleme işlemlerine izin verir.",
    "org.department.read": "Departman ağacını görüntülemeye izin verir.",
    "org.department.manage": "Departman ekleme ve düzenlemeye izin verir.",
    "org.position.manage": "Unvan/pozisyon tanımlarını yönetmeye izin verir.",
    "org.workplace.read": "İşyeri kayıtlarını görüntülemeye izin verir.",
    "org.workplace.manage": "İşyeri oluşturma ve düzenlemeye izin verir.",
    "employee.read": "Çalışan kayıtlarını ve çalışan listesini görüntülemeye izin verir.",
    "employee.create": "Yeni çalışan kaydı oluşturmaya izin verir.",
    "employee.update": "Çalışan bilgilerinin güncellenmesine izin verir.",
    "employee.photo.upload": "Çalışan profil fotoğrafı ekleme, değiştirme ve kaldırma işlemlerine izin verir.",
    "employee.delete": "Çalışanı arşivlemeye izin verir; fiziksel silme yapmaz.",
    "employee.sensitive.read": "Yetkili kişisel/hassas çalışan verilerini maskesiz görmeye izin verir.",
    "employee.sensitive.write": "Çalışanın kişisel ve hassas verilerini değiştirmeye izin verir.",
    "employee.suspend": "Çalışanı askıya alma veya askıdan çıkarma işlemlerine izin verir.",
    "employee.terminate": "Çalışan için işten çıkış süreci başlatmaya izin verir.",
    "employee.restore": "Ayrılmış/askıdaki çalışanı gerekçeli olarak geri almaya izin verir.",
    "employee.history.read": "Çalışanın istihdam ve işlem geçmişini görmeye izin verir.",
    "employee.form.manage": "Yeni çalışan kişisel bilgi formu alanlarını ekleme, düzenleme ve pasifleştirmeye izin verir.",
    "leave.request.read": "İzin taleplerini görüntülemeye izin verir.",
    "leave.request.create": "İzin talebi oluşturmaya ve düzenleyip yeniden onaya göndermeye izin verir.",
    "leave.request.approve": "İzin taleplerini onaylama veya reddetmeye izin verir.",
    "leave.request.approve.override": "Standart onay adımlarını kayıtlı gerekçeyle atlamaya izin verir.",
    "leave.request.cancel": "İzin talebini iptal etmeye izin verir.",
    "leave.balance.read": "Çalışanların izin bakiyelerini görmeye izin verir.",
    "leave.balance.manage": "İzin bakiyesi düzeltme ve hak tanımlama işlemlerine izin verir.",
    "leave.type.manage": "İzin türleri ve izin politikalarının yönetilmesine izin verir.",
    "overtime.request.create": "Fazla mesai bildirimi oluşturmaya izin verir.",
    "overtime.request.read": "Fazla mesai bildirim ve taleplerini görmeye izin verir.",
    "overtime.request.approve": "Fazla mesai taleplerini onaylama veya reddetmeye izin verir.",
    "overtime.rule.read": "Fazla mesai kurallarını görüntülemeye izin verir.",
    "overtime.rule.manage": "Fazla mesai kurallarını oluşturma ve düzenlemeye izin verir.",
    "shift.read": "Vardiya kayıtlarını görüntülemeye izin verir.",
    "shift.assign": "Çalışanlara vardiya atamaya izin verir.",
    "shift.schedule.manage": "Vardiya planını oluşturmaya ve değiştirmeye izin verir.",
    "attendance.read": "Devam/puantaj kayıtlarını görüntülemeye izin verir.",
    "attendance.manage": "Devam/puantaj kayıtlarını düzeltmeye izin verir.",
    "document.read": "Belgeleri görüntülemeye izin verir.",
    "document.upload": "Çalışan belgesi yüklemeye izin verir.",
    "document.delete": "Belgeleri silmeye izin verir.",
    "document.confidential.read": "Gizli belgeleri görüntülemeye izin verir.",
    "audit.read": "Denetim ve işlem kayıtlarını görüntülemeye izin verir.",
    "billing.manage": "Abonelik ve faturalama ayarlarını yönetmeye izin verir.",
}


def izin_aciklamasi(code: str | None) -> str:
    """Yetki kodunun yöneticiye gösterilecek kısa açıklaması."""
    return IZIN_ACIKLAMALARI.get(str(code or "").strip(), "Bu yetki ilgili modülde tanımlı işlemi yapmayı sağlar.")


def modul_aciklamasi(module: str | None) -> str:
    """İzin modülünün kısa açıklaması."""
    return MODUL_ACIKLAMALARI.get(str(module or "").strip(), "")
