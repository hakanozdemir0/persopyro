# İK Yol Haritası (HR Roadmap) — Persopyro

Tasarım ve plan dokümanı. İki büyük gelecek özelliği kapsar:

1. **Fazla mesai (overtime) modülü**
2. **Tenant tarafından yapılandırılabilen dinamik çalışan kayıt/onboarding formu**

- Tarih: 2026-09-12
- Durum: **designed-not-coded** (bu doküman; kod bu turda yazılmadı)
- İlgili dokümanlar: `PROJECT.md`, `ARCHITECTURE.md`, `SECURITY.md`,
  `AUTHORIZATION.md`, `DATABASE.md`, `ORGANIZATION.md`, `FEATURES.md`,
  `DEVELOPMENT_RULES.md`
- Referans şema (salt okunur): `~/hrsaas_db/04_attendance_timesheet.sql`,
  `~/hrsaas_db/06_payroll.sql` (`hr.attendance_records.overtime_minutes`,
  `hr.shift_*`/`hr.attendance_records`, `hr.salary_components`/
  `hr.employee_compensations`, `hr.payroll_lines.overtime_hours`)

---

## 1. Amaç ve kapsam

- Bu doküman **kod içermez**; yalnız **şema planı, izin (permission) planı, iş akışı
  planı, bağımlılıklar ve açık kararlar** üretir.
- Her şema önerisi mevcut proje standardına uyar: tablolar `hr.` şemasında,
  kolonlar `snake_case` İngilizce, kiracı tablolarında `tenant_id` **NOT NULL** +
  FK, **RLS + FORCE ROW LEVEL SECURITY**, kiracı-içi yabancı anahtarlar
  `hr.ensure_same_tenant_ref()` guard'ı, yüksek etkili olaylarda `apps/audit/services.py::log_action`.
- Kişisel veri sınıfları korunur: **K0** (kişisel değil) · **K1** (kurumsal kimlik) ·
  **K2** (kişisel veri) · **K3** (özel nitelikli; **Fernet şifreli**,
  `apps/common/fields.py::EncryptedCharField`/`EncryptedTextField`).
- Her yeni karar noktası için **önerilen OD** numarası verilir. Mevcut en yüksek
  numara `docs/AUTHORIZATION.md`'de **OD31**'dir → bu dokumandaki öneriler
  **OD32'den** başlar ve **öneri**'dir (verilmiş karar **değildir**).
- Kod yazılmadan önce her ilgili OD karara bağlanmalıdır (proje kuralı:
  `DEVELOPMENT_RULES.md` madde 9 — önce doküman, sonra kod).

**Durum: designed-not-coded.**

---

## 2. Fazla mesai (overtime) modülü

### 2.1 Hedef davranış

- Çalışan (veya kapsamındaki yetkili) belirli bir **iş günü + saat aralığı** için
  fazla mesai **talebi** açar; süre **sunucu tarafında** hesaplanır (leave modülündeki
  "istemci değeri sunucu hesabıyla birebir uyuşmalı" ilkesiyle aynı yaklaşım).
- Fazla mesai **haftalık sınıra** tabidir (varsayılan 45 saat/hafta; kiracı
  tarafından yapılandırılabilir). Sınır, haftalık **onaylanmış** toplam üzerinden
  doğrulanır.
- Talep **onay akışından** geçer; onay adımları **mevcut genel `hr.approval_steps`**
  tablosunu kullanır (`subject_kind = 'overtime_request'`) — yeni bir onay motoru
  yazılmaz (leave modülüyle aynı desen).
- Onaylanan fazla mesainin karşılığı **iki türlüdür**: **PAY** (ücret) veya
  **TIME_OFF** (karşılık izin). Karşılık türü kural (rule) düzeyinde
  varsayılan, talep düzeyinde gerekçeli olarak değiştirilebilir.
- **Kaynak tekliği:** bir çalışan–gün için fazla mesai süresi tek satırda
  (`hr.overtime_requests`) tutulur; `hr.attendance_records.overtime_minutes`
  (M15) yalnız **girdi/öneri** kaynağıdır, ikinci bir source-of-truth değildir
  (bkz. önerilen **OD40**).
- **Kapsam notu:** resmî mevzuat hesaplamaları (gelir vergisi, SGK vb.) `PROJECT.md`
  gereği **kapsam dışıdır**; bu modül kayıt/veri tutma + basit katsayı düzeyinde
  ele alınır. Ücret **tutarı** payroll modülü gelene kadar hesaplanmaz (bkz. bölüm 4).

### 2.2 Veri modeli (öneri)

