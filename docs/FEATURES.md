# Özellikler — Persopyro

Özellik listesi ve durumları. Bu aşamada **hiçbir özellik kodlanmamıştır**;
durumlar "tasarlandı" veya "planlandı" düzeyindedir.

Durum tanımları:

- **Tasarlandı**: Veri modeli ve kuralları bu doküman setinde tanımlı, kod yok.
- **Planlandı**: Kapsam kararı verildi, tasarım detayı yazılmadı.
- **Kodlanmadı**: Henüz başlanmadı.

---

## 1. Kiracı (tenant) yönetimi

Durum: Tasarlandı (veri modeli mevcut)

- Kiracı kaydı: unvan, vergi bilgisi, adres, iletişim, durum (`active`, `trial`,
  `suspended`, `canceled`)
- Kullanım limitleri: `max_users`, `max_storage_mb`
- Kiracı ayarları: `tenant_settings.config` (JSONB)
- Kiracı askıya alma / kapatma akışı
- Abonelik planı ile ilişki

Kabul ölçütü: bir kiracının kullanıcısı, başka bir kiracının hiçbir kaydını
göremez — API, rapor, dosya ve arama yolları dahil.

---

## 2. Kimlik, kullanıcı ve yetki (RBAC)

Durum: **Kısmen kodlandı** (faz 2, 1. dilim) — model, migration, RLS ve izin
çözümleme servisi hazır; istek hattı, görünümler/API, MFA uçları ve parola
sıfırlama akışı yazılacak.

- Kullanıcı kaydı ve e-posta doğrulama
- Giriş, çıkış, token yenileme
- Parola sıfırlama (tek kullanımlık, süreli token)
- Başarısız giriş sayacı ve hesap kilitleme
- Çok faktörlü doğrulama alanı ve akışı
- Rol yönetimi: sistem/genel/özel roller
- İzin yönetimi ve kullanıcı bazlı istisnalar (override)
- Rol ve yetki değişikliklerinin audit kaydı

Detay: `AUTHORIZATION.md`

---

## 3. Organizasyon yönetimi

Durum: Tasarlandı (veri modeli mevcut)

- Şirket kaydı (çok şirketli kiracı desteği)
- Departman ağacı (hiyerarşi, döngü koruması)
- Pozisyon/kadro yönetimi
- Çalışan kaydı ve profili
- Görev/pozisyon/ücret geçmişi
- Acil durum kişileri
- Organizasyon şeması görünümü (kişisel veri içermez)

Detay: `ORGANIZATION.md`

---

## 4. İzin (leave) yönetimi

Durum: Tasarlandı (veri modeli mevcut)

- İzin türleri (`leave_types`): yıllık, mazeret, doğum, hastalık vb.
- İzin bakiyeleri (`leave_balances`) ve tahakkuk
- İzin talebi oluşturma (`leave_requests`)
- Onay akışı ve onay kayıtları (`leave_approvals`)
- Onayda bakiye yansıtma (`hr.sync_leave_balance_on_approval()`)
- Takvim görünümü, ekip çakışma kontrolü
- İptal ve geri alma akışı
- Sağlık/mazeret gerekçesi gibi alanların maskelenmesi

Kabul ölçütü: bakiye, onaylanan taleplerle tutarlıdır; aynı tarih aralığı için
çift bakiye düşümü olmaz.

---

## 5. Vardiya ve devam takibi

Durum: Tasarlandı (veri modeli mevcut)

- Çalışma düzeni ve vardiya şablonları (`work_schedules`)
- Vardiya tipleri (sabit, gece, dönüşümlü, esnek)
- Vardiya planı oluşturma ve çalışana atama
- Giriş/çıkış kayıtları (`attendance_events`)
- Günlük devam kaydı (`attendance_records`)
- Zaman çizelgesi (`timesheets`, `timesheet_entries`)
- Gecikme, devamsızlık, fazla mesai hesaplaması
- Planlanan vardiya ile fiili çalışmanın karşılaştırılması

---

## 6. Evrak ve belge yönetimi

Durum: Tasarlandı (veri modeli mevcut)

- Belge kategorileri (`document_categories`)
- Belge yükleme, sürümleme (`documents`, `document_versions`)
- Bütünlük doğrulama (`checksum_sha256`)
- Gizli belge işaretleme (`is_confidential`) ve şifreleme işareti
- Erişim kaydı (`document_access`)
- Zorunlu belge takibi (ör. sözleşme, sağlık raporu) ve eksik belge uyarısı
- Kısa ömürlü imzalı indirme bağlantısı
- Saklama süresi ve arşivleme

---

## 7. İşe alım (recruitment)

Durum: Tasarlandı (veri modeli mevcut)

- İş ilanları (`job_postings`)
- Aday kaydı (`candidates`)
- Başvuru ve aşama takibi (`job_applications`, `application_stages`)
- Mülakat planlama (`interviews`, `interview_panelists`)
- Teklif yönetimi (`offers`)
- Adayın çalışana dönüşmesi

---

## 8. Performans değerlendirme

Durum: Tasarlandı (veri modeli mevcut)

- Değerlendirme döngüleri (`review_cycles`)
- Form şablonları (`review_form_templates`)
- Değerlendirmeler, hedefler ve puanlama (`performance_reviews`,
  `review_goals`, `review_scores`)

---

## 9. Bordro (maaş) kayıtları

Durum: Planlandı / kısmen tasarlandı

- Ücret bileşenleri (`salary_components`)
- Çalışan ücret bilgisi (`employee_compensations`)
- Bordro dönemleri ve satırları (`payroll_runs`, `payroll_lines`,
  `payroll_component_lines`)

Kısıt: resmi mevzuat hesaplamaları ve muhasebe entegrasyonu kapsam dışıdır
(bkz. `PROJECT.md` kapsam dışı). Bu modül, kayıt ve veri tutma düzeyinde ele
alınır.

---

## 10. Abonelik ve faturalama

Durum: Tasarlandı (veri modeli mevcut)

- Planlar (`plans`)
- Abonelikler (`subscriptions`) ve koltuk sayısı (`subscription_seats`)
- Faturalar (`invoices`) ve ödemeler (`payments`)
- Kullanım limitleriyle (kullanıcı, depolama) ilişki

---

## 11. Denetim (audit) ve raporlama

Durum: Tasarlandı (veri modeli mevcut)

- Otomatik audit kaydı (`audit_logs`, `hr.audit_trigger_fn()`)
- Giriş denemeleri kaydı (`login_audit`)
- Denetim kaydı görüntüleme (`audit.read` izni)
- Raporların kişisel veri içermeyecek şekilde üretilmesi

---

## 12. Öncelik sırası (önerilen)

1. Kiracı + kimlik/RBAC (ürünün güvenlik temeli)
2. Organizasyon + çalışan kaydı
3. İzin yönetimi
4. Vardiya ve devam
5. Evrak
6. Denetim/raporlama
7. İşe alım
8. Performans
9. Abonelik/faturalama
10. Bordro kayıtları

Her modül bitmeden sonraki modüle geçilmez; sıra değişikliği gerekçesiyle
`CHANGELOG.md`'ye yazılır.
