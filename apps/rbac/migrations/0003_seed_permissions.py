# Varsayılan izin kodlarının eklenmesi (veri migration'ı).
#
# NOT: Kod listesi burada DONDURULMUŞTUR; canlı kaynak
# `apps/rbac/permission_catalog.py` dosyasıdır. Yeni izin eklenirse burası
# DEĞİŞTİRİLMEZ, yeni bir migration yazılır (uygulanmış migration değiştirilmez).
#
# `permissions` tablosu global bir tanım tablosudur (RLS yok), bu yüzden kiracı
# bağlamı gerektirmez.

from django.db import migrations

IZINLER: list[tuple[str, str, str]] = [
    ("tenant.read", "Kiracı bilgilerini görme", "tenant"),
    ("tenant.manage", "Kiracı ayarlarını yönetme", "tenant"),
    ("user.read", "Kullanıcıları görme", "user"),
    ("user.manage", "Kullanıcı oluşturma/güncelleme", "user"),
    ("role.read", "Rolleri görme", "role"),
    ("role.manage", "Rol ve izin yönetimi", "role"),
    ("org.department.read", "Departmanları görme", "org"),
    ("org.department.manage", "Departman yönetimi", "org"),
    ("org.position.manage", "Pozisyon yönetimi", "org"),
    ("employee.read", "Çalışanları görme", "employee"),
    ("employee.create", "Çalışan ekleme", "employee"),
    ("employee.update", "Çalışan güncelleme", "employee"),
    ("employee.delete", "Çalışan silme/arşivleme", "employee"),
    ("employee.sensitive.read", "Hassas çalışan verisini görme (maskesiz)", "employee"),
    ("leave.request.read", "İzin taleplerini görme", "leave"),
    ("leave.request.create", "İzin talebi oluşturma", "leave"),
    ("leave.request.approve", "İzin talebi onaylama/reddetme", "leave"),
    ("leave.type.manage", "İzin türü yönetimi", "leave"),
    ("shift.read", "Vardiyaları görme", "shift"),
    ("shift.assign", "Vardiya atama", "shift"),
    ("shift.schedule.manage", "Vardiya planı yönetimi", "shift"),
    ("attendance.read", "Devam kayıtlarını görme", "attendance"),
    ("attendance.manage", "Devam kaydı düzeltme", "attendance"),
    ("document.read", "Belgeleri görme", "document"),
    ("document.upload", "Belge yükleme", "document"),
    ("document.delete", "Belge silme", "document"),
    ("document.confidential.read", "Gizli belge görme", "document"),
    ("audit.read", "Denetim kayıtlarını görme", "audit"),
    ("billing.manage", "Abonelik ve faturalama yönetimi", "billing"),
]


def izinleri_ekle(apps, schema_editor):
    Permission = apps.get_model("rbac", "Permission")
    for kod, ad, modul in IZINLER:
        Permission.objects.update_or_create(
            code=kod,
            defaults={"name": ad, "module": modul},
        )


def izinleri_kaldir(apps, schema_editor):
    Permission = apps.get_model("rbac", "Permission")
    Permission.objects.filter(code__in=[kod for kod, _, _ in IZINLER]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("rbac", "0002_rolepermission_tenant"),
    ]

    operations = [
        migrations.RunPython(izinleri_ekle, izinleri_kaldir),
    ]