Tablolar `hr.` şemasında, her birinde `tenant_id` + RLS + FORCE.

**`hr.overtime_rules`** (tenant düzeyinde yapılandırma şablonu; `leave_policies` deseniyle hizalı)

| Kolon | Tip / kural | Sınıf |
|---|---|---|
| `id` | UUID PK | K0 |
| `tenant_id` | UUID NOT NULL FK `hr.tenants(id)` | K1 |
| `code` | `varchar(60)` (kiracı-içi benzersiz, kısmi: `WHERE deleted_at IS NULL`) | K0 |
| `name` | `varchar(120)` | K0 |
| `compensation_type` | kapalı küme: `pay` / `time_off` / `choice` (varsayılan) | K0 |
| `pay_multiplier` | `numeric(4,2)` (varsayılan 1.50) | K0 |
| `time_off_multiplier` | `numeric(4,2)` (varsayılan 1.50 = 1,5 dk izin / 1 dk fazla mesai) | K0 |
| `weekly_limit_minutes` | `integer` (varsayılan **2700** = 45 saat) | K0 |
| `daily_limit_minutes` | `integer` NULL | K0 |
| `annual_limit_minutes` | `integer` NULL (varsayılan 16200 = 270 saat, önerilen) | K0 |
| `min_approval_minutes` | `integer` (bu değerin altı onay gerektirmez; varsayılan 0) | K0 |
| `requires_approval` | `boolean` (varsayılan true) | K0 |
| `requires_document` | `boolean` (varsayılan false; M16 entegrasyonu) | K0 |
| `scope_department_id` / `scope_position_id` | UUID NULL FK (kural kapsamı; NULL = kiracı geneli) | K1 |
| `effective_from` / `effective_to` | `date` NOT NULL / NULL | K0 |
| `status` | `RecordStatus` kapalı küme (`active`/`inactive`/`archived`) | K0 |
| `created_at`/`updated_at`/`deleted_at` | `timestamptz` (OD21 soft-delete) | K0 |

- Kısıtlar: `weekly_limit_minutes > 0`, `pay_multiplier >= 1`, `time_off_multiplier >= 0`,
  `effective_to IS NULL OR effective_to >= effective_from`,
  kısmi benzersizlik `(tenant_id, code) WHERE deleted_at IS NULL`.

**`hr.overtime_requests`** (fazla mesai talebi; `leave_requests` deseniyle hizalı)

| Kolon | Tip / kural | Sınıf |
|---|---|---|
| `id` | UUID PK | K0 |
| `tenant_id` | UUID NOT NULL FK | K1 |
| `employee_id` | UUID NOT NULL FK `hr.employees(id)` (PROTECT) | K1 |
| `rule_id` | UUID NOT NULL FK `hr.overtime_rules(id)` (PROTECT) | K0 |
| `attendance_record_id` | UUID NULL FK `hr.attendance_records(id)` (M15; PROTECT/SET_NULL — OD40) | K0 |
| `work_date` | `date` NOT NULL | K0 |
| `week_start_date` | `date` NOT NULL (**saklanan**; haftalık toplam/limit doğrulaması ve indeks için) | K0 |
| `start_time`/`end_time` | `time` NULL | K0 |
| `minutes` | `integer` NOT NULL (`> 0`) | K0 |
| `reason` | `text` | **K2** (serbest metin gerekçe olabilir → denetim kaydına **değer** yazılmaz) |
| `compensation_type` | `pay` / `time_off` (kuraldan türetilir, gerekçeli değiştirilebilir) | K0 |
| `pay_multiplier_snapshot` | `numeric(4,2)` NOT NULL (**onay anındaki** katsayı; sonradan kural değişse de sabit — OD34) | K0 |
| `status` | kapalı küme: `draft` / `pending` / `approved` / `rejected` / `canceled` | K0 |
| `submitted_at` / `decided_at` / `decided_by_id` | `timestamptz` NULL / `timestamptz` NULL / FK `hr.users(id)` SET_NULL | K1 |
| `canceled_at` / `cancel_reason` | `timestamptz` NULL / `text` | K1 / K2 |
| `document_ref` | UUID NULL (FK **yok** — `leave_requests.document_ref` ile aynı desen; M16 gelince gerçek FK) | K0 |
| `created_at`/`updated_at`/`deleted_at` | `timestamptz` | K0 |

- Durum geçişleri **kapalı küme** (leave ile aynı ilke, OD21: joker/sessiz geçiş yok):
  `draft → pending | canceled`; `pending → approved | rejected | canceled`;
  `approved → canceled`; `rejected/canceled` → (bitiş).
