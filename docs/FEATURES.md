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

Durum: **Büyük ölçüde kodlandı** (faz 2, 1. ve 2. dilim) — model, migration, RLS,
istek hattı, oturum/token girişi, MFA (TOTP), izin kataloğu ve varsayılan roller
hazır. Kalan: parola sıfırlama, MFA kayıt ucu, kullanıcı CRUD uçları.

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

Durum: **Uygulandı (M14 ✅ 2026-09-11)** — tür/politika/hakediş/bakiye/talep/onay
zinciri servis + veritabanı seviyesinde çalışıyor; arayüz (frontend/API uçları)
bu turda **kapsam dışıdır**.

- İzin türleri (`leave_types`): kiracı bazlı; sistem kodları rezerve
  (`annual_leave`, `annual_leave_block`, `work_from_home`, `flex_time`,
  `sick_leave`, `administrative_leave`, `paternity_leave`); belge/onay/bakiye
  bayrakları kiracı tarafından yönetilir.
- İzin politikası (`leave_policies` + `leave_policy_rules`): `fixed` (yılda sabit gün)
  ve `tenure_based` (kıdeme göre **[min, max)** aralıkları); tür başına **tek**
  yaşayan politika; çakışan aralıklar reddedilir.
- İzin bakiyeleri ve tahakkuk (`leave_balances`): `entitled/carried_forward/
  adjustment/used/reserved` + **üretilen** `available_days`; hakediş idempotent
  (`manage.py accrue_leave_entitlements`), manuel düzeltme **gerekçeli** ve denetimli;
  negatif bakiye yalnız politika açıkça izin veriyorsa.
- İzin talebi (`leave_requests`): sunucu tarafı gün hesabı (takvim günü; hafta
  sonu/tatil hesabı **takvim modülü (M15) gelene kadar yok**), yarım gün, ön bildirim
  ve azami süre kuralları, kapalı durum kümesi (`draft/pending/approved/rejected/
  canceled/taken`).
- Onay akışı (`approval_steps`, genel tablo): sıra → birincil yönetici, ardından
  yetkili rol (`hr_manager` + kapsam); **adım sırası atlanamaz**; onaylayan kendi
  talebini onaylayamaz (`SEPARATE_ACTOR`); yetkili override kayıtlı gerekçe ister.
- Bakiye yansıtma: onaya sunumda **rezervasyon**, nihai onayda **tek** kullanım
  kaydı; red/iptal rezervasyonu çözer; onaylı iptalde iade politika izin veriyorsa
  açık `refund` kaydı + denetimle yapılır (`hr.sync_leave_balance_on_approval()`
  işlevinin servis karşılığı: `apps/leave/services/requests.py`).
- Belge zorunlu türler (ör. `sick_leave`): belge referansı olmadan **onaylanamaz**
  (fail-closed); gerçek `hr.documents` FK'si M16 ile bağlanacak.
- İptal/geri alma: iptal gerekçesi zorunlu; taslak/onay bekleyen/onaylı için ayrı
  bakiye davranışı; sessiz bakiye değişimi **yok** (her hareket olay defterinde).
- Kapsam: çalışan kendi talebi (`SELF`), `dept_manager` **UNIT alt ağacı**,
  `tenant_officer`/`tenant_admin` kiracı geneli — karar `izin ∧ kapsam`.

Kabul ölçütü: bakiye, onaylanan taleplerle tutarlıdır; aynı tarih aralığı için
çift bakiye düşümü olmaz. **Ölçüldü:** onayda tek kullanım kaydı; ikinci onay
durum hatası; aynı talep için ikinci `usage` kaydı veritabanı kısıtıyla reddedilir.

Kalan (kapsam dışı): takvim görünümü ve ekip çakışma kontrolü, çalışma takvimi /
resmî tatil hesabı (M15), izin gerekçesi maskeleme, işten çıkışta bakiye kapatma,
devir (carryover) işi ve API uç noktaları.

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

## 12. Geliştirme/test arayüzü (`/test-ui/`) — manuel test paneli

Durum: Uygulandı (yalnızca geliştirme/test; üretim özelliği değildir)

