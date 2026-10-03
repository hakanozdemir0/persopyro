# API — Persopyro

Bu doküman API sözleşmesinin kurallarını tanımlar. **Henüz hiçbir uç nokta
kodlanmamıştır.** Yeni bir uç nokta yazılmadan önce bu kurallar okunur ve uç
nokta bu dokümana eklenir.

## 1. Genel kurallar

- Stil: REST + JSON (UTF-8).
- Sürümleme: yol içinde, `/api/v1/...`. Kırıcı değişiklik yeni sürüm gerektirir.
- Alan adları `snake_case`.
- Tarihler ISO-8601 (`2026-09-11T11:49:00Z`), para birimi alan adında belirtilir
  (`amount_try`) ve kuruş cinsinden tam sayı olarak taşınır.
- Kimlikler UUID (string).
- İstek gövdesinde `tenant_id` **gönderilmez**; kiracı bağlamı oturumdan
  çözümlenir. Gönderilirse yok sayılır.
- Bilinmeyen alanlar sessizce kabul edilmez; doğrulama hatası döner.

## 2. Kimlik doğrulama

- Oturum uçları **çerez** (HttpOnly) kullanır ve **CSRF korumalıdır**.
- API istemcileri için **opak token**: `Authorization: Token <token>` başlığı ile
  taşınır (Django `Bearer` biçimi **kullanılmaz**); yenileme ucu **yoktur** — süre
  bitince yeni token alınır.
- **Token gövdesi (OD26 — kapalı, uygulandı ✅):**
  `POST /api/v1/auth/token` → `tenant`, `email`, `password`, `totp_code?`,
  **`purpose`** (zorunlu, ≥ 10 karakter), **`scopes`** (zorunlu, en az bir izin
  kodu), `kind` (`interactive` | `service`, varsayılan `interactive`),
  `ttl_hours` (isteğe bağlı; `interactive` ≤ 720, `service` ≤ 2160).
  Kapsam **sahibinin izinlerinin alt kümesi** olmalıdır; boş kapsam, wildcard,
  katalogda olmayan kod ve `platform.*` → `400`. Başarılı üretim **`201 Created`**
  döner; düz metin token yalnızca bu yanıtta görünür (bir daha elde edilemez).
- Token ile yapılan isteklerde yetki **`kullanıcı izinleri ∩ token kapsamı`** olarak
  hesaplanır (fail-closed); kapsam yalnızca **daraltır**.
- **Kiracı erişimi (F11 — kapalı):** `suspended`/`canceled`/soft-delete kiracıda
  giriş ve mevcut oturum/token erişimi **kesilir**; istek hattı `403` +
  `tenant_unavailable` döner (token doğrulama katmanı da ayrıca reddeder).
