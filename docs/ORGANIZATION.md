# Organizasyon Yapısı — Persopyro

Şirket içi hiyerarşi ve çalışan yapısının modellenmesi. Mevcut veritabanı
tasarımındaki `03_org_structure.sql` tabloları esas alınır.

## 1. Nesne hiyerarşisi

```
tenant (kiracı = müşteri şirket)
└── company (tüzel kişilik / şirket kaydı)
    └── department (departman, hiyerarşik: parent_id)
        └── position (pozisyon / kadro)
            └── employee (çalışan)
                ├── employment_history (görev/ücret/pozisyon geçmişi)
                └── emergency_contacts (acil durum kişileri)
```

Ek yapılar:

- `work_schedules` — çalışma düzeni / vardiya şablonları
- `projects` — çalışanların görevlendirildiği projeler

## 2. Tablolar ve sorumlulukları

| Tablo | Sorumluluk |
|---|---|
| `companies` | Kiracı içindeki şirket kaydı; tüzel unvan, vergi bilgisi, adres |
| `departments` | Departman ağacı; `parent_id` ile hiyerarşi |
| `positions` | Pozisyon/kadro tanımı; unvan ve seviye |
| `employees` | Çalışan ana kaydı; kişisel bilgi, işe giriş, durum |
| `employment_history` | Pozisyon, departman, ücret değişimlerinin tarihçesi |
| `emergency_contacts` | Çalışanın acil durumda aranacak kişileri |
| `work_schedules` | Haftalık çalışma düzeni ve vardiya şablonu |
| `projects` | Proje kayıtları ve görevlendirmeler |

`employees` tablosundaki `work_shift_type` enum'u vardiya tipini tanımlar.

## 3. Kurallar

1. Bir departman, kendi alt departmanı olamaz (döngü yasak). Departman
   taşınırken döngü kontrolü zorunludur.
2. Departman silinmeden önce içindeki çalışanların ve pozisyonların durumu
   kontrol edilir; boş olmayan departman arşivlenir, silinmez.
3. Tüm organizasyon kayıtları `tenant_id` taşır; kiracılar arası hiyerarşi
   kurulamaz.
4. Çalışan kaydı bir kiracıya ve bir şirkete aittir; kiracı değiştirilemez.
5. Departman/pozisyon/ücret değişiklikleri geçmişe yazılır
   (`employment_history`); geçmiş kaydı silinmez, düzeltme yeni kayıtla yapılır.
6. Her departmanın en fazla bir sorumlusu olur; sorumlu ataması yetki gerektirir
   ve audit log'a yazılır.
7. Aynı çalışan numarası (employee number) bir kiracı içinde tekildir.
8. Çalışan kaydı silinmez, durumu `inactive`/`archived` yapılır (soft delete).

## 4. Çalışanın yaşam döngüsü

| Durum | Anlamı | Yapılabilecekler |
|---|---|---|
| Aday (`candidates`) | İşe alım sürecinde | Mülakat, teklif |
| İşe giriş (`employee` oluşur) | Aktif çalışan | İzin, vardiya, evrak, değerlendirme |
| İzinli/ayrılış sürecinde | Askıda | Kısıtlı erişim |
| Ayrıldı (`inactive`) | İlişki bitti | Kayıt arşivde, veri saklama politikası işler |
| Arşiv (`archived`) | Uzun süreli saklama | Raporlama için okunabilir |

Aday → çalışan geçişi, aday kaydını silmez; yeni çalışan kaydı adayla
ilişkilendirilebilir. Bu geçiş audit log'a yazılır.

## 5. Vardiya ve çalışma düzeni

- Çalışma düzeni (`work_schedules`) haftanın günleri, günlük süre ve vardiya
  tipini tanımlar.
- Vardiya tipleri: sabit gündüz, gece, dönüşümlü, esnek (tasarımda
  `work_shift_type` enum'u ile modellenir).
- Devam kayıtları (`attendance_records`, `attendance_events`) fiili çalışmayı
  tutar; vardiya planı ile karşılaştırılarak gecikme/fazla mesai hesaplanır.
- Vardiya değişikliği, etkilenen çalışanlara bildirilir ve audit log'a yazılır.
- Fazla mesai ve izin, vardiya planıyla çakışma açısından kontrol edilir.

## 6. Organizasyon görünürlüğü ve yetki

- `employee` rolü yalnızca kendi kaydını ve kendi departmanının genel
  bilgilerini görür.
- `manager` rolü kendi departmanı (ve alt departmanları) kapsamındaki çalışanları
  görür.
- `hr_manager` ve `tenant_admin` kiracının tamamını görür.
- Organizasyon şeması görüntülemesi (ağaç) kişisel veri içermez; yalnızca ad,
  unvan, departman gösterilir.

## 7. Çok şirketli kiracı durumu

- Bir kiracı birden fazla tüzel şirket barındırabilir (`companies`).
- Departman ve çalışan kayıtları şirketle ilişkilendirilir.
- Şirketler arası veri görünürlüğü kiracı yöneticisi tarafından ayrıca
  kısıtlanabilir; bu kısıt uygulama katmanında uygulanır ve varsayılan olarak
  kapalıdır (yani tüm şirketler tenant_admin'e görünür, diğer roller yalnızca
  kendi şirketini görür).