- Kısıtlar/index: `minutes > 0`, `week_start_date = date_trunc('week', work_date)::date`
  (CHECK), `(tenant_id, employee_id, week_start_date)` indeksi,
  `(tenant_id, status, work_date)` indeksi.

**`hr.overtime_entries`** (append-only olay/karşılık defteri — `leave_balance_entries` deseni)

| Kolon | Tip / kural | Sınıf |
|---|---|---|
| `id` | UUID PK | K0 |
| `tenant_id` | UUID NOT NULL FK | K1 |
| `request_id` | UUID NOT NULL FK `hr.overtime_requests(id)` (CASCADE) | K0 |
| `employee_id` | UUID NOT NULL FK (PROTECT) | K1 |
| `entry_kind` | kapalı küme: `approval` / `pay_compensation` / `time_off_credit` / `reversal` | K0 |
| `delta_minutes` | `integer` (işaretli) | K0 |
| `period_year` | `smallint` | K0 |
| `actor_id` | FK `hr.users(id)` SET_NULL | K1 |
| `reason` | `text` (düzeltme/ters kayıtta zorunlu) | K2 |
| `created_at` | `timestamptz` | K0 |

- İdempotans: `UNIQUE (request_id, entry_kind)` (talep başına tür başına **tek** kayıt
  — çift tüketim/çift ücret DB düzeyinde engellenir).

**Haftalık sınır ve yarış (öneri):** talep onaya sunulurken **ve** nihai onayda,
ilgili `(employee_id, week_start_date)` kilidi (`select_for_update` benzeri
satır/aralık kilidi) altında `SUM(minutes)` (statüsü `pending` + `approved`)
`rule.weekly_limit_minutes` ile karşılaştırılır. Sınır aşımı **fail-closed** reddedilir.
Alternatif: ayrı `hr.overtime_week_totals` denormalize tablo — **önerilmez** (ikinci
source-of-truth). Uygulanırsa kilit/guard zorunlu.

### 2.3 Ücret / izin karşılığı (PAY / TIME_OFF)

- **PAY:** onayda `pg_multiplier_snapshot` ve `minutes` ile **tutar üretilmez**;
  yalnız `pay_compensation` defter kaydı (dakika) yazılır. Dakika→para dönüşümü
  **payroll modülü** hazır olunca (`hr.payroll_lines.overtime_hours`) yapılır
  (bkz. bölüm 4/5). Bu, mevzuat hesabının kapsam dışı olmasıyla tutarlıdır ve
  sahte kesinlik üretilmez.
- **TIME_OFF:** onayda `time_off_credit` kaydı (`minutes × time_off_multiplier`)
  yazılır ve **leave ledger**'a aktarılır (bkz. 2.4). "Karşılık izin" için ayrı
  bir izin türü (sistem kodu, ör. `overtime_comp`) ya da mevcut tür seçilir — OD32.
- Karşılık türü `choice` ise talep sahibi seçer, onaylayan `SEPARATE_ACTOR` kuralıyla
  onaylar; seçim `overtime_entries`'ta kayıtlıdır (değişmezlik: onay sonrası tür
  değişmez, değişiklik = iptal + yeni talep).

### 2.4 Mevcut leave ledger ile entegrasyon (öneri — OD32)

Üç seçenek değerlendirilir; **önerilen (A)**:

- **(A)** `leave_balance_entries`'e yeni `entry_kind` (`overtime_credit`) + yeni
  nullable kolon `overtime_request_id` (FK) eklenir; zaman karşılığı izin, ilgili
  `leave_balances` satırında `adjustment_days`/yeni bir "hakediş" kolonu üzerinden
  **aynı işlemde** yazılır. Tek ledger, tek doğruluk kaynağı, çift tüketim
  `UNIQUE (overtime_request_id, entry_kind)` ile engellenir.
- **(B)** Mevcut `ADJUSTMENT` türü + zorunlu gerekçe ile yazılır (şema değişmez, ama
  kaynağı ayırt etmek için gerekçe metnine bağımlı kalınır → **zayıf**).
- **(C)** Fazla mesainin **kendi** bakiyesi (`hr.overtime_balances`) tutulur, leave
  ledger'a dokunulmaz (iki ledger → kalan izin toplamı iki yerde; **önerilmez**).

Her durumda: yüksek etkili olay (onay, iptal, ters kayıt) `log_action` ile denetlenir;
gerekçe **değeri** denetim kaydına yazılmaz.

### 2.5 Permission kodları (öneri — `overtime.*`)