Amaç: organizasyon + izin akışını **gerçek kullanıcı gibi** tarayıcıdan uçtan uca
denemek. Üretim kullanıcı arayüzü değildir; yeni frontend zinciri (React/Vite/
Tailwind) **kurulmaz** — Django şablonu + vanilla JS/CSS kullanılır.

- Giriş: kiracı (`slug`) + e-posta + parola ile **mevcut** oturum ucu üzerinden
  (`/api/v1/auth/login`); yeni/paralel kimlik doğrulama yok, çıkış aynı şekilde
  hızlı (impersonation **yok**).
- Panel: kullanıcı, kiracı, roller, organizasyon kapsamı, çalışan kaydı, dönem
  izin bakiyesi, kendi talepleri, onay kuyruğu — UUID yerine okunabilir adlar.
- Departmanlar: ağaç görünümü, oluşturma, üst birim değiştirme, soft delete
  (`org.department.manage` ∧ `OD5` kapsamı).
- Çalışanlar: kapsamla sınırlı liste/oluşturma; birim + unvan seçimi, yönetici
  görünürlüğü, dönem bakiyesi. Liste **geniş tablo** olarak çalışır: çalışan (ad +
  e-posta), çalışan no, unvan, birim, yönetici, rol, kalan izin, durum, işlem
  kolonları + ad/no/birim/unvan/e-posta **filtresi**. Çalışan no **kullanıcıdan
  istenmez**: sunucuda üretilir (`EMP-000001`, `hr.employee_no_seq`) ve birim
  değişince değişmez. Detay **sağ çekmecede** açılır (sayfa yerleşimini bozmaz);
  aynı çalışanın `Detay`ına tekrar basmak çekmeceyi **kapatır**, aynı anda tek
  detay açık kalır. **K2/K3 kişisel veri gösterilmez.**
- **Yerleşim standardı:** `base.html` içinde ortak sınıflar (`page-container`,
  `grid-2/3`, `split`, `form-grid`, `data-table`, `detail-panel`, `action-bar`,
  `option-card`, `badge`) — masaüstünde içerik ~1400 px'e kadar genişler, dar
  ekranda bloklar alt alta düşer, kart içi geniş tablolar kendi içinde kayar
  (sayfada yatay kaydırma oluşmaz). Yeni bağımlılık eklenmez.
- İzin: talep oluşturma (gün sayısı sunucuda hesaplanır), kendi talepleri,
  "Bekleyen İzinler" ekranı, Onayla/Reddet (red için gerekçe), iptal.
- **Onay beklenen kişi:** onay kuyruğunda satırın **sıradaki onaylayıcısı** adı + unvanıyla
  gösterilir (`Mehmet Demir — Finans Müdürü`); çok adımlıysa sıralı liste
  (`1. … — Bekliyor`, `2. … — Sonraki adım`). Kaynak `approval_steps`; arayüz tahmin etmez.
- **Yaklaşan izinler durumu:** bugün izinde → yeşil «İzinde», 0–7 gün → sarı «N gün sonra»,
  daha ileri → nötr «N gün sonra» (renk **ve** metin birlikte).
- **Çalışan oluşturma (giriş hesabı zorunlu):** ad, soyad, kurumsal e-posta, parola, birim ve
  işe giriş zorunludur; çalışan + kullanıcı **tek atomik işlemde** yazılır (biri başarısızsa
  hiçbiri). Parola yanıtta/log'da görünmez. Çalışanlar ekranı *Çalışan Ara / Liste* ve
  *Yeni Çalışan* alt bölümlerine ayrılmıştır.
- **İzin Hakları / İzin Bakiyeleri ekranı:** çalışan detayında izin türü · yıllık hak ·
  kullanılan · bekleyen · kalan · **sonraki hakediş** (işe giriş yıl dönümü) görünür; ayrı
  ana sekmede kapsam süzgeçli bakiye tablosu (filtre: çalışan/birim/izin türü/düşük-tükendi)
  ve **servis katmanı üzerinden** düzeltme (hak ekle / hak düş / düzeltme / politikadan
  hakediş) bulunur — gerekçe zorunlu, defter + denetim kaydı yazılır.