- İptal: `POST /api/v1/auth/token/logout` (yalnızca isteği yapan token) ve
  **`POST /api/v1/auth/token/revoke-all`** (sahibin tüm token'ları). İptal
  **işaretlemedir** (satır silinmez) ve **tek yönlüdür**; çıkış sonrası aynı token
  `401` döner.
- Çok faktörlü doğrulama desteklenir; doğrulama bekleyen oturum kısıtlı yetkiye
  sahiptir.
- Kimlik doğrulama uç noktaları (`/api/v1/auth/*`) denetim kaydına yazılır.

## 3. Yetki

- Her uç nokta, gerektirdiği izin kodunu (`AUTHORIZATION.md` biçimi) belirtir.
- Yetki kontrolü API katmanında başlar, servis katmanında nesne seviyesinde
  tamamlanır.
- Açık izin yoksa istek reddedilir (varsayılan reddet).

## 4. Kaynak grupları (planlanan)

| Yol öneki | Kapsam | Gereken izin örnekleri |
|---|---|---|
| `/api/v1/auth` | Giriş, çıkış, token, parola sıfırlama | — |
| `/api/v1/tenants` | Kiracı ayarları, kullanım limitleri | `tenant.manage` |
| `/api/v1/users` | Kullanıcı ve rol atamaları | `user.manage` |
| `/api/v1/roles` | Rol ve izin yönetimi | `role.manage` |
| `/api/v1/org` | Şirket, departman, pozisyon | `org.department.manage` |
| `/api/v1/employees` | Çalışan kayıtları | `employee.*` |
| `/api/v1/leaves` | İzin talepleri, bakiyeler, onaylar | `leave.request.*` |
| `/api/v1/shifts` | Vardiya ve çalışma düzeni | `shift.*` |
| `/api/v1/attendance` | Devam kayıtları, zaman çizelgesi | `attendance.*` |
| `/api/v1/documents` | Evrak yükleme/indirme | `document.*` |
| `/api/v1/audit` | Denetim kayıtları (salt okunur) | `audit.read` |
| `/api/v1/billing` | Abonelik ve faturalar | `billing.manage` |

### 4.1 Uygulanmış uç noktalar (faz 2)

| Uç nokta | Yöntem | Kimlik doğrulama | Notlar |
|---|---|---|---|
| `/api/v1/auth/login` | POST | Yok (giriş) | Gövde: `tenant`, `email`, `password`, `totp_code?`. Oturum çerezi (HttpOnly) döner; **CSRF korumalı** |
| `/api/v1/auth/logout` | POST | Oturum | Oturumu kapatır; denetim kaydı yazar |
| `/api/v1/auth/password/reset/confirm` | POST | E-posta bağlantısındaki tek kullanımlık token | Gövde: `tenant`, `uid`, `token`, `new_password`, `new_password_confirm`. Token süresi dolunca veya kullanıldıktan sonra geçersiz olur; parola kuralları uygulanır ve önceki oturum/token sürümleri iptal edilir |
| `/api/v1/auth/token` | POST | Yok (giriş) | Gövde: `tenant`, `email`, `password`, `totp_code?` + **`purpose`, `scopes`, `kind?`, `ttl_hours?`** (OD26 — kapalı, uygulandı ✅); opak API token döner (**201**) |
| `/api/v1/auth/token/logout` | POST | Token | Yalnızca isteği yapan token'ı **iptal eder** (satır silinmez; `revoked_reason='logout'`) ✅ — başarılı çıkış **204**, çıkış sonrası aynı token **401** |
| `/api/v1/auth/token/revoke-all` | POST | Token | Sahibinin **tüm** token'larını iptal eder ✅ (OD26); döner: `{"data": {"revoked": <sayı>}}` (`200`) |
| `/api/health` | GET | Yok | Canlılık (veritabanına dokunmaz) |
| `/api/ready` | GET | Yok | Veritabanı erişilebilir mi (503 dönebilir) |

Giriş uçlarının davranışı:

- Kiracı bulunamazsa veya parola yanlışsa **aynı** yanıt döner
  (`invalid_credentials`) — kullanıcı numaralandırmasına karşı.
- MFA açıksa kod zorunludur: kod yoksa `mfa_required`, kod yanlışsa `mfa_invalid`.
  Bu bilgi yalnızca parola doğrulandıktan sonra döner.
- **Kiracı erişime kapalıysa** (askıda/iptal/silinmiş — F11) giriş **parola
  denenmeden** reddedilir: `403` + `tenant_unavailable` (oturum ve token uçlarının
  ikisinde de). Kiracı erişime kapalıyken mevcut oturum/token ile yapılan istekler
  de aynı kodla reddedilir.
- Bu uçlar en katı hız sınırı sınıfındadır (`auth`); sınır aşımında `429`.

### 4.2 Geliştirme/test uçları (`/test-ui/api/*`) — **üretim sözleşmesi DEĞİLDİR**

Manuel test arayüzünün (`/test-ui/`) kullandığı uçlar, sürümlenmiş üretim API'sinin
(`/api/v1/*`) parçası **değildir**; ayrı önek altında tutulur ki üretim sözleşmesi
genişlemesin. Tümü **oturum çerezi** ile çalışır (yeni kimlik doğrulama yolu
eklenmedi), güvenli yöntemlerde **CSRF** zorunludur ve varsayılan olarak **kapalıdır**:
`TEST_UI_ENABLED` (`PERSOPYRO_TEST_UI`, varsayılan `DEBUG`) kapalıyken bütün yol
**404** döner.

| Uç nokta | Yöntem | Notlar |
|---|---|---|
| `/test-ui/api/me` | GET | Oturumdaki kullanıcı, kiracı, roller, etkin izinler, organizasyon kapsamı |
| `/test-ui/api/dashboard` | GET | Panel: çalışan kaydı, dönem bakiyeleri, kendi talepleri, onay kuyruğu |
| `/test-ui/api/lookups` | GET | Form seçenekleri: birimler, unvanlar, aktif izin türleri, **kullanıcı hesapları** (bağlı çalışan bilgisiyle) + yönetim bayrakları |
| `/test-ui/api/departments` | GET / POST | Birim ağacı / yeni birim (`org.department.manage` ∧ kapsam); `?moves_for=<id>` ile **taşıma hedefleri** (kendisi ve alt ağacı hariç) |
| `/test-ui/api/departments/<uuid>/move` | POST | Üst birim değiştir (OD24 ağaç guard'ları) |
| `/test-ui/api/departments/<uuid>/delete` | POST | Soft delete (OD21) |
| `/test-ui/api/employees` | GET / POST | Kapsamdaki çalışanlar / yeni çalışan (`employee.create` ∧ kapsam). **Zorunlu:** `email`, `password`, `first_name`, `last_name`, `department_id`, `hire_date` — hesap + çalışan **tek işlemde**; `employee_no` istemciden alınmaz (sunucu `EMP-000001` üretir) |
| `/test-ui/api/balances` | GET | **Bakiye listesi (uç korunur)**: kapsamdaki çalışanların dönem bakiyeleri (`leave.balance.read`). Ayrı "İzin Bakileri" sekmesi **kaldırıldı** (hak yönetimi çalışan detayında); uç regresyon için duruyor. Sunucu tarafı filtre: `q`, `department_id`, `leave_type_id`, `durum=dusuk\|tukendi`. Satır: hak/kullanılan/bekleyen/kalan + `sonraki_hakedis` + `can_adjust` |
| `/test-ui/api/balances/adjust` | POST | Bakiye düzeltmesi / hakediş tanımlama (`leave.balance.manage` ∧ kapsam). Gövde: `employee_id`, `islem=duzeltme\|hakedis`, `leave_type_id`, `delta_days`, **`reason` (zorunlu)** — servis katmanı: `adjust_leave_balance` / `accrue_leave_entitlements` (idempotent) |
| `/test-ui/api/employees/<uuid>/balances` | GET | Çalışanın dönem bakiyeleri + **izin hakları** (bakiye kullanan tüm aktif türler; hak tanımlanmamışsa `—`) (`leave.balance.read`) |
| `/test-ui/api/employees/<uuid>/entitlements` | POST | **İzin hakkı tanımla/düzelt** (`leave.balance.manage` ∧ kapsam). Gövde: `leave_type_id`, `days` (mutlak hak), **`reason` (zorunlu, ≥ 5 karakter)**. Servis: `set_leave_entitlement` (hakediş kaydı yoksa `accrual`, varsa `adjustment`; fark yoksa **yazmaz**) — arayüz bakiye kolonunu doğrudan ezmez |
| `/test-ui/api/overtime` | GET / POST | **GET:** görünür kayıtlar (`SELF` ∨ kapsam; kapsam dışı **hiç dönmez**) — `?status=<pending\|approved\|rejected\|canceled>` süzgeci; `meta`: `kendi_kayitlarim`, `karar_bekleyen` (`can_decide`), `durum_sayilari`, `can_create`, `max_minutes`, `today`. **POST:** fazla mesai bildirimi (`overtime.request.create`; **yalnız kendi adına**). Gövde: `work_date`, `start_time`, `end_time`, `description`. Süre **sunucuda** `end - start`; 0/negatif, > 16 saat, gelecek tarih ve aynı gün çakışma reddedilir |
| `/test-ui/api/overtime/<uuid>/decision` | POST | Onay/red (`overtime.request.approve` ∧ kapsam ∧ adım); red gerekçesi zorunlu |
| `/test-ui/api/overtime/<uuid>/cancel` | POST | İptal (`overtime.request.cancel`; gerekçe zorunlu) |
| `/test-ui/api/employees/<uuid>/lifecycle/<islem>` | POST | **P1-B yaşam döngüsü** — `islem`: `suspend` (`employee.suspend`) / `restore` (`employee.restore`) / `terminate` (`employee.terminate`) / `archive` (`employee.delete` = arşivleme). Gövde: `reason` (her işlemde **zorunlu**); terminate için ayrıca `termination_date`, `termination_reason_code` (kapalı küme), `termination_note?`. Geçersiz geçiş **409**, izin/kapsam reddi **403**, aynı durum **no-op 200**. Merkezi servis: `apps/organization/lifecycle.py` (erişim kesme + audit aynı işlemde) |
| `/test-ui/api/employees/<uuid>/history` | GET | **P1-B iş geçmişi** (`employee.history.read` ∧ kapsam): business-readable olaylar (created / assignment_changed / employment_terms_changed / manager_changed / status_changed + alt etiketler); **PII değeri döndürülmez** |
| `/test-ui/api/employees/<uuid>/assignment` | POST | Departman/pozisyon değişikliği (`employee.update` ∧ kapsam). Gövde: `department_id` ve/veya `position_id`, `effective_from` (≥ bugün), **`reason` (zorunlu)**. Eski açık atama kapanır, yenisi açılır (OD60); snapshot senkron |
| `/test-ui/api/employees/<uuid>/employment-terms` | POST | İstihdam koşulu değişikliği (`employee.update` ∧ kapsam). Gövde: `employment_type`, `working_model`, `workplace_id?`, sözleşme/deneme alanları, `effective_from`, **`reason` (zorunlu)**. Yeni koşul satırı açılır (üzerine yazım yok); ücret alanı **yok** (P6) |
| `/test-ui/api/employees/<uuid>/manager` | POST | Birincil yönetici değişikliği (`employee.update` ∧ kapsam). Gövde: `manager_employee_id`, `effective_from?`, **`reason` (zorunlu)**. Mevcut `employee_manager` altyapısı (tek aktif birincil kısıtı korunur); audit: `employee.manager.change` |
| `/test-ui/api/leave-requests?scope=mine\|pending` | GET | Kendi talepleri / onay kuyruğu |
| `/test-ui/api/leave-requests` | POST | Talep oluştur (gün sayısı **sunucuda** hesaplanır) |
| `/test-ui/api/leave-requests/<uuid>/decision` | POST | Onay/red (`decision`: `approved`/`rejected`) |
| `/test-ui/api/leave-requests/<uuid>/cancel` | POST | İptal (gerekçe zorunlu) |
| `/test-ui/api/employees/<uuid>` | GET | Çalışan detayı: unvan/birim/yönetici, yıllık izin özeti, (yalnız `role.manage` sahibine) rol/kapsam/izin istisnaları |
| `/test-ui/api/employees/<uuid>/roles` | POST | Rol atama (`role.manage` ∧ kapsam); `UNIT` rollerde birim kapsamı zorunlu |
| `/test-ui/api/roles` | GET | Kiracının atanabilir rolleri (atanabilirlik gerekçesiyle); platform rolleri hariç |
| `/test-ui/api/role-assignments/<uuid>/revoke` | POST | Rol atamasını kaldırır (soft delete, OD21) |
| `/test-ui/api/role-assignments/<uuid>/restore` | POST | Kaldırılmış atamayı açıkça geri alır (OD21) |
| `/test-ui/api/positions` | POST | Unvan (pozisyon) tanımı oluşturur (`org.position.manage`) |
| `/test-ui/api/leave-types` | GET / POST | İzin türleri (tümü, pasifler dahil) / kiracıya özel tür ekleme (`leave.type.manage`; sistem kodları rezerve) |
| `/test-ui/api/leave-types/<uuid>/deactivate` | POST | İzin türünü pasifleştirir (silmez; mevcut talepler korunur) |
| `/test-ui/api/departments/<uuid>/update` | POST | Birimin adı/kodunu değiştirir (`org.department.manage` ∧ kapsam) |
| `/test-ui/api/employees?scope=...` | GET | Kapsamdaki çalışanlar; `SELF` kapsamında **yalnız kendi kaydı** (`meta.self_only`), her satırda `izin_durumu` (yeşil/sarı nokta verisi) |
| `/test-ui/api/employees` | POST | `manager_employee_id` verilirse birincil yönetici atanır (`employee.update` + kapsam; yazmadan önce doğrulanır) |
| `/test-ui/api/leave-requests?scope=pending` | GET | Onay kuyruğu: satırlarda `talep_sahibi_bakiye` (hak/kullanılan/bekleyen/kalan) ve `kendi_talebi` bayrağı |
| `/test-ui/api/leave-policies` | GET | İzin politikaları (`leave.type.manage`; yetkisizde `404`) |
| `/test-ui/api/leave-policies/<uuid>/update` | POST | `allow_negative_balance` / `allow_refund_after_approval` / `fixed_days` günceller (`update_leave_policy`; karar serviste, `leave.type.manage`) |
| `/test-ui/api/employees/<uuid>/personal` | GET / POST | Kişisel veriler (K2 **+ K3 kan grubu**) — okuma **ve** yazma `employee.sensitive.read` ∧ kapsam; izinsiz `403`, kapsam dışı `404`; `citizenship ≠ TR` iken izin türü zorunlu (`400`); kan grubu kapalı kümeden değilse `400`; kan grubu **şifreli** saklanır, yanıtta kod (`blood_type`) + etiket (`blood_type_label`) döner |
| `/test-ui/api/users` | POST | Giriş hesabı açar (`user.manage`): `email` + `password` (+ ad/soyad). Zayıf parola/çakışan e-posta `400`; parola yanıtta **dönmez**, denetimde yalnız e-posta yazılır |
| `/test-ui/api/roles/create` | POST | Kiracıya özel rol oluşturur (`role.manage`, `create_custom_role`): `name`, `code`, `scope_type` (TENANT/UNIT/UNIT_DIRECT/SELF). Varsayılan/var olan kod veya platform kapsamı `400` |
| `/test-ui/api/roles/<uuid>/update` | POST | Rol adını ve (etkin ataması yoksa) kapsam tipini günceller (`update_role_meta`); `role.manage` gerekir |
| `/test-ui/api/roles/<uuid>/delete` | POST | Özel rolü soft-delete eder (`role.manage`, OD21); varsayılan/sistem rollerinde veya aktif atama varsa reddedilir |
| `/test-ui/api/roles/<uuid>/permissions` | GET / POST | Rolün izin kümesini okur (modül gruplu, Türkçe adlarla) / yazar (`role.manage`) |
| `/test-ui/api/shifts` | GET / POST | Vardiya şablonlarını listeler / oluşturur (`shift.read`, `shift.schedule.manage`) |
| `/test-ui/api/shifts/<uuid>/update` | POST | Vardiya adı, saatleri, mola ve çalışma modelini günceller (`shift.schedule.manage`) |
| `/test-ui/api/shifts/<uuid>/delete` | POST | Ataması olmayan vardiya şablonunu soft-delete eder (`shift.schedule.manage`) |
| `/test-ui/api/shift-assignments` | GET / POST | Günlük planı listeler; tek gün veya tarih aralığında seçili haftanın günlerine vardiya atar (`shift.read`, `shift.assign`) |
| `/test-ui/api/shift-assignments/<uuid>/update` | POST | Kapsam içindeki tek günlük vardiya atamasını başka bir vardiya şablonuyla günceller (`shift.assign`; izin çakışması denetlenir) |
| `/test-ui/api/shift-control` | GET | Vardiya planını, onaylı izin/serbest zaman ve onaylı fazla mesai kayıtlarını aynı kontrol akışında listeler (`shift.read` + ilgili kapsam) |

Hata eşlemesi (tek yerde, `apps/testui/api.py`): `400` doğrulama (ör. `UNIT` rolde
eksik birim kapsamı), `401/403` kimlik ve yetki/kapsam, `404` kapsam dışı veya yok,
`409` durum/iş kuralı çakışması. Rol izin yazımında ek kurallar: platform izni (**OD3**),
bilinmeyen kod veya **boş küme** `400`; kendi rolü / sahip olunmayan izni dağıtma `403`.
Oturum yokken dönen `403` yanıtı
`error.code = "not_authenticated"` taşır; "yetki yok" durumundan böylece ayrılır.
Arayüz hiçbir yetki kararını kendisi vermez: her işlem servis katmanına delege
edilir (`apps/organization/services.py`, `apps/leave/services/*`), RLS yürürlükte
kalır ve hata gövdeleri yığın izi (stack trace) içermez.

## 5. Yanıt biçimi

Başarılı tekil kayıt:

```json
{ "data": { "id": "…", "…": "…" } }
```

Başarılı liste:

```json
{
  "data": [ /* … */ ],
  "page": { "limit": 25, "offset": 0, "total": 137 }
}
```

Hata:

```json
{
  "error": {
    "code": "validation_error",
    "message": "İnsan tarafından okunabilir açıklama",
    "fields": [ { "name": "email", "issue": "Geçersiz e-posta biçimi" } ]
  }
}
```

Kurallar:

- Hata gövdesi iç durum, SQL, yığın izi veya dosya yolu içermez.
- Hata mesajı, yetkisi olmayan kaydın varlığını sızdırmaz (404 tercih edilir).
- HTTP durum kodları: `200`, `201`, `204`, `400`, `401`, `403`, `404`, `409`,
  `422`, `429`, `500`.

## 6. Sayfalama, filtreleme, sıralama

- Sayfalama: `limit` (varsayılan 25, üst sınır 100) ve `offset`.
- Filtreleme: kaynağa özel sorgu parametreleri (`status`, `department_id`, tarih
  aralığı).
- Sıralama: `sort=<alan>&order=asc|desc`. Beyaz liste dışındaki alan reddedilir.
- Toplam sayı gerektiğinde döner; büyük tablolarda yaklaşık değer kullanılabilir
  ve bu durum belgelenir.

## 7. Yıkıcı işlemler

- Silme işlemleri varsayılan olarak yumuşak silmedir (soft delete).
- Fiziksel silme yalnızca ayrı, açık bir uç nokta ve yetki ile yapılır ve audit
  kaydına yazılır.
- Toplu işlemler kimlik listesi ile sınırlıdır; "hepsini sil" tarzı işlem yoktur.
- Toplu işlemler de tenant sınırının dışına çıkamaz.

## 8. Dosya yükleme/indirme

- Yükleme: `multipart/form-data`; boyut ve MIME türü sınırı uygulanır; dönen kayıt
  `storage_key` yerine belge kimliğidir.
- İndirme: doğrudan genel URL verilmez; kısa ömürlü imzalı bağlantı üretilir.
- `is_confidential` belgeler için ayrıca izin ve erişim kaydı gerekir.

## 9. Oran sınırlama (rate limiting)

- Kimlik doğrulama uç noktaları en katı sınırdadır.
- Sınır aşımında `429` ve `Retry-After` döner.
- Aynı hesaba yönelik tekrarlı başarısız giriş, hesap kilitleme kuralına bağlanır.

## 10. Sürümleme ve uyumluluk

- Alan eklemek geriye uyumludur; alan kaldırmak veya anlamını değiştirmek değildir.
- Kaldırılacak alanlar en az bir sürüm boyunca "deprecated" olarak işaretlenir.
- Her uç nokta değişikliği `CHANGELOG.md`'ye yazılır.

## 11. Uç nokta tanım şablonu

Yeni uç nokta eklerken bu bloğu `FEATURES.md`/ilgili dokümana ekleyin:

```
Uç nokta : POST /api/v1/leaves/requests
Amaç     : Çalışanın izin talebi oluşturması
İzin     : leave.request.create
Tenant   : Oturum bağlamından çözümlenir
Girdi    : leave_type_id, start_date, end_date, reason?
Çıktı    : data{ id, status, ... }
Audit    : audit_logs (action=create)
Hatalar  : 400, 401, 403, 422 (tarih çakışması/bakiye yetersiz)
```