| Kod | Ad | Kapsam | Not |
|---|---|---|---|
| `overtime.rule.manage` | Fazla mesai kuralı yönetimi | TENANT | `tenant_admin` + `hr_manager` |
| `overtime.request.read` | Fazla mesai taleplerini görme | SELF/UNIT/TENANT | kapsam ∧ izin |
| `overtime.request.create` | Fazla mesai talebi oluşturma | SELF + UNIT | kendi adına / kapsamındaki adına |
| `overtime.request.approve` | Fazla mesai onaylama/reddetme | UNIT/TENANT + ASSIGNED | `SEPARATE_ACTOR` |
| `overtime.request.approve.override` | Onay adımlarını kayıtlı gerekçeyle atlama | TENANT | leave ile aynı desen |
| `overtime.request.cancel` | Fazla mesai iptali | SELF + UNIT | onay öncesi SELF |
| `overtime.balance.read` | Fazla mesai (karşılık izin) bakiyesini görme | SELF/UNIT/TENANT | |

- Kod biçimi mevcut kurala uyar (`<kaynak>.<işlem>`, `docs/AUTHORIZATION.md` bölüm 4).
- Katalog değişikliği = **yeni migration** (`apps/rbac/migrations/…`); veri migration'ı
  donmuş kopya taşır (mevcut desen). Rol izin sayıları ve `verify_fresh_install.sh`
  beklentileri **birlikte** güncellenir.
- Karşılık **(PAY)** için ayrı kod yok; ücret verisi zaten hassas ve payroll izinleriyle
  korunur. **Karar:** `overtime.pay.read` gerekli mi — **OD33** (öneri: gerekli değil;
  `overtime.request.read` + payroll izinleri yeterli).

### 2.6 İş akışı (workflow)

1. **Taslak:** `overtime.request.create` ∧ (SELF ∨ kapsam) ile talep açılır; `minutes`
   sunucuda hesaplanır; haftalık sınır **soft-uyarı** düzeyinde ön kontrol edilir.
2. **Onaya sunum:** `week_start_date` hesaplanır, `pending` olur, onay adımları
   kurulur (genel `hr.approval_steps`): adım 1 **birincil yönetici** (`employee_manager`,
   varsa; `SEPARATE_ACTOR` ihlali ise atlanır) → adım 2 **yetkili rol** (`hr_manager`
   rol kodu + kapsam). Adım sırası **atlanamaz**.
3. **Haftalık limit doğrulaması:** submit ve nihai onayda kilit altında toplam kontrol;
   aşım → fail-closed red.
4. **Karar:** `overtime.request.approve` ∧ kapsam ∧ `ASSIGNED` ∨ TENANT ∧
   `SEPARATE_ACTOR`; kendi talebini onaylayamaz. Red gerekçesi kayıtlıdır.
5. **Nihai onay (tek kez):** `overtime_entries`'a tek `approval` kaydı; `compensation_type`'a
   göre `pay_compensation` **veya** `time_off_credit`; TIME_OFF ise leave ledger
   aktarımı (2.4) **aynı transaction** içinde.
6. **İptal:** onay öncesi SELF; onaylıda gerekçe zorunlu + `reversal` defter kaydı +
   (TIME_OFF ise) leave iadesi politika izin veriyorsa.
7. **Denetim:** create / submit / approve_step / approve / reject / cancel /
   override / reversal olayları `log_action` ile; değer değil **alan adı** yazılır.

### 2.7 Kabul kriterleri

- Haftalık sınır aşan talep ne submit ne onayda geçmez (fail-closed); ikinci bir
  onay denemesi **durum hatası** verir.
- Aynı talep için ikinci `pay_compensation`/`time_off_credit` kaydı **DB kısıtıyla**
  reddedilir (ölçülür).
- TIME_OFF onayında leave bakiyesi **tek** hareketle artar; çift bakiye artışı yok.
- `SEPARATE_ACTOR`: talep sahibi kendi fazla mesaisini onaylayamaz (`403`).
- Kapsam dışı çalışanın talebi görünmez (`404`); RLS yapısal testi yeni tabloları
  kapsar (FORCE + `tenant_id`).
- Denetim kaydında PII **değeri** yok; yalnız alan adı/kimlik.
- Payroll yokken **hiçbir** tutar hesaplanmaz/üretilmez.

---

## 3. Yapılandırılabilir çalışan kayıt/onboarding formu

### 3.1 Hedef davranış

- Kiracı (yetkili rol) çalışan kayıt formunu yapılandırır: hangi alanlar **görünür**,
  hangileri **zorunlu**, hangi **sırada** ve hangi **bölümde**. Görsel düzen
  (Django şablonu + vanilla JS) alanları bu yapılandırmadan üretir.