- **Panel operasyonel özeti:** toplam çalışan, bugün izinde, 7 gün içinde izne çıkacak,
  bekleyen onay kartları; altta bekleyen talepler, yaklaşan izinler ve bakiyesi kritik
  (kalan ≤ 3 gün) çalışanlar tablosu.
- **Planlanan (kod yazılmadı):** fazla mesai (overtime) modülü ve tenant tarafından
  yapılandırılabilen onboarding formu `docs/HR_ROADMAP.md` içinde tasarlanmıştır.
- Kapsam testi görünür: Finans müdürü Finans + Muhasebe'yi görür, İnsan Kaynakları
  çalışanını/talebini **görmez ve onaylayamaz**.
- **Yıllık izin görünürlüğü:** çalışan kendi panelinde, yetkili kullanıcı çalışan
  listesinde ve detayda hak/kullanılan/bekleyen/kalan değerlerini görür; değerler
  backend bakiye kaydından gelir (arayüz hesap yapmaz). Hakediş yoksa hata yerine
  "Henüz yıllık izin hakedişi oluşturulmamış." gösterilir.
- **Unvan / rol / yetki ayrımı:** çalışan kaydında iş unvanı (`position`) ve birim
  (`department`) ayrı gösterilir; kullanıcının sistem rolü Türkçe adıyla listelenir
  (ör. `dept_manager` → "Departman Yöneticisi", kapsamı "Finans + alt birimleri").
  `role.manage` sahibi, çalışan detayındaki "Rol ve Yetkiler" bölümünden rol atar
  (UNIT rollerde birim kapsamı seçer), atamayı kaldırır (soft delete, OD21) ve
  izin istisnalarını (override) salt okunur görür.
- **Bilgilerim (her rol):** panelde ad soyad, kayıtlı e-posta, şirket, unvan, birim,
  çalışan no, yıllık izin (hak/kullanılan/bekleyen/kalan) ve roller. Çalışan kaydı
  yoksa alanlar boş, bakiye yerine açıklama gösterilir.
- **Hesap açma:** "hesap bağlama" listesi yok; yönetici (`user.manage`) e-posta ve
  parolayı verir, **"Parola üret"** düğmesi 15 karakterlik parola üretir (tarayıcıda;
  sunucu parolayı geri döndürmez), zayıf parola `400`. Denetim kaydı yalnız e-postayı yazar.
- **Kişisel veriler (K2):** çalışan kaydında ad, soyad, doğum tarihi, kişisel e-posta,
  adres, yakın telefonu + yakınlık derecesi, vatandaşlık (TR değilse oturma/çalışma izni
  türü **zorunlu**). Bölüm yalnızca `employee.sensitive.read` + kapsam sahibine görünür;
  yazma da aynı kapıdan geçer, izinsiz istek `403` (kapsam dışı `404`). Denetim yalnız
  alan adlarını yazar — değerler PII olarak kaydedilmez.
- **İzin türü ekleme:** hazır şablonlar (yıllık, hastalık/rapor, ücretsiz, doğum) formu
  doldurur; dört özellik açıklamalı (ücretli, bakiye kullanır, onay, belge).
- **Yetki Yönetimi:** sekme yalnızca `role.manage` sahibinde görünür; izinler modül
  başlığı + modül açıklaması + Türkçe izin adıyla listelenir. **Rol oluşturma** (ad, kod,
  kapsam tipi) ve **rol düzenleme** (ad; etkin ataması yoksa kapsam) desteklenir —
  SYS/platform kapsamı, varsayılan/var olan kod, kendi rolünü düzenleme `400/403`.
- **Görünürlük:** yetkisi olmayan kullanıcıda departman/çalışan/unvan/izin türü/politika
  formları **hiç görünmez** ("yetkiniz yok" notu da gösterilmez); karar backend'de verilir.
- **Çalışan listesi:** normal çalışan yalnız **kendi kaydını** görür; birim yöneticisi
  kapsamındaki (birim + alt ağaç) çalışanları, kiracı yöneticisi tüm kiracıyı görür.
  Kapsam dışı çalışan detayı **404**. Organizasyon şeması (birim ağacı) ayrı görünümdür.
- **İzin durumu noktası:** bugün izinde olan çalışanın numarasının yanında **yeşil**,
  izni **7 günden az** kaldıysa **sarı** nokta (onaylı taleplerden hesaplanır).
- **Onay ekranı:** her satırda talep sahibinin güncel bakiyesi (hak/kullanılan/bekleyen/
  kalan) görünür. Talep sahibi kendi talebini **onaylayamaz** (backend `SEPARATE_ACTOR`
  ile `403`); satırda **Düzenle** ve **İptal** vardır. Düzenleme, talebi forma taşır ve
  kaydedince eski talebi iptal edip yenisini onaya sunar.
- **İzin politikası kartı:** `leave.type.manage` sahibi "bakiye yetersizse talep
  açılabilir" ve "onaydan sonra iade" anahtarlarını yönetir; kapalıyken aşırı talep
  `409` ile reddedilir, açıkken talep açılır ve kararı onaylayan verir.
- **Yeni çalışan:** *Bağlı olduğu yönetici* seçilir (onay akışının ilk adımı bu ilişkiye
  göre kurulur); kişisel veri bölümü **bütün alanları** sorar: ad, soyad, **kimlik no**,
  doğum tarihi, **kan grubu**, kişisel e-posta/telefon, adres, yakın kişi + yakınlık,
  vatandaşlık, oturma/çalışma izni ve **Banka + IBAN**. Zorunlu olan yalnız ad/soyaddır;
  kayıt tek istekte hem çalışanı hem kişisel veriyi oluşturur.
- **Kan grubu (K3):** kapalı kümeden seçilir (A+/A-/B+/B-/AB+/AB-/0+/0-, boş =
  bilinmiyor), veritabanında **Fernet ile şifreli** saklanır; kanıt: adım **6c-8**
  ham kolonun düz metin olmadığını ölçer. Sınıflandırma kararı **OD31**'de açıktır.
- **Tarih biçimi:** tüm gösterimler **gg.aa.yyyy**.
- **Self-elevation yasakları arayüzden bağımsız korunur:** kullanıcı kendi rolünü
  atayamaz/yükseltemez, sahip olmadığı yetkiyi dağıtamaz, `role.manage` yoksa
  rol yönetimi kapanır; platform rolleri kiracı arayüzünden yönetilemez.
- **Panel karar görünümü:** panelde onay bekleyen / onaylanan / reddedilen / iptal
  sayıları ve son karara bağlanan izinler listelenir (kapsam süzgeciyle: çalışan
  kendi kayıtlarını, yönetici alt ağacındakileri görür).
- **Görünürlük sınırları (test arayüzünde de geçerli):** rol kataloğu/yetki ekranı
  yalnızca `role.read` + düzenleme yalnızca `role.manage` (şirket yöneticisi);
  `employee` kapsamlı kullanıcı çalışan listesinde ve detayda **yalnız kendi kaydını**
  görür (başka çalışanın kaydı/numarası dönmez, kapsam dışı detay `404`) ve
  **organizasyon şemasını** görür; başkalarının izin/bakiye detaylarını görmez.
  Yaklaşan izinler ve onay kuyruğu yalnız kapsam/yetki dahilinde listelenir.
- **Departman düzenleme:** yetkili kullanıcı birimin adını ve kodunu değiştirebilir
  (denetimli `org.unit.update`); taşıma ayrı işlemdir, kapsam/ağaç bozulmaz.
- **Yetki Yönetimi sekmesi (rol izin düzenleme):** şirket yöneticisi (`role.manage`)
  bir rolü seçip izin kümesini modül gruplarına ayrılmış Türkçe etiketli kutularla
  düzenler ("Tümünü seç" / modül bazında seçim). Kurallar: yalnız `role.manage`,
  kendi rolü düzenlenemez (self-elevation), sahip olunmayan izin dağıtılamaz,
  platform izinleri (OD3) listelenmez ve verilemez, rol en az bir izin içerir;
  her değişiklik denetim kaydına yazılır ve **anında** geçerli olur.