- **Sistem alanları korunur:** çekirdek alanlar (ör. `first_name`, `last_name`,
  `department_id`, `hire_date`) gizlenemez ve zorunlulukları düşürülemez.
- Dinamik katman **yeni PII yüzeyi açmaz**: alanlar mevcut kolonlara eşlenir
  (`hr.employees` / `hr.employee_personal_data`). Serbest "custom alan" **OD35**'e
  bırakılmıştır (öneri: v1'de yok).
- **K2/K3 sınıflandırması dinamik formdan bağımsız ve değiştirilemez:** bir alanın
  veri sınıfı katalogda sabittir; tenant onu "K2'ye düşüremez", şifrelemesini
  kaldıramaz. K3 alanlar yine `EncryptedCharField`/`EncryptedTextField` ile saklanır.
- Form yapılandırması **hiçbir kişisel veri içermez** (yalnız alan anahtarı/etiket/düzen).

### 3.2 Şema (öneri)

**`hr.form_field_definitions`** (tenant-scoped; kiracının form alan yapılandırması)

| Kolon | Tip / kural | Sınıf |
|---|---|---|
| `id` | UUID PK | K0 |
| `tenant_id` | UUID NOT NULL FK | K1 |
| `form_key` | `varchar(60)` (ör. `employee_onboarding`) | K0 |
| `field_key` | `varchar(60)` (katalogdaki **sabit** anahtar; ör. `national_id`) | K0 |
| `section_key` | `varchar(60)` (bölüm grubu; ör. `identity`, `contact`, `bank`) | K0 |
| `data_class` | kapalı küme: `K0`/`K1`/`K2`/`K3` (**katalogdan gelir, tenant değiştiremez**) | K0 |
| `input_type` | kapalı küme: `text`/`email`/`tel`/`date`/`choice`/`textarea` | K0 |
| `lock_level` | kapalı küme: `system_locked` / `hideable` / `optional` (bkz. 3.3) | K0 |
| `label_override` | `varchar(120)` NULL (tenant etiketi; yalnız `hideable`/`optional`) | K0 |
| `is_visible` | `boolean` (varsayılan true; `system_locked` için **zorla true**) | K0 |
| `is_required` | `boolean` (`system_locked` için düşürülemez) | K0 |
| `display_order` | `integer` | K0 |
| `validation` | `jsonb` (min/max, max_length, regex, choices; **kapalı küme** `choice` için zorunlu) | K0 |
| `storage_ref` | `varchar(120)` (hedef tablo.kolon; ör. `employee_personal_data.iban`) | K0 |
| `created_at`/`updated_at`/`deleted_at` | `timestamptz` | K0 |

- Kısmi benzersizlik: `(tenant_id, form_key, field_key) WHERE deleted_at IS NULL`.
- Katalog eşlemesi kod düzeyinde tutulur (leave `LeaveType` sistem kodları deseni);
  kiracı kurulumunda seed edilir. Katalogda olmayan `field_key` **reddedilir**.

**`hr.form_field_visibility`** (tenant override — rol/bölüm bazlı görünürlük)

| Kolon | Tip / kural | Sınıf |
|---|---|---|
| `id` | UUID PK | K0 |
| `tenant_id` | UUID NOT NULL FK | K1 |
| `field_definition_id` | UUID NOT NULL FK `hr.form_field_definitions(id)` (CASCADE) | K0 |
| `role_id` | UUID NULL FK `hr.roles(id)` (NULL = tüm roller) | K0 |
| `is_visible` | `boolean` | K0 |
| `is_required_override` | `boolean` NULL (NULL = definition'a bırak) | K0 |
| `created_at`/`updated_at`/`deleted_at` | `timestamptz` | K0 |

- Kısmi benzersizlik: `(tenant_id, field_definition_id, role_id) WHERE deleted_at IS NULL`.
- `system_locked` alan için `is_visible=false` **DB guard'ı ile reddedilir**
  (definition + visibility üzerinde BEFORE INSERT/UPDATE tetikleyicisi).

### 3.3 Alan kilit seviyeleri

| Seviye | Gizlenebilir | Zorunlu→opsiyonel | Etiket/sıra | Örnek alanlar |
|---|---|---|---|---|
| `system_locked` | ❌ | ❌ | ❌ (etiket sabit; sıra sabit) | `first_name`, `last_name`, `department_id`, `hire_date`, `employee_no` (sunucu üretir) |
| `hideable` | ✅ | ❌ (görünürse zorunlu) | ✅ | `national_id`, `iban`, `bank_name`, `personal_email`, `emergency_contact_phone` |
| `optional` | ✅ | ✅ | ✅ | `blood_type`, `citizenship`, `work_permit_type`, `address`, `birth_date` |

- `employee_no` **hiç görünmez/girilemez** (sunucu üretir: `EMP-000001`,
  `hr.employee_no_seq`); katalogda `system_locked` + `is_visible=false` özel durumu
  — bu istisna guard'da açıkça tanımlanır (gizli sistem alanı ≠ gizlenebilir alan).
- Zorunlu alanlar yalnız **ad/soyad** ile sınırlı başlar; tenant `optional`
  alanları zorunlu yapabilir ama **K3 alanları zorunlu yapamaz** (öneri — OD37).

### 3.4 K2/K3 sınıflandırmasının dinamik formda korunması

- `data_class` **katalogdan gelir**; `form_field_definitions.data_class` üzerinde
  tenant yazımı **guard ile reddedilir** (INSERT/UPDATE'te katalog değeriyle eşleşmeli).
- K3 alanlar için: kaydetme yolu **her zaman** şifreli kolon
  (`EncryptedCharField`); dinamik katman saklama biçimini **seçemez**.
- Kişisel veri okuma/yazma **mevcut kapıdan** geçer: `employee.sensitive.read` +
  organizasyon kapsamı; K3 için ayrı kod **OD30** ile açık (bugün tek kod, fail-closed).
- Dinamik form **gereksiz kişisel veri toplayamaz**: `is_required=true` ile K3 alan
  toplanması **engellenir** (öneri — OD37/OD31 hizası); K1/K2 alanlarda tenant
  serbest, ancak "en az veri" ilkesi (`DEVELOPMENT_RULES.md` madde 5) uygulanır.
- **Kan grubu (mevcut durum):** `hr.employee_personal_data.blood_type` **K3/şifreli**
  (OD31 açık). Dinamik form bu alanı **opsiyonel/gizlenebilir** sunar; zorunlu yapamaz.

### 3.5 Validation ve denetim

- **Sunucu tarafı zorunlu:** tüm validation backend'de; istemci doğrulaması yalnız
  UX'tir. `choice` alanlar **kapalı küme**den; geçersiz değer `400` (fail-closed).
- Alan eşlemesi (`storage_ref`) ile gelen değer ilgili kolona yazılır; sınıf
  uyuşmazlığı (K3 → düz metin yolu) **reddedilir**.
- **Denetim:** yapılandırma değişikliği `log_action(action='tenant_form.update')` ile
  — yalnız **alan anahtarları ve görünürlük/zorunluluk değişimleri** (PII yok).
  Kayıt gönderiminde `masked_changes` ile **değer değil alan adı** yazılır.
- Kiracı yalnız **kendi** form yapılandırmasını görür (RLS).
- Form yapılandırması çalışan kayıt **verisidir** (K0): liste/dışa aktarmalarda
  kişisel veri taşımaz.

### 3.6 Permission kodları (öneri)

| Kod | Ad | Kapsam | Not |
|---|---|---|---|
| `tenant_form.read` | Kayıt formu yapılandırmasını görme | TENANT | |
| `tenant_form.manage` | Kayıt formu yapılandırmasını yönetme | TENANT | `tenant_admin` (varsayılan); `hr_manager` opsiyonel |

- **`role.manage` ile ilişki:** form yapılandırması bir **yetki** konusudur, rol/izin
  yönetimiyle aynı değildir. Öneri: **ayrı kod** (`tenant_form.*`); `role.manage`
  sahibine **otomatik verilmez** (least privilege). Alternatif: `role.manage`'e
  bağlamak veya `form.definition.*` adlandırması — **OD36**.
- Katalog değişikliği yeni migration + rol sayısı beklentileri birlikte güncellenir.

### 3.7 Kabul kriterleri

- `system_locked` alan gizlenemez/zorunlu kılınanı düşürülemez; deneme DB guard'ıyla
  veya servis hatasıyla **reddedilir** (ölçülür).
- `data_class` tenant tarafından değiştirilemez; K3 alan şifreli kolonda durur
  (ham kolon düz metin değildir — ölçüm sorgusu).
- K3 alan **zorunlu yapılamaz**; kayıt formu K3'ü zorunlu istemez.
- Kapsam/yetkisiz kullanıcı form yapılandırmasını göremez (`403`); kapsam dışı `404`.
- Yapılandırma değişikliği denetim kaydına yazılır; kayıtta **PII değeri yoktur**.
- Kiracı yalnız kendi yapılandırmasını görür (RLS yapısal testi).
- Form üretimi mevcut şablon altyapısıyla yapılır; yeni frontend zinciri kurulmaz.

---

## 4. Bağımlılıklar ve sıralama

**Kesin bağımlılıklar (bu özellikler hazır olmadan ilgili parça yazılmaz):**

- **Payroll / ücret geçmişi (FEATURES §9):** hazır olmadan **overtime PAY tutarı
  hesaplanmaz**. Referans şema: `hr.salary_components`, `hr.employee_compensations`,
  `hr.payroll_lines.overtime_hours`. v1'de yalnız dakika + katsayı snapshot'ı tutulur.
- **M15 — vardiya/devam (`hr.shifts`, `hr.shift_assignments`, `hr.attendance_records`):**
  fazla mesainin **kaynağı** ve haftalık sınırın fiili verisi. M15 gelmeden overtime
  yalnız **manuel** olarak anlamlıdır; `attendance_record_id` entegrasyonu M15'e bağlıdır.
- **M16 — evrak (`hr.documents`, `hr.document_access`):**
  - Overtime `requires_document` türler ve `document_ref` gerçek FK'si M16 ile bağlanır.
  - Dinamik formda **`input_type='document'`** alan tipi M16'ya bağlıdır (bu turda
    tanımlanmaz).
- **Leave ledger (M14 ✅):** overtime TIME_OFF karşılığı bu ledger'a yazılır (OD32).
- **Bildirimler (modül yok):** `hr.notifications` tablosu **henüz yok**. Overtime onayı
  ve form tamamlama bildirimleri bu modüle bağlıdır; modül gelene kadar özellikler
  **bildirimsiz** çalışır (arayüz yoklaması/kuyruk). Bağımlılık kayda geçirilir.
- **Organizasyon (M12 ✅):** dinamik form mevcut `hr.employees`/`hr.employee_personal_data`
  kolonlarına eşlenir; ek şema gerekmez.

**Bağımsız çalışabilen parçalar:**

- Overtime **kaydı + onayı + TIME_OFF** (payroll hariç) M15'e bağlı ama M16/payroll'a değil.
- Dinamik form **sistem alanları** (custom alanlar hariç) M16 olmadan (evrak alanı hariç) yazılabilir.

---

## 5. Uygulama sırası önerisi

Mevcut en yüksek iş numarası `docs/DATABASE.md`'de **M33**'tür → yeni işler **M34+**.
`M15`/`M16` dokümandaki mevcut tanımlarıyla **değiştirilmez**.

| Sıra | İş | Neden önce bu |
|---|---|---|
| 0 | **Bu doküman (`HR_ROADMAP.md`)** | Karar/doküman, koddan önce (`DEVELOPMENT_RULES.md` madde 9). Açık OD'ler kapatılmadan kod yazılmaz. |
| 1 | **M15 — vardiya/devam** | Overtime'ın **kaynağı** (fiili çalışma) ve haftalık sınırın veri temeli; M15 olmadan overtime yalnız tahmine dayanır. |
| 2 | **M34 — overtime kayıt katmanı** (`hr.overtime_rules`/`request`/`entries` + RLS + guard + approval adımları + `overtime.*` katalog delta'sı) | M15'e bağlı; TIME_OFF *hariç* uçtan uca çalışır → erken değer, düşük risk. |
| 3 | **M16 — evrak** | Overtime `requires_document` ve formun `document` alan tipi için gerekli; ayrıca mevcut `document_ref` desenini gerçek FK'ye bağlar. |
| 4 | **M35 — dinamik form (sistem alanları)** (`hr.form_field_definitions`/`visibility` + `tenant_form.*` delta'sı) | M12 (✅) + M16 ile hazır; kiracıya erken değer; K3 şifrelemesi zaten yerde. Custom alanlar **OD35**'e bırakılır. |
| 5 | **M36 — payroll/ücret geçmişi** (FEATURES §9) | Overtime **PAY tutarı** yalnız bundan sonra hesaplanabilir (mevzuat kapsam dışı → kayıt düzeyi). |
| 6 | **M37 — overtime karşılık motoru** (PAY dönüşümü + TIME_OFF leave ledger aktarımı, OD32 kararına göre) | M34 (defter) + M36 (payroll) + M14 (ledger) ile tamamlanır; sıra bu üçüne bağlı. |
| 7 | **M38 — bildirimler** (overtime/onay + form tamamlama) | Bağımsız modül; 1–6 bildirimsiz çalışır, gelince bağlanır. |

> Not: 2 ve 4 (overtime kaydı, dinamik form) birbirinden **bağımsızdır** ve koşullar
> oluştuğunda paralel yürütülebilir; sıra önerisi risk/bağımlılık temellidir, katı sıra değildir.

---

## 6. Açık kararlar (öneri)

> Aşağıdaki numaralar **öneri**dir. `docs/AUTHORIZATION.md`'deki en yüksek numara
> **OD31**'dir; bu nedenle **OD32'den** başlanır. Kesin numara, ilgili karar
> `AUTHORIZATION.md` §14'e **işlendiğinde** ürün sahibince onaylanır. **Uydurulmuş
> numara yoktur** — hiçbiri verilmiş karar değildir.

- **OD32 (öneri) —** Fazla mesainin TIME_OFF karşılığı leave ledger'a nasıl yazılmalı?
  **Öneri (A):** yeni `leave_balance_entries.entry_kind='overtime_credit'` + yeni
  nullable FK `overtime_request_id`; tek ledger, çift tüketim DB kısıtıyla engellenir.
- **OD33 (öneri) —** Ücret karşılığı için ayrı `overtime.pay.read` izni gerekli mi?
  **Öneri:** gerekli değil; `overtime.request.read` + payroll izinleri yeterli.
- **OD34 (öneri) —** Overtime ücret/izin katsayısı hangi anda sabitlenir?
  **Öneri:** onay anında `*_multiplier_snapshot` alınır (sonradan kural değişse bile
  geriye dönük tutar değişmez).
- **OD35 (öneri) —** Tenant "serbest (custom) alan" tanımlayabilsin mi; olacaksa değerler
  nerede saklanır? **Öneri v1:** yalnız sistem alanları (custom **yok**); ileri
  aşamada ayrık K2/K3 kolonlu `hr.employee_custom_field_values` (JSONB **önerilmez**).
- **OD36 (öneri) —** Form yapılandırma izni `tenant_form.*` yeni kod mu, mevcut
  `role.manage` mi, yoksa `form.definition.*` adlandırması mı? **Öneri:** yeni ve ayrı
  kod (`tenant_form.read`/`tenant_form.manage`); `role.manage`'e **otomatik bağlanmaz**.
- **OD37 (öneri) —** K3 (özel nitelikli) alanlar dinamik formda zorunlu yapılabilir mi?
  **Öneri:** hayır; K3 alanlar yalnız `hideable`/`optional` olur, zorunlu yapılamaz
  (OD30/OD31 ile fail-closed hizası).
- **OD38 (öneri) —** Form yapılandırması sürümlenmeli mi; yeni zorunlu alanlar mevcut
  çalışanlar için ne anlama gelir? **Öneri:** sürümleme + yeni zorunluluklar yalnız
  **yeni kayıtlar** için geçerli (mevcut kayıtlar zorunlu alan eksikliğiyle geçersiz sayılmaz).
- **OD39 (öneri) —** Overtime onay adım sırası leave ile aynı mı olacak?
  **Öneri:** aynı — birincil yönetici → `hr_manager` rol kodu; genel `approval_steps`
  yeniden kullanılır (`subject_kind='overtime_request'`).
- **OD40 (öneri) —** `hr.attendance_records.overtime_minutes` (M15) ile
  `hr.overtime_requests` ilişkisi ne olacak? **Öneri:** `overtime_requests` **tek
  source-of-truth**; attendance `attendance_record_id` üzerinden **yalnız girdi**
  sağlar, toplam sınır doğrulaması yalnız `overtime_requests` üzerinden yapılır.

---

## Özet

- Bu doküman: `~/persopyro/docs/HR_ROADMAP.md` — **tasarım/plan** dokümanı, **kod yok**.
- Bölümler: (1) amaç/kapsam, (2) fazla mesai modülü, (3) yapılandırılabilir kayıt/
  onboarding formu, (4) bağımlılıklar, (5) uygulama sırası (M15/M16 + M34–M38),
  (6) açık kararlar.
- Önerilen açık karar sayısı: **9** (OD32–OD40) — hepsi **öneri**; `AUTHORIZATION.md`
  §14'e işlenmeyi ve ürün sahibi onayını bekler.
- Ana şema önerileri: `hr.overtime_rules`, `hr.overtime_requests`,
  `hr.overtime_entries`; `hr.form_field_definitions`, `hr.form_field_visibility`.
- Ana izin önerileri: `overtime.rule.manage`, `overtime.request.{read,create,approve,
  approve.override,cancel}`, `overtime.balance.read`; `tenant_form.{read,manage}`.
- Kickoff için ilk bağımlılık: **M15** (vardiya/devam); kritik blokaj: **payroll**
  (ücret hesabı için) ve **bildirim modülü** (henüz yok).

**Durum: designed-not-coded** (önerilen kararlar **open-decision** olarak işaretli).