- **Yaklaşan izinler (panel):** kapsamınızdaki çalışanların onaylı ve gelecekteki
  izinleri panelde listelenir (çalışan, birim, tür, başlangıç, bitiş, gün).
- **Unvan ve izin türü tanımlama:** yetkili kullanıcı (`org.position.manage`)
  çalışan formundan yeni unvan ekleyebilir; şirket yöneticisi (`leave.type.manage`)
  kiracıya özel izin türü tanımlayabilir (ücretli/bakiye/belge/onay bayraklarıyla)
  ve mevcut türü pasifleştirebilir (silinmez; mevcut talepler korunur, sistem kodları
  rezervedir). Çalışana hesap **listeden seçilerek** bağlanır (serbest metin yok).
- **Birim taşıma görsel seçimle:** taşıma hedefi ağaçtan seçilir; hedef listesi
  (kendisi ve alt ağacı hariç) sunucudan gelir, taşıma öncesi onay sorulur ve
  sonrasında ağaç yenilenir. Döngü/derinlik/kiracı garantileri veritabanında
  aynen yürürlüktedir.
- Hata gösterimi: `400/401/403/409/500` anlaşılır Türkçe mesajlarla; yetki
  reddinde "Bu işlem için yetkiniz veya organizasyon kapsamınız yok." Güvenlik
  ayrıntısı/yığın izi gösterilmez.
- Güvenlik: RLS, superuser/`is_staff` bypass, authorization disable veya sabit
  kiracı bağlamı **yok**; tüm kararlar backend servis katmanında verilir ve arayüz
  yalnızca buton görünürlüğünü ayarlar. Üretimde varsayılan olarak **kapalıdır**
  (tüm `/test-ui/*` yolları 404).
- Kalıcılık: demo kullanıcılar/parolalar yalnız demo/bootstrap betiğinde üretilir;
  migration'lara parola konmaz, depoya gerçek secret yazılmaz.

## 13. Öncelik sırası (önerilen)

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

> **Güncel sıra (2026-09-12):** yukarıdaki liste ilk ürün sıralamasıdır. **Uçtan uca HR ürün
> mimarisi ve gerçek kod bağımlılıklarına göre düzeltilmiş faz sırası (P0–P9)** ile birlikte
> modül tasarımları, izin/audit/job planı ve kabul kriterleri **`docs/HR_ROADMAP.md`** içindedir
> (bkz. §22 fazlandırma, §20 HR görev matrisi). Bu doküman uygulanmış özellikleri anlatır;
> plan içeriği burada tekrar edilmez.
>
> **Ölçüm notu (2026-09-12):** §5 (vardiya/devam), §6 (evrak), §9 (bordro) başlıkları
> **tasarlanmış ama kodlanmamıştır** (izin kodları ve DB tasarımı var, uygulama yok);
> §7 (işe alım) ve §8 (performans) **hiç başlanmamıştır** ve V1 kapsamı dışındadır.
>
> **P1 durumu (2026-09-13):** blocker kararlar **KAPALI**; **P1-A (veri modeli + izin altyapısı)
> ✅ UYGULANDI** — yeni tablolar (`workplaces`, `employee_employment_terms`,
> `employee_assignments`, `form_field_definitions`), çalışan yaşam döngüsü kolonları,
> `users.auth_version`, 8 yeni izin kodu ve sistem rol senkronu. **Servisler/uçlar/arayüz
> (P1-B/P1-C) henüz yok**; kullanıcıya görünen davranış bu turda değişmedi. Kapsam: çalışan oluşturma/güncelleme,
> departman-pozisyon-yönetici değişikliği **geçmişli**, istihdam tipi/çalışma modeli/işyeri/sözleşme/
> deneme süresi, durum makinesi (askıya alma/çıkış/arşiv), form alan politikası (A/B/C kilit seviyesi),
> çalışan↔kullanıcı ilişkisi (hesap zorunlu, atomik). **P1 kapsamı dışı:** ücret (P6), belge (P3),
> bildirim gönderimi (P2), offboarding checklist ve self-service (P9), custom alan (P9).
> Ayrıntı: `docs/HR_ROADMAP.md` §28; kararlar: `docs/AUTHORIZATION.md` §16.
