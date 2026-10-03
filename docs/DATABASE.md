# Veritabanı — Persopyro

Bu belge veri katmanının **doğrulanmış envanterini**, kesinleşen yetkilendirme
tasarımının veritabanı karşılığını (hedef şema) ve **güvenli migration planını**
içerir. Uygulama kodu, migration ve SQL dosyaları bu turda **değiştirilmemiştir**.

**Doğrulama kaynağı (2026-09-11):** Tüm "mevcut durum" bilgileri dosyalardan ve
çalışan veritabanından (PostgreSQL 18.6, `persopyro`) ölçülerek çıkarılmıştır;
varsayım yoktur. Kaynaklar: `apps/*/migrations/*.py` (18 dosya),
`scripts/01_bootstrap.sql`, `scripts/02_grant_app_privileges.sql`,
`apps/rbac/permission_catalog.py`, `config/settings.py` ve canlı katalog
sorguları (`pg_class`, `pg_policy`, `pg_indexes`, `information_schema`).

**İşaretler:** ✅ mevcut ve doğrulandı · 🔜 tasarlandı, uygulanmadı ·
⚠️ mevcut koda müdahale gerektirir · ❓ OPEN DECISION

**Not (etiket çakışması):** `F#` iki farklı ad alanında kullanılır — bu belgede
**veri göçü iş kalemleri** (`F1`–`F10`), `docs/SECURITY.md`'de **güvenlik bulguları**
(`F1`–`F11`). Aynı numara farklı şeyleri gösterebilir; numara yerine **belge adıyla**
atıf yapılmalıdır (`DATABASE.md F10` = token iptali; `SECURITY.md F10` = typosquatting).

İlgili belgeler: `docs/AUTHORIZATION.md` (yetki modeli, OD listesi),
`docs/SECURITY.md` (kontroller, tehdit modeli), `docs/ARCHITECTURE.md` (K1–K22).

---

## 0. Özet: mevcut durum haritası

| Ölçüm | Sonuç |
|---|---|
| Şemalar | `hr`, `public` + **`platform` ✅** (M1–M5 uygulandı — ayrı güvenlik sınırı, ayrı DB rolü) |
| `hr` içindeki tablo sayısı | **21** (10 uygulama tablosu + 11 Django/teknik tablo) |
| Kiracıya bağlı (`tenant_id`) ve RLS'li tablo | **7** (`tenants`, `users`, `roles`, `role_permissions`, `user_roles`, `user_permission_overrides`, `audit_logs`) — hepsinde `rls=true` **ve** `force_rls=true` |
| RLS'siz tablo | `permissions` (global), `tenant_directory` (giriş dizini), `api_tokens`, `cache_table` + Django tabloları |
| RLS politikası sayısı | 7 (hepsi `ALL`, `USING` = `WITH CHECK`) |
| Kiracılar arası referans guard tetikleyicisi | 9 adet (6 tabloda) |
| `hr` fonksiyonları | 3: `current_tenant_id`, `ensure_same_tenant_ref`, `sync_tenant_directory` |
| Veritabanı rolleri | `persopyro_app`, `persopyro_owner` (ikisi de `super=false`, `bypassrls=false`) |
| Uygulanmış migration | `tenancy` 0001–0007, `accounts` 0001–0002, `rbac` 0001–0003, `audit` 0001 (+ Django: auth, admin, contenttypes, sessions, django_q) |
| İzin kataloğu | **29** izin, 11 modül |
| Veri | 1 kiracı (`demo`), 1 kullanıcı, 4 rol, 64 rol-izin bağı, 1 rol ataması, 2 denetim kaydı, 0 API token |
| `public_id` (genel kimlik) | **kodda ve şemada yok** ❌ → hedef şemada eklenecek |

**En önemli tespit:** Kiracı bağlamı olmadan kiracı tabloları **0 satır** döndürür.
Bu belgenin ilk ölçümünde `tenants/users/roles` "0 satır" görünmüştü; bağlam
kurulunca gerçek sayılar (1 kiracı, 1 kullanıcı, 4 rol) çıktı. Bu, RLS'in
fail-closed çalıştığının canlı kanıtıdır — ve her ölçümün bağlamla yapılması
gerektiğinin.

---

# A) MEVCUT ŞEMA

## A.1 Tablo envanteri (doğrulanmış)

`hr` şeması iki grup tablo barındırır. Django'nun kendi tabloları da `hr` içine
düşmüştür, çünkü bağlantı `search_path=hr,public` ile açılıyor.

**A.1.1 Uygulama tabloları (10)**

| Tablo | Amaç | RLS | FORCE | Guard | Satır (ölçüldü) |
|---|---|---|---|---|---|
| `tenants` | Kiracı (şirket) kaydı; slug/domain, durum, limitler, ayarlar | ✅ | ✅ | sync tetikleyici | 1 |
| `users` | Kiracı kullanıcısı (özel `AUTH_USER_MODEL`) | ✅ | ✅ | 1 | 1 |
| `roles` | Kiracıya bağlı rol (varsayılan/özel) | ✅ | ✅ | — | 4 |
| `permissions` | **Global** izin kataloğu (kiracı verisi yok) | — | — | — | 29 |
| `role_permissions` | Rol → izin bağı (kiracıya bağlı) | ✅ | ✅ | 1 | 64 |
| `user_roles` | Kullanıcı → rol ataması (süreli olabilir) | ✅ | ✅ | 3 | 1 |
| `user_permission_overrides` | Kişiye özel izin ekleme/çıkarma | ✅ | ✅ | 2 | 0 |
| `audit_logs` | Denetim kaydı (append-only) | ✅ | ✅ | 1 | 2 |
| `api_tokens` | API token'ı (yalnızca özet) | — | — | — | 0 |
| `tenant_directory` | Giriş öncesi slug/domain → kiracı eşlemesi | — | — | sync tetikleyici | 1 |

**A.1.2 Django/teknik tablolar (11) — dikkat**

| Tablo | Kaynak | Kullanılıyor mu? |
|---|---|---|
| `django_migrations` | Django | ✅ gerekli |
| `django_session` | `sessions` | ✅ gerekli (oturumlar DB'de) |
| `django_content_type` | `contenttypes` | ✅ gerekli (bağımlılık) |
| `cache_table` | Veritabanı cache | ✅ gerekli (K17) |
| `django_q_ormq`, `django_q_schedule`, `django_q_task` | `django_q` | ✅ gerekli (kuyruk) |
| `auth_permission`, `auth_group`, `auth_group_permissions` | `django.contrib.auth` | ❌ **kullanılmıyor** — kendi RBAC'imiz var; **yetkiler geri alındı ✅** (M22b; ölçüm: app rolünde hiç yetki yok) |
| `django_admin_log` | `django.contrib.admin` | ❌ kullanılmıyor; `/admin/` **kaldırıldı ✅** (M22) — yeni kurulumlarda tablo **hiç oluşmaz** |

**Karar (OD19 — kapalı, 2026-09-11) — ✅ UYGULANDI (M22–M22d):** `/admin/` yolu
kaldırıldı, `django.contrib.admin` uygulama listesinden çıkarıldı, `create_superuser`
artık `is_staff=True` atamaz (açık hata verir) ve bu tabloların (`auth_*`,
`django_admin_log`) uygulama rolü yetkileri **tamamen geri alındı**. Tablolar teknik
olarak kalabilir (`django.contrib.auth`; `AbstractBaseUser`, parola hash'leme ve oturum
kimlik doğrulaması için gereklidir) ama **izin kaynağı olamaz**. Belirleyici ölçüm:
Django'nun `AdminSite.has_permission` kapısı `is_active and is_staff`
(`django/contrib/admin/sites.py:207`) — yani `is_staff=True` olan aktif bir kiracı
kullanıcısı mevcut oturumuyla admin dizinini açabilir (bugün 0 model kayıtlı
olduğu için etki boş dizinle sınırlıdır).

## A.2 Tablo kartları (mevcut)

### `tenants`
- **PURPOSE:** Kiracı (şirket) kökü; izolasyonun çıpası.
- **PRIMARY KEY:** `id` (uuid) · **UNIQUE:** `slug`, `domain`
- **FOREIGN KEYS:** yok (kök tablo)
- **TENANT ISOLATION:** Kendi `id`'si üzerinden — politika: `id = hr.current_tenant_id()`
- **PERSONAL DATA LEVEL:** K1 (kurumsal iletişim: `email`, `phone`, `address`, `tax_number`)
- **RLS:** ✅ etkin + FORCE · **AUDIT REQUIREMENT:** yüksek (oluşturma/durum/limit değişikliği)
- **Kolonlar (21):** `id, name, legal_name, tax_number, email, phone, country, city, address, status, max_users, max_storage_mb, max_active_employees, max_employee_records, valid_from, valid_until, settings(jsonb), created_at, updated_at, deleted_at, slug, domain`

### `users`
- **PURPOSE:** Kiracı kullanıcısı; kimlik doğrulama + organizasyon üyeliği.
- **PRIMARY KEY:** `id` (uuid) · **UNIQUE:** `(tenant_id, email)`
- **FOREIGN KEYS:** `tenant_id → tenants.id`, `created_by_id → users.id` (self)
- **TENANT ISOLATION:** `tenant_id = hr.current_tenant_id()`; `created_by_id` guard tetikleyicisi
- **PERSONAL DATA LEVEL:** **K2** (`first_name`, `last_name`, `email`, `phone`) · K3'e yakın: `totp_secret`
- **RLS:** ✅ etkin + FORCE · **AUDIT REQUIREMENT:** yüksek (oluşturma, güncelleme, pasifleştirme, MFA değişimi, rol ataması)
- **Kolonlar (21):** `password, last_login, id, email, first_name, last_name, phone, status, is_staff, email_verified_at, password_changed_at, failed_login_count, lockout_until, multi_factor_enabled, totp_secret, last_login_at, created_at, updated_at, deleted_at, created_by_id, tenant_id`
- **`is_staff` (OD19 — kapalı, uygulandı ✅):** Django Admin kaldırıldı; bayrak hiçbir Persopyro yetkisi **vermez** ve üretimde `True` **olamaz** → uygulama guard'ı (`User.save`) + veritabanı kısıtı `users_is_staff_disabled CHECK (NOT is_staff)` (`accounts/0004`). Denetim: `manage.py audit_is_staff`. `is_superuser` alanı **yoktur** (`PermissionsMixin` kullanılmaz).
- **Eksik:** `public_id` ❌ (hedef şemada eklenecek — bölüm B)

### `roles`
- **PURPOSE:** Kiracıya bağlı rol (varsayılan şablon veya kiracının oluşturduğu özel rol).
- **PRIMARY KEY:** `id` (uuid) · **UNIQUE:** `(tenant_id, code)`
- **FOREIGN KEYS:** `tenant_id → tenants.id` (guard tetikleyicisi **yok**; `created_by_id` kolonu **yok** — dokümanın eski 3.3 listesi bu satırda yanlıştı, düzeltildi)
- **TENANT ISOLATION:** `tenant_id = hr.current_tenant_id()`
- **PERSONAL DATA LEVEL:** K0
- **RLS:** ✅ etkin + FORCE · **AUDIT REQUIREMENT:** yüksek (rol oluşturma/silme, izin seti değişimi)
- **Kolonlar (11):** `id, created_at, updated_at, deleted_at, name, code, role_type, scope_type, description, is_assignable, tenant_id`
- **Kısıt (M8 ✅):** `roles_scope_type_valid` — `CHECK (scope_type IN ('TENANT','UNIT','UNIT_DIRECT','ASSIGNED','SELF'))`; `scope_type` **NOT NULL**. `SYS` bu kümeye **alınmaz**: §4.4 `SYS`i yalnızca platform rollerine verir (`platform.roles`, OD3).
- **Doğrulanan veri (M7-M9 sonrası):** `tenant_admin` (29 izin, `TENANT`), `hr_manager` (22, `TENANT`), `tenant_officer` (19, `TENANT`), `dept_manager` (14, `UNIT`), `employee` (6, `SELF`)
- **Alan (M8 ✅ UYGULANDI — 2026-09-11):** `scope_type` = **rolün izinlerinin ulaşabileceği azami organizasyon kapsamı** (OD5 §5.1). İzin **üretmez**; karar `izin ∧ kapsam`. Backfill: `tenant_admin`/`tenant_officer`/`hr_manager` = `TENANT`, `dept_manager` = `UNIT`, `employee` = `SELF`, bilinen kod dışı (custom) roller = `TENANT` (§4.1; kiracı tanımlı rol) — eşlenen custom roller göç çıktısında **raporlanır**. Atama düzeyinde daraltma: `user_roles.scope_org_unit_id` (M9).

### `permissions`
- **PURPOSE:** Global izin kataloğu (kiracıdan bağımsız tanım tablosu).
- **PRIMARY KEY:** `id` (uuid) · **UNIQUE:** `code`
- **FOREIGN KEYS:** yok · **TENANT ISOLATION:** kapsam dışı (kiracı verisi içermez)
- **PERSONAL DATA LEVEL:** K0
- **RLS:** yok (bilinçli; gerekçe A.5) · **AUDIT REQUIREMENT:** orta (katalog değişikliği — migration ile)
- **Kolonlar (7):** `id, created_at, updated_at, code, name, module, description`
- **Mevcut kodlar (29, modül bazında):** `attendance` (2), `audit` (1), `billing` (1), `document` (4), `employee` (5), `leave` (4), `org` (3), `role` (2), `shift` (3), `tenant` (2), `user` (2)
- ⚠️ **Yetki sorunu:** uygulama rolü `persopyro_app` bu tabloda **INSERT/UPDATE/DELETE** yetkisine sahip (ölçüldü). Katalog migration'lar tarafından doldurulur; uygulamanın yazma yetkisi gereksizdir → geri alınmalı (bölüm C, M6).

### `role_permissions`
- **PURPOSE:** Rol → izin bağı (kiracıya bağlı; tenant_id taşır).
- **PRIMARY KEY:** `id` (uuid) · **UNIQUE:** `(role_id, permission_id)`
- **FOREIGN KEYS:** `role_id → roles.id`, `permission_id → permissions.id`, `tenant_id → tenants.id`
- **TENANT ISOLATION:** `tenant_id` + `role_id` guard tetikleyicisi · **PERSONAL DATA LEVEL:** K0
- **RLS:** ✅ + FORCE · **AUDIT REQUIREMENT:** yüksek (izin verilmesi/kaldırılması)
- **SOFT DELETE:** **yok** — bağ tablosudur, `deleted_at` kolonu **yoktur** (kolonlar:
  `id, created_at, updated_at, permission_id, role_id, tenant_id`); kayıt fiziksel
  silinir. OD21 bağ tabloları için soft-delete öngörmez. Bu yüzden yetki
  çözümlemesinde filtrelenecek bir `deleted_at` yoktur (F7 ölçüm düzeltmesi).

### `user_roles`
- **PURPOSE:** Kullanıcıya rol ataması (süreli olabilir — `expires_at`, kapsamlı olabilir — `scope_org_unit_id`).
- **PRIMARY KEY:** `id` · **UNIQUE:** `(user_id, role_id, scope_org_unit_id)` **`NULLS NOT DISTINCT`** (M9 ✅) — kapsamsız rollerde çift atamayı engeller, kapsamlı rollerde (aynı rol farklı birim) çoklu atamaya izin verir
- **FOREIGN KEYS:** `user_id → users.id`, `role_id → roles.id`, `granted_by_id → users.id`, `tenant_id → tenants.id`; `scope_org_unit_id` FK'si **M10 sonrası** (`departments.id`) — M9 kısmi uygulama
- **TENANT ISOLATION:** `tenant_id` + 3 guard tetikleyicisi + **`trg_user_role_scope_guard`** · **PERSONAL DATA LEVEL:** K1
- **RLS:** ✅ + FORCE · **AUDIT REQUIREMENT:** **yüksek** (yetki değişikliği)
- **Eksik:** `created_by_id` yok (bölüm B)
- **Kapsam (OD5 ✅ kapalı, M9 ✅ kısmi — 2026-09-11):** `scope_org_unit_id` (uuid, **nullable**)
  kapsamın tek kaynağıdır. **Uygulanan:** kolon + `NULLS NOT DISTINCT` benzersizlik +
  guard tetikleyicisi — (a) `scope_type ∈ {UNIT, UNIT_DIRECT}` iken kapsam **zorunlu**
  (yoksa INSERT/UPDATE reddedilir), (b) diğer tiplerde kapsam **NULL olmalı** (kullanılmayan
  veri üretilmez), (c) rol kiracı bağlamında görünmüyorsa yazım reddedilir (fail-closed).
  **Ertelenen (M10 sonrası):** `departments(id)` FK'si, "kapsam aynı kiracının **aktif**
  birimini göstermeli" guard'ı ve alt ağaç (subtree) çözümlemesi — bu yüzden **M10 öncesi
  kapsam çözülemez ve `UNIT`/`UNIT_DIRECT` atamaları izin ÜRETMEZ** (`apps/rbac/services/scope.py`).
  Aynı kullanıcı aynı kiracıda **birden fazla** atama taşıyabilir; etkin kapsam etkin
  atamaların **birleşimidir** (kiracılar arası birleşim yok). Soft-delete edilmiş veya
  süresi geçmiş atama **kapsam üretmez**; kullanıcı kendi kapsamını **yükseltemez**.
  Ayrıntı: `docs/AUTHORIZATION.md` OD5 §5.1–5.9.

### `user_permission_overrides`
- **PURPOSE:** Kişiye özel izin ekleme/çıkarma (`effect`), gerekçeli.
- **PRIMARY KEY:** `id` · **UNIQUE:** `(user_id, permission_id)`
- **FOREIGN KEYS:** `user_id`, `granted_by_id` (ikisi `users`), `permission_id → permissions.id`, `tenant_id → tenants.id`
- **TENANT ISOLATION:** `tenant_id` + 2 guard · **PERSONAL DATA LEVEL:** K1 (gerekçe metni serbest → K2 sızabilir ⚠️)
- **RLS:** ✅ + FORCE · **AUDIT REQUIREMENT:** yüksek

### `audit_logs`
- **PURPOSE:** Denetim kaydı; append-only.
- **PRIMARY KEY:** `id` · **UNIQUE:** yok
- **FOREIGN KEYS:** `tenant_id → tenants.id`, `user_id → users.id` (nullable)
- **TENANT ISOLATION:** `tenant_id` + `user_id` guard · **PERSONAL DATA LEVEL:** K0 **olmalı** (değer değil, alan adı taşır)
- **RLS:** ✅ + FORCE · **Yetkiler:** uygulama rolünde **yalnızca INSERT + SELECT** (UPDATE/DELETE yok ✅ doğrulandı)
- **Kolonlar (11):** `id, created_at, updated_at, action, table_name, record_id, changes(jsonb), request_id, ip_address, tenant_id, user_id`
- **Eksik (hedef):** `actor_kind`, `platform_actor_id`, `actor_label`, `access_grant_ref`, `purpose`, zincir özeti ❌ — planlanan alanlar ve `CHECK` kısıtı OD23'te kesinleşti.
- **Not (bulgu F9 — KAPATILDI ✅ 2026-09-11):** `user` ilişkisi artık **`PROTECT`**.
  Önceki `SET_NULL`, ORM toplayıcısının denetim satırına **UPDATE** yazması demekti
  (append-only kuralıyla çelişiyordu; koruma yalnızca uygulama rolünün UPDATE
  yetkisinin olmamasına dayanıyordu). Django FK DDL'inde `ON DELETE` yazmadığı için
  veritabanı kısıtı zaten `NO ACTION`'dır; koruma artık uygulama katmanındadır
  (`ProtectedError`, hiç SQL üretmeden). Migration: `audit/0002`.

### `api_tokens`
- **PURPOSE:** Kiracı kullanıcısının API token'ı; yalnızca SHA-256 özeti saklanır.
- **PRIMARY KEY:** `id` · **UNIQUE:** `token_hash`
- **FOREIGN KEYS:** `user_id → users.id`, `tenant_id → tenants.id`
- **TENANT ISOLATION:** RLS yok; bağlam token satırından öğrenilir (gerekçe A.5) — token **yalnızca kendi `tenant_id`'si** için bağlam kurar, kiracı sınırını aşamaz · **PERSONAL DATA LEVEL:** K1
- **RLS:** yok (bilinçli) · **Yetkiler (M30 — uygulandı ✅):** `SELECT`, `INSERT` + **kolon düzeyi** `UPDATE (last_used_at, revoked_at, revoked_reason)`; tablo düzeyinde UPDATE ve `DELETE` **geri alınmıştır** (iptal = işaretleme; satır silinemez)
- **Kolonlar (mevcut, 14):** `id, tenant_id, user_id, token_hash, label, token_kind, purpose, scopes, expires_at, revoked_at, revoked_reason, last_used_at, created_at, updated_at`
- **OD26 — uygulandı ✅ (M29/M30/F10):** `token_kind` (`interactive`/`service`), `purpose` (NOT NULL, ≥10 karakter), `scopes text[]` (NOT NULL; boş yalnızca iptal edilmiş satırda; wildcard yasak; değerler `hr.permissions.code`'dan ve `platform.*` **yasak**), `revoked_at`, `revoked_reason` (kapalı küme). **Değişmezlik:** scope/purpose/expires_at/token_kind/token_hash/tenant_id/user_id değişmez; iptal **tek yönlü** (veritabanı tetikleyicisi). Ham token ve özeti **loglanmaz**.

### `tenant_directory`
- **PURPOSE:** Giriş öncesi slug/domain → kiracı eşlemesi.
- **PRIMARY KEY:** `tenant_id` · **UNIQUE:** `slug`, `domain`
- **FOREIGN KEYS:** `tenant_id → tenants.id` (CASCADE)
- **TENANT ISOLATION:** kapsam dışı; yalnızca `tenant_id, slug, domain, updated_at` taşır (kiracı verisi yok)
- **RLS:** yok (bilinçli) · **Yetkiler:** uygulama rolünde **yalnızca SELECT** ✅ (yazma SECURITY DEFINER tetikleyicide)

## A.3 RLS politikaları (doğrulanmış)

| Tablo | Politika adı | Komut | `USING` = `WITH CHECK` |
|---|---|---|---|
| `tenants` | `tenant_isolation` | ALL | `id = hr.current_tenant_id()` |
| `users` | `tenant_isolation` | ALL | `tenant_id = hr.current_tenant_id()` |
| `roles` | `tenant_isolation` | ALL | `tenant_id = hr.current_tenant_id()` |
| `role_permissions` | `tenant_isolation` | ALL | `tenant_id = hr.current_tenant_id()` |
| `user_roles` | `tenant_isolation` | ALL | `tenant_id = hr.current_tenant_id()` |
| `user_permission_overrides` | `tenant_isolation` | ALL | `tenant_id = hr.current_tenant_id()` |
| `audit_logs` | `tenant_isolation` | ALL | `tenant_id = hr.current_tenant_id()` |
| `departments` ✅ (M10) | `tenant_isolation` | ALL | `tenant_id = hr.current_tenant_id()` |
| `positions` ✅ (M10) | `tenant_isolation` | ALL | `tenant_id = hr.current_tenant_id()` |
| `employees` ✅ (M12) | `tenant_isolation` | ALL | `tenant_id = hr.current_tenant_id()` |
| `employee_personal_data` ✅ (M12) | `tenant_isolation` | ALL | `tenant_id = hr.current_tenant_id()` |
| `employee_manager` ✅ (M13) | `tenant_isolation` | ALL | `tenant_id = hr.current_tenant_id()` |
| `leave_types` ✅ (M14) | `tenant_isolation` | ALL | `tenant_id = hr.current_tenant_id()` |
| `leave_policies` ✅ (M14) | `tenant_isolation` | ALL | `tenant_id = hr.current_tenant_id()` |
| `leave_policy_rules` ✅ (M14) | `tenant_isolation` | ALL | `tenant_id = hr.current_tenant_id()` |
| `leave_balances` ✅ (M14) | `tenant_isolation` | ALL | `tenant_id = hr.current_tenant_id()` |
| `leave_balance_entries` ✅ (M14) | `tenant_isolation` | ALL | `tenant_id = hr.current_tenant_id()` |
| `leave_requests` ✅ (M14) | `tenant_isolation` | ALL | `tenant_id = hr.current_tenant_id()` |
| `approval_steps` ✅ (M14) | `tenant_isolation` | ALL | `tenant_id = hr.current_tenant_id()` |

**19/19 tabloda** `rls=true` **ve** `force_rls=true` (ölçüldü: M10–M13 sonrası 7 → **12**;
M14 sonrası → **19**).
Politikalarda platform istisnası **yoktur** (bu, OD3 kararının veritabanı
karşılığıdır: politika gevşetilmez).

## A.4 Kiracılar arası referans guard'ları (19 tetikleyici, 11 tablo)

`hr.ensure_same_tenant_ref()` + `BEFORE INSERT OR UPDATE` tetikleyicisi:

| Tablo | Sütun(lar) → hedef |
|---|---|
| `users` | `created_by_id → users` |
| `role_permissions` | `role_id → roles` |
| `user_roles` | `user_id → users`, `role_id → roles`, `granted_by_id → users`, **`scope_org_unit_id → departments` (M9 tamamlama)** |
| `user_permission_overrides` | `user_id → users`, `granted_by_id → users` |
| `audit_logs` | `user_id → users` |
| `departments` ✅ (M11) | `parent_id → departments`, `manager_employee_id → employees` |
| `employees` ✅ (M12) | `user_id → users`, `department_id → departments`, `position_id → positions` |
| `employee_personal_data` ✅ (M12) | `employee_id → employees` |
| `employee_manager` ✅ (M13) | `employee_id → employees`, `manager_employee_id → employees`, `created_by_id → users` |
| `tenants` | (guard değil) `sync_tenant_directory` — `AFTER INSERT/UPDATE/DELETE` |

**Neden gerekli:** Yabancı anahtar kontrolü RLS'ten etkilenmez; `tenant_id` doğru
olsa bile bir satır başka kiracının nesnesine bağlanabilir. Guard, hedef satırın
kiracısını RLS görünürlüğü üzerinden okur ve görünmüyorsa **hata verir
(fail-closed)**.

## A.5 Fonksiyonlar (3 — doğrulanmış)

| Fonksiyon | Görev | SECURITY DEFINER | Volatility |
|---|---|---|---|
| `hr.current_tenant_id()` | Kiracı bağlamını okur; `NULLIF(current_setting('app.tenant_id', TRUE), '')::uuid` | ❌ (invoker) | stable |
| `hr.ensure_same_tenant_ref()` | Guard tetikleyici gövdesi | ❌ (invoker) | volatile |
| `hr.sync_tenant_directory()` | Dizini güncel tutar | ✅ | volatile |

**Ölçülmüş ders (korunacak):** Kiracı tablolarında `FORCE RLS` tablo sahibini de
bağladığı için, `SECURITY DEFINER` bir fonksiyon bile kiracı satırını **göremez**.
Bu yüzden "hangi kiracı bu slug'a ait?" sorusu `SECURITY DEFINER` fonksiyonla
çözülemedi (denendi, başarısız) ve `tenant_directory` yansı tablosu kuruldu.
`tenancy/0007` artık kullanılmayan `hr.tenant_id_by_slug/domain` fonksiyonlarını
**düşürdü** (doğrulandı: bu iki fonksiyon veritabanında yok).

## A.6 Roller ve yetkiler (doğrulanmış)

| Rol | SUPERUSER | BYPASSRLS | LOGIN | CREATEDB | Not |
|---|---|---|---|---|---|
| `persopyro_app` | ❌ | ❌ | ✅ | ❌ | Uygulama bağlantısı |
| `persopyro_owner` | ❌ | ❌ | ✅ | ✅ | Yalnızca migration + test veritabanı |
| `postgres` | ✅ | ✅ | ✅ | ✅ | Sistem süper kullanıcısı (uygulama kullanmaz) |

Şema yetkileri: `hr` → `persopyro_app`: USAGE var, **CREATE yok**; `persopyro_owner`: USAGE + CREATE.
`public` → ikisi de USAGE; `persopyro_app`: CREATE yok.

Tablo yetkileri (`persopyro_app`, ölçüldü):

| Tablo | Yetkiler | Değerlendirme |
|---|---|---|
| `audit_logs` | INSERT, SELECT | ✅ doğru (append-only) |
| `tenant_directory` | SELECT | ✅ doğru (yazma tetikleyicide) |
| `permissions` | DELETE, INSERT, SELECT, UPDATE | ⚠️ **fazla** → yazma geri alınmalı (M6) |
| `tenants`, `users`, `roles`, `role_permissions`, `user_roles`, `user_permission_overrides` | DELETE, INSERT, SELECT, UPDATE | ✅ beklenen |
| `api_tokens` | INSERT, SELECT + kolon düzeyi UPDATE (`last_used_at`, `revoked_at`, `revoked_reason`) | ✅ beklenen (M30 — uygulandı; tablo UPDATE ve DELETE **geri alındı**) |

Ayrıca `scripts/02_grant_app_privileges.sql` ile yeni nesneler için
`ALTER DEFAULT PRIVILEGES` tanımlı: `hr` şemasında oluşturulacak **her yeni tabloya**
uygulama rolüne tam DML verilir. Bu, yeni tablolarda bilinçli bir "en az yetki"
turu gerektirir (bölüm D.6).

## A.7 İndeksler (özet — uygulama tabloları)

| Tablo | Benzersiz / performans indeksleri |
|---|---|
| `tenants` | `tenants_pkey`, `tenants_slug_..._uniq`, `tenants_domain_key`, `tenants_status_idx`, `%_like` (slug/domain) |
| `users` | `users_pkey`, `users_tenant_email_uniq`, `users_tenant_id_...`, `users_tenant_status_idx`, `users_email_idx`, `users_created_by_id_...` |
| `roles` | `roles_pkey`, `roles_tenant_code_uniq`, `roles_tenant_type_idx`, `roles_tenant_id_...` |
| `permissions` | `permissions_pkey`, `permissions_code_key`, `permissions_module_idx`, `%_like` |
| `role_permissions` | `role_permissions_uniq (role_id, permission_id)`, `role_perms_tenant_idx (tenant_id, role_id)`, FK indeksleri |
| `user_roles` | `user_roles_uniq (user_id, role_id)`, `user_roles_tenant_user_idx`, FK indeksleri |
| `user_permission_overrides` | `user_permission_overrides_uniq (user_id, permission_id)`, `upo_tenant_user_idx`, FK indeksleri |
| `audit_logs` | `audit_tenant_created_idx (tenant_id, created_at DESC)`, `audit_tenant_action_idx`, `audit_record_idx (tenant_id, table_name, record_id)` |
| `api_tokens` | `api_tokens_token_hash_key`, `api_tokens_tenant_user_idx` |
| `tenant_directory` | `tenant_directory_pkey`, `_slug_key`, `_domain_key` |

Not: Benzersiz indeksler **soft-delete'i dikkate almaz** (bkz. H.4 / OD21).

## A.8 Migration geçmişi (uygulanmış)

| App | Migration'lar | İçerik |
|---|---|---|
| `tenancy` | 0001 → 0007 | `hr` şeması + `tenants`/`current_tenant_id`; RLS etkinleştirme; aynı-kiracı guard'ları; `role_permissions` RLS; slug/domain; (düşürülen) arama fonksiyonları; `tenant_directory` |
| `accounts` | 0001 → **0004** | `users`; `api_tokens` (+ knox tablosu düşürüldü); **0003: OD26 token modeli** — `token_kind`/`purpose`/`scopes`/`revoked_at`/`revoked_reason` + CHECK'ler + tetikleyici + yetki turu (M29/M30) ve F10 veri göçü; **0004: OD19** — `is_staff` kısıtı (`users_is_staff_disabled`), ölçümlü ön kontrol, `auth_*`/`django_admin_log` yetkilerinin geri alınması (M22b/M22c) |
| `rbac` | 0001 → 0003 | `roles`, `permissions`, `role_permissions`, `user_roles`, `user_permission_overrides`; `role_permissions.tenant_id`; izin kataloğu seed (29) |
| `audit` | 0001 → **0002** | `audit_logs`; **0002: `AuditLog.user` → `PROTECT`** (F9 kapandı; SQL düzeyinde no-op, FK zaten `NO ACTION`) |

**Kural (uygulanmış migration değişmez):** Bu dosyalar tarihsel kayıttır; yeni
ihtiyaç **yeni migration** ile eklenir. Bu belgedeki hiçbir iş kalemi mevcut
migration dosyasını düzenlemez (tek istisna yok — `0002` daha önce bir kez
düzeltilmişti ve o hata sıfırdan kurulumu bozmuştu; tekrarlanmayacak).

## A.10 P1-A sonrası güncel ölçüm (2026-09-13)

**Genel (canlı geliştirme veritabanı, salt okuma):**

| Ölçüm | Değer |
|---|---|
| `hr`+`public` şemasında tablo | **37** (P1-A öncesi 33) |
| RLS + FORCE etkin tablo | **23** (öncesi 19) |
| İzin kataloğu (`hr.permissions`) | **47** (41 kiracı + 6 platform; öncesi 39) |
| Yeni tablolar | `workplaces`, `employee_employment_terms`, `employee_assignments`, `form_field_definitions` |

**Tenant bazlı (kiracı bağlamı kurularak; `demo`):**

| Ölçüm | Değer |
|---|---|
| Çalışan | 9 (5'i kullanıcıya bağlı; çoklu bağlı kullanıcı **0**) |
| `employee_assignments` | **9** açık atama (backfill) |
| `employee_employment_terms` | 0 (bilinçli: uydurma veri yazılmadı — ilk satır P1-B'de) |
| `workplaces` | 0 (kiracı kendi işyerini P1-B/P1-C arayüzünden açacak; backfill işyeri uydurmaz) |
| `form_field_definitions` | **33** (katalogdaki tüm alanlar; 8'i A seviyesi zorunlu). **Yeni kiracı provizyonu** bu satırları `ensure_default_form_fields(tenant)` ile üretir (`apps/organization/services.py`; `create_default_roles` ile aynı akış, idempotent) — migration backfill'i yalnız **mevcut** kiracıları kapsar |
| Rol izin sayıları | `tenant_admin` 41 · `hr_manager` 30 · `tenant_officer` 23 · `dept_manager` 17 · `employee` 8 |

> **Not (drift düzeltmesi):** senkron öncesi `demo` kiracısında `hr_manager` şablondan sapmıştı
> (33 izin; `tenant.manage`, `role.manage`, `user.manage`, `employee.delete`, `billing.manage`,
> `leave.balance.manage`, `document.delete` gibi katalog dışı kodlar). OD16 gereği
> `rbac/0012` senkronu ile **30**'a indirildi; özel roller (`ik_sorumlusu`) **değiştirilmedi**.

## A.9 Mevcut veri durumu (ölçüldü, kiracı bağlamı kurularak)

```
tenants=1 (slug: demo, durum: active)  users=1  roles=4  role_permissions=64
user_roles=1  user_permission_overrides=0  audit_logs=2  api_tokens=0
tenant_directory=1  permissions=29
roller: tenant_admin (29 izin), hr_manager (22), manager (7), employee (6)
```

Bu veri **yerel geliştirme/duman testi** verisidir (`scripts/smoke.sh`);
üretim verisi yoktur → veri migration'ları için gerçek veri hacmi riski düşüktür,
ama plan bunu varsaymamalıdır.

## A.11 P5a V1 sonrası güncel ölçüm (2026-09-13) — fazla mesai + izin hakkı

> **Not:** A.1/A.2 kartları M-serisi ölçümleridir; A.10 (P1-A) ve bu bölüm (P5a V1)
> **güncel** değerleri verir. Çelişki varsa bu bölüm geçerlidir.

**Genel (canlı geliştirme veritabanı, salt okuma):**

| Ölçüm | Değer |
|---|---|
| `hr`+`public` şemasında tablo | **38** (P1-A: 37) |
| RLS + FORCE etkin tablo | **24** (23) |
| İzin kataloğu (`hr.permissions`) | **51** (45 kiracı + 6 platform; P1-A: 47) |
| Yeni tablo | `hr.overtime_requests` |
| `hr` şemasında (internal olmayan) tetikleyici | **61** (P5a: +4) |
| Uygulanan yeni migration | `overtime/0001–0004`, `leave/0005`, `rbac/0013` |

**Tenant bazlı (kiracı bağlamı kurularak; `demo`):**

| Ölçüm | Değer |
|---|---|
| Rol izin sayıları | `tenant_admin` 45 · `hr_manager` 33 · `tenant_officer` 27 · `dept_manager` 21 · `employee` 11 |

### `hr.overtime_requests` (P5a V1 ✅)

- **PURPOSE:** Fazla mesai bildirimi ve onay durumu (V1: talep + onay; kural/katsayı/ücret yok).
- **PRIMARY KEY:** `id` (uuid) · **FOREIGN KEYS:** `tenant_id → tenants.id`,
  `employee_id → employees.id`, `created_by_id/decided_by_id → users.id`
- **TENANT ISOLATION:** `tenant_id = hr.current_tenant_id()` (tek politika `tenant_isolation`);
  üç yabancı anahtar için **kiracılar arası guard tetikleyicisi** (`overtime/0004`)
- **PERSONAL DATA LEVEL:** K0 (çalışan/karar referansı; serbest metin `description` ≤ 500)
- **RLS:** ✅ etkin **+ FORCE** · **AUDIT REQUIREMENT:** yüksek (oluşturma/karar/iptal)
- **Kolonlar (15):** `id, created_at, updated_at, deleted_at, work_date, start_time, end_time,
  total_minutes, description, status, compensation_type, decided_at, decided_by_id,
  canceled_at, cancel_reason, employee_id, tenant_id, created_by_id`
- **Süre kaynağı:** `total_minutes` **yalnız sunucuda** `end_time - start_time` ile hesaplanır;
  DB bunu `overtime_requests_minutes_match` `CHECK`'i ile **zorlar** (uygulama hatası DB'ye
  yansımaz).
- **CHECK kısıtları (8):** `time_order` (`end > start`), `minutes_positive` (> 0),
  `minutes_max` (≤ 960), `status_valid` (`pending|approved|rejected|canceled`),
  `compensation_valid` (boş veya `pay|time_off`), `cancel_reason` (iptalde zorunlu),
  `description_length` (≥ 5), `minutes_match` (yukarıdaki uyum).
- **Durum makinesi (DB tetikleyicisi `trg_overtime_requests_state`):** yalnız `pending` açılış;
  `pending → approved|rejected|canceled`; terminal durumdan çıkış yok.
- **İndeks/kısıt:** kısmi benzersizlik `overtime_requests_slot_uniq`
  (`tenant, employee, work_date, start_time` — `deleted_at IS NULL`), indeksler
  `(tenant, employee, work_date)` ve `(tenant, status)`.
- **Yetki (ölçüldü):** uygulama rolü `persopyro_app` → `SELECT/INSERT/UPDATE` var,
  `DELETE/TRUNCATE` **yok** (soft delete: OD21); `persopyro_platform` → erişim **yok**.
- **Onay:** `hr.approval_steps` yeniden kullanılır (`subject_kind='overtime_request'`,
  `leave/0005`); onay sırası birincil yönetici → İK (OD39 ile aynı model, OD62).

### İzin hakkı (entitlement) — P5a V1 değişiklikleri

- **Servis:** `set_leave_entitlement(actor, employee, leave_type, period_year, days, reason)` —
  dönemde `accrual` kaydı yoksa **hakediş** yazar, varsa **gerekçeli düzeltme** (`adjustment`);
  fark sıfırsa **hiçbir satır yazılmaz**. `reason` ≥ 5 karakter (servis + DB), aktör ve denetim
  kaydı zorunlu; `leave.balance.manage` ∧ organizasyon kapsamı kapısı vardır.
- **Değişmez (invariant):** bir bakiyede dönem başına **tek** `accrual` kaydı
  (`leave_balance_entries_accrual_uniq`, kısmi benzersizlik) — manuel hak tanımlama, yıllık
  hakediş ile **çakışmaz/çoğalmaz**. `scripts/verify_fresh_install.sh` (6f) ve
  `tests/test_p5a_ui_entitlement.py` bunu ölçer.

---

# B) HEDEF ŞEMA

Tasarım `docs/AUTHORIZATION.md` (OD1–OD18 kapalı/açık kararlar) ve
`docs/SECURITY.md` ile **çelişmez**. Temel ilkeler:

1. Kiracı tablolarında RLS politika ifadesi **değişmez**; platform için gevşetilmez.
2. Platform verisi **ayrı şemada** (`platform`) ve **ayrı veritabanı rolüyle** erişilir.
3. `BYPASSRLS` **kullanılmaz**.
4. Platform kullanıcılarının `tenant_id`'si **yoktur**; kiracı kullanıcı tablosuyla
   birleştirilmez.
5. Genel kimlik: `<kiracı-slug>-<8×Crockford Base32>`, benzersiz, tahmin edilemez,
   değişmez.

## B.1 Yeni şema: `platform`

`CREATE SCHEMA platform AUTHORIZATION persopyro_owner;`
`REVOKE ALL ON SCHEMA platform FROM PUBLIC;` → **`persopyro_app`'e hiçbir yetki
verilmez**; yalnızca `persopyro_platform` rolüne USAGE verilir.

Yeni veritabanı rolü:

| Rol | SUPERUSER | BYPASSRLS | Yetkiler |
|---|---|---|---|
| `persopyro_platform` | ❌ | ❌ | `platform` şemasında tam DML (tablo bazlı **allowlist**: `users`, `roles`, `role_permissions`, `user_roles`, `tokens`, `tenant_registry`, `company_plans`) ✅; `hr` şemasında **yalnızca izin kataloğu okuma** (`USAGE` + `SELECT ON hr.permissions`, **yazma yok**) ✅. Kiracı tablolarına (`hr.tenants`, `hr.users`, `hr.audit_logs`, ...) **bu pakette yetki verilmez** — RLS'e tabi kiracı erişimi **erişim-onay katmanıyla** (M17–M19, OD1/OD4) ve denetim kaydıyla açılır; **şema varlığı erişim yetkisi değildir**. `auth_*`/`django_admin_log` dahil hiçbir Django teknik tablosunda yetkisi yoktur ✅ |

`persopyro_app` **`platform` şemasını göremez** (USAGE yok) → kiracı isteğinden
platform verisine yol yoktur. (Test: T17.)

## B.2 Platform tabloları ✅ (M1–M5 uygulandı: `users`, `roles`, `role_permissions`, `user_roles`, `tokens`, `tenant_registry`; `company_plans` `platform.0013`)

Kart biçiminde; hepsi `platform` şemasında, RLS **yok** (kiracı verisi taşımaz),
erişim yalnızca `persopyro_platform` rolüyle.

### `platform.users`
- **PURPOSE:** SUPERADMIN/ADMIN kimliği. Kiracıya bağlı **değil**.
- **PRIMARY KEY:** `id` uuid · **UNIQUE:** `email` (global), `public_id`
- **FOREIGN KEYS:** yok
- **TENANT ISOLATION:** kapsam dışı — kiracı verisi yok; `tenant_id` kolonu **yoktur** (OD3 kararı)
- **PERSONAL DATA LEVEL:** K2 (`full_name`, `email`) — erişim yalnızca platform rolünde
- **RLS:** yok (şema yetkisiyle korunur) · **AUDIT REQUIREMENT:** yüksek (oluşturma, MFA, rol ataması, giriş)
- **Kolonlar:** `id, public_id, email, full_name, password, status, is_active, failed_login_count, lockout_until, password_changed_at, mfa_enabled, mfa_secret_encrypted, created_at, updated_at, deleted_at, created_by_id`
- **`public_id` kuralı (OD27/OD28 kapalı):** kiracı dışı oldukları için **rezerve `plt` ön eki** kullanılır (`plt-<8×Crockford>`); kiracı kullanıcılarının `<tenant-prefix>` ad alanıyla **çakışmaz**. `plt` rezerve listede olduğu için hiçbir kiracı bu ön eki alamaz.

### `platform.roles` ✅
- **PURPOSE:** `superadmin`, `platform_admin` tanımları.
- **PRIMARY KEY:** `id` · **UNIQUE:** `code` · **FK:** yok
- **Kolonlar (uygulama):** `id, code, name, description, created_at, updated_at, deleted_at ✅`
  — `deleted_at` M31 ile eklendi (K25 standardı: silme işareti; **silinen rol yetki vermez**).
- **Kod kapalı küme:** `CHECK (code IN ('superadmin','platform_admin'))` ✅
- **İzin bağları:** ayrı tabloda (`platform.role_permissions`); **izin devralma (inheritance) yok** —
  `superadmin` 6/6, `platform_admin` 4/4 açık bağla tanımlı (M31).
- **TENANT ISOLATION:** kapsam dışı · **PERSONAL DATA LEVEL:** K0 · **RLS:** yok
- **AUDIT:** rol tanımı değişikliği kayıtlanır

### `platform.role_permissions` ✅
- **PURPOSE:** Platform rolü → izin bağı. İzin kataloğu **paylaşılır** (`hr.permissions`, salt okunur).
- **PRIMARY KEY:** `id` · **UNIQUE:** `(role_id, permission_id)` · **FK:** `role_id → platform.roles`, `permission_id → hr.permissions`
- **Kolonlar (uygulama):** `id, role_id, permission_id, created_at, deleted_at ✅` (M31)
- **Yalnız `platform.*` kodu yetki üretir:** çözümleyici kodları `platform.` ön ekiyle süzer;
  ortak katalogda kiracı izni yanlışlıkla bağlansa bile platform yetkisi **üretmez** (M32, test).
- **TENANT ISOLATION:** kapsam dışı · **RLS:** yok · **AUDIT:** yüksek

### `platform.user_roles` ✅
- **PURPOSE:** Platform kullanıcısı → platform rolü (süreli olabilir).
- **PRIMARY KEY:** `id` · **UNIQUE:** `(platform_user_id, role_id)` · **FK:** `platform_user_id → platform.users`, `role_id → platform.roles`, `granted_by_id → platform.users`
- **Kolonlar (uygulama):** `id, platform_user_id, role_id, granted_by_id, granted_at, expires_at, deleted_at ✅` (M31)
- **Atama/kaldırma (M32):** atama `granted_by_id` ile kayıtlanır; kaldırma **soft-delete**tir
  (satır kalır → denetim izi). `expires_at` verilirse `granted_at`'ten sonra olmalıdır ve
  süresi geçen atama **yetki vermez**. Bootstrap (M33) `granted_by_id = NULL` ile yazılır.
- **RLS:** yok · **AUDIT:** yüksek

### `platform.tokens`
- **PURPOSE:** Platform API/service token'ı (OD22 — kapalı). Kiracı token'larından (`hr.api_tokens`) **tamamen ayrıdır**; tenant token tablosuna platform kaydı karıştırılmaz (yöntem B reddedildi).
- **PRIMARY KEY:** `id` · **UNIQUE:** `token_hash` · **FK:** `platform_user_id → platform.users`, `created_by_id → platform.users`
- **Kolonlar:** `id, platform_user_id (owner), token_kind (interactive|service), purpose NOT NULL, scope text[] NOT NULL, token_hash (SHA-256), label, created_at, expires_at NOT NULL, revoked_at, last_used_at, created_by_id`
  - Kısıtlar: `CHECK (array_length(scope, 1) > 0)` (boş kapsam **reddedilir**), `CHECK (expires_at > created_at)`, ham token alanı **yok**.
- **Süre sınırları (uygulama katmanı + test):** `interactive` ≤ **24 saat**; `service` ≤ **90 gün** ve **superadmin izin kümesiyle üretilemez**. Uzun ömürlü sınırsız SUPERADMIN token **yasak**.
- **İptal:** `revoked_at` işaretlenir (satır silinmez → denetim izi korunur); rol değişimi, kullanıcı pasifleştirme, MFA ve parola değişiminde ilgili token'lar otomatik iptal edilir.
- **TENANT ISOLATION:** kapsam dışı; **tenant RLS sınırını aşamaz** (platform rolü `NOBYPASSRLS`); kiracı verisi yalnızca geçerli `AccessGrant` ile ve denetim kaydıyla okunur.
- **RLS:** yok (platform şeması; erişim yalnızca `persopyro_platform` rolünde)
- **PERSONAL DATA LEVEL:** K1 (token sahibi referansı) · **AUDIT:** üretim, kullanım, iptal kayıtlanır

### `platform.tenant_registry`
- **PURPOSE:** Platformun kiracı **referans indeksi** (OD20 — kapalı). `hr.tenants` verisinin ikinci tam kopyası **değildir**; yalnızca platform operasyonları için gereken alanları taşır.
- **PRIMARY KEY:** `tenant_id` · **UNIQUE:** `slug`, `domain` · **FK:** `tenant_id → hr.tenants.id` (CASCADE)
- **Authoritative alan sahipliği:** `slug`, `domain`, `status`, `max_users`, `max_storage_mb`, `max_active_employees`, `max_employee_records`, `valid_from`, `valid_until`, `created_at`, `deleted_at` → **`hr.tenants`** (yansı); `plan_code`, `profile_fields`, `billing_ref`, `internal_note` → **bu tablo** (tek sahibi platform). `name/legal_name/tax_number/email/phone/address/country/city/settings` buraya **kopyalanmaz**.
- **Senkronizasyon:** **tek yönlü** `hr.tenants` → bu tablo, `SECURITY DEFINER` tetikleyiciyle (aynı işlem içinde). Yansı yazımı başarısız olursa kaynak yazımı **geri alınır** (fail-closed, sapma yok). Ters yön yoktur: platform, kiracı alanlarını registry üzerinden değiştirmez.
- **TENANT ISOLATION:** kapsam dışı (kiracı iş verisi yok) · **RLS:** yok
- **PERSONAL DATA LEVEL:** K1 metadata; `profile_fields` sadece şirket başına seçilen alan kodlarını taşır, şirket profil değerleri burada kopyalanmaz.
- **AUDIT:** durum/limit değişikliği (kaynak `hr.tenants` üzerinden)
- `profile_fields` yalnız şirket başına seçili alan kodlarını saklar; şirket profil
  değerleri burada kopyalanmaz. SUPERADMIN kanonik `hr.tenants` kaydını kiracı
  bağlamında günceller; trigger tarih/limit alanlarını registry'ye yansıtır.
- **Tutarlılık:** registry ↔ `hr.tenants` sapması için düzenli kontrol + test (M21, H2)

### `platform.company_plans`
- **PURPOSE:** Şirket oluşturma ve düzenlemede kullanılabilen plan adları ve varsayılan limitler.
- **Yetki:** `persopyro_platform` için tablo allowlist'inde tam DML; `persopyro_app` ve `PUBLIC` için erişim yoktur.
- **AUDIT:** Plan ekleme/düzenleme platform audit olayına plan koduyla yazılır.

### `platform.access_requests` / `platform.access_grants` (M18–M19 — `platform.0011` ile uygulandı)
- **PURPOSE:** Platform kaynaklı kişisel veri erişim talebi ve süreli izni (OD1/OD4).
- **`access_requests`:** `id, requested_by (platform_user_id), target_tenant_id, resource_type, resource_ids uuid[], field_list text[], purpose text NOT NULL, status (pending/approved/rejected/expired/cancelled), approvals jsonb, created_at, decided_at, expires_after_minutes`
  - **Kısıt:** `purpose` için **asgari uzunluk + anlamlılık** kontrolü (`PURPOSE_QUALITY`) — uygulama katmanı + `CHECK (length(btrim(purpose)) >= 20)`.
- **`access_grants`:** `id, request_id, target_tenant_id, granted_fields text[], starts_at, expires_at NOT NULL, revoked_at, use_count, last_used_at`
  - **Koşul:** `GRANT_VALID` = `revoked_at IS NULL AND now() < expires_at`.
- **TENANT ISOLATION:** kapsam dışı ama **tek kiracıya hedefli** (`target_tenant_id` zorunlu)
- **RLS:** yok (platform şeması) · **AUDIT:** **çok yüksek** — talep, onay, red, başlangıç, bitiş (OD4)

> Bu erişim akışı kişisel veri erişim talepleri içindir. SUPERADMIN şirket profili,
> planı, tarihleri ve limitleri şirket ayrıntı ekranından doğrudan günceller; ikinci
> bir onay gerekmez. Profil değişiklikleri platform audit kaydına alan adlarıyla yazılır.

### `platform.audit_logs`
- **PURPOSE:** Platform akışının denetim kaydı (kiracı akışından ayrı — K22).
- **PRIMARY KEY:** `id` · **FK:** `actor_platform_user_id → platform.users`
- **RLS:** yok · **Yetkiler:** `persopyro_platform` yalnızca INSERT/SELECT (append-only)
- **Ek:** platform kaynaklı kiracı erişimleri **ayrıca** `hr.platform_access_notices`'a yazılır (kiracıya görünürlük).

## B.3 Mevcut tablolara eklenecekler / değişecekler 🔜

> **Plan dışı bırakma notu (2026-09-12):** aşağıdaki tablo **mevcut tabloların**
> değişikliklerini listeler. **Yeni modüllerin tabloları** (fazla mesai, ücret geçmişi, bordro
> hazırlığı, ülke kural motoru, bildirim/outbox, raporlama, istihdam koşulları/sözleşme/görev
> listeleri) bu dokümanda **henüz tanımlanmamıştır**; tasarımları `docs/HR_ROADMAP.md`
> §3–§16'da planlanmıştır ve **uygulandıkları turda bu dokümana işlenir**. Vardiya/devam
> (M15) ve evrak (M16) tabloları ise **zaten bu dokümanda tasarlıdır** (bkz. ilgili bölümler).

| Tablo | Değişiklik | Gerekçe |
|---|---|---|
| `users` | `public_id varchar(32) NOT NULL` + **global benzersiz** indeks + üretim fonksiyonu (Crockford Base32, 8 karakter, `gen_random_bytes`) | OD2 (kapalı): genel kimlik |
| `users` | `deleted_at` ile birlikte `(tenant_id, email)` benzersizliğinin **kısmi** olması (`WHERE deleted_at IS NULL`) | H.4 / OD21 |
| `roles` | `scope_type varchar(16) NOT NULL DEFAULT 'TENANT'` (`TENANT`/`ORG_UNIT`/`SELF`/`ASSIGNED`) | Kapsam modeli (AUTHORIZATION 4.4) |
| `roles` | `(tenant_id, code)` benzersizliği kısmi (`WHERE deleted_at IS NULL`) | OD21 |
| `user_roles` | `scope_org_unit_id uuid NULL → departments.id` + guard tetikleyicisi + kısmi benzersizlik | Kapsam daraltma (AUTHORIZATION 4.4) |
| `user_roles` | `(user_id, role_id)` benzersizliği kısmi | OD21 |
| `role_permissions` | `(role_id, permission_id)` benzersizliği zaten var; **ek değişiklik yok** | — |
| `audit_logs` | `actor_kind` (`tenant_user`/`platform_user`/`service`/`system`, NOT NULL), `platform_actor_id` (FK'sız), `actor_label` (snapshot), `access_grant_ref` (FK'sız), `purpose`, `prev_hash`/`entry_hash` (zincirleme) 🔜. **`user` FK → `PROTECT`** ✅ **uygulandı** (`audit/0002`, F9 kapandı) | OD23 (kapalı), K22, OD4; zincirleme (OD13) |
| `permissions` | `platform.*` kodları eklenecek (modül `platform`); **yazma yetkisi geri alınacak** | Katalog genişletmesi + en az yetki |
| `tenants` | **`public_id_prefix varchar(20) NOT NULL UNIQUE`** (immutable; değişmezlik tetikleyicisi) — RLS politikası aynı kalır | OD25 (kapalı): public ID ön eki sabit |
| `api_tokens` | **Genişletildi ✅ (OD26 — kapalı, uygulandı):** `token_kind`, `purpose` NOT NULL, `scopes text[]` (katalog doğrulamalı, `platform.*` yasak), `revoked_at` + `revoked_reason`; değişmezlik ve tek yönlü iptal tetikleyicileri. **Yeni token sistemi kurulmaz** (`accounts/0003`) | OD26 / bulgu F8 |

## B.4 Kiracı iş tabloları (yeni, `hr` şemasında, RLS zorunlu) — **M10–M13 ✅ UYGULANDI**

Yetkilendirme modelinin ihtiyaç duyduğu minimum küme tasarlanmıştır; bordro,
performans, işe alım, faturalama gibi modüller **bu turun kapsamı dışındadır**
(ileride aynı kurallarla eklenir).

**OD27 (kapalı) sonucu:** v1'de **tek tüzel kişilik** (kiracı = şirket) vardır;
aşağıdaki tüm tablolar **doğrudan `tenant_id`'ye** bağlıdır ve hiçbirinde
`company_id`/`legal_entity_id` **bulunmaz**. Gelecekte çok tüzel kişilik gerekirse
ayrı bir katman eklenir ve **tenant izolasyon sınırının yerine geçmez** (OD27).

### `hr.departments` ✅ (M10/M11)
- **PURPOSE:** Organizasyon birimleri; `UNIT` kapsamının temeli.
- **PRIMARY KEY:** `id` · **UNIQUE (kısmi, OD21):** `(tenant_id, code) WHERE deleted_at IS NULL`
- **FOREIGN KEYS:** `tenant_id → tenants.id`, `parent_id → departments.id` (self), `manager_employee_id → employees.id` (nullable)
- **Kolonlar (10):** `id, created_at, updated_at, deleted_at, tenant_id, parent_id, manager_employee_id, code, name, status`
- **Doğrulanan tasarım:** `status` kapalı küme `CHECK` (`active`/`inactive`/`archived`); `code`/`name` zorunlu; `parent_id` **adjacency list** (OD24); ağaç kolonu (`path`/closure) **yok**.
- **DB garantileri (M11):** `CHECK (parent_id IS DISTINCT FROM id)`; tetikleyici `trg_departments_tree_guard` — **döngü reddi**, **derinlik ≤ 20**, **silinmiş birimin altına yeni aktif birim bağlanamaz** (OD21).
- **TENANT ISOLATION:** `tenant_id` RLS + `parent_id`/`manager_employee_id` kiracı guard'ı · **PERSONAL DATA LEVEL:** K0
- **RLS:** ✅ + FORCE · **AUDIT:** yapı değişikliği, birim taşıma (`org.unit.move`)
- **İndeksler:** `(tenant_id, parent_id)` (kapsam sorguları), `(tenant_id, status)`

### `hr.positions` ✅ (M10)
- **PURPOSE:** Unvan/kadro tanımı (tenant düzeyinde; **birime bağlı değildir**).
- **PRIMARY KEY:** `id` · **UNIQUE (kısmi):** `(tenant_id, code) WHERE deleted_at IS NULL` · **FK:** `tenant_id`
- **Kolonlar:** `id, created_at, updated_at, deleted_at, tenant_id, code, name, level`
- **RLS:** ✅ + FORCE · **PERSONAL DATA LEVEL:** K0 · **AUDIT:** orta (`org.position.create`)

### `hr.employees` ✅ (M12)
- **PURPOSE:** Çalışanın **iş** kaydı (kimlik verisi değil).
- **PRIMARY KEY:** `id` · **UNIQUE (kısmi):** `(tenant_id, employee_no) WHERE deleted_at IS NULL`
- **FOREIGN KEYS:** `tenant_id`, `user_id → users.id` (nullable), `department_id → departments.id` (**NOT NULL** — OD24 §4), `position_id → positions.id` (nullable)
- **Kolonlar:** `id, created_at, updated_at, deleted_at, tenant_id, user_id, department_id, position_id, employee_no, hire_date, termination_date, status`
- **`employee_no` (sicil numarası):** **sunucu üretir** (`EMP-000001`; kaynak: `hr.employee_no_seq` sequence, migration **0013**). İstemci numara göndermek zorunda değildir/uydurmaz; **birim değişse de numara değişmez**. Bu bir **sicil** numarasıdır, `public_id` **değildir** (OD2/OD25). `nextval` atomiktir → eşzamanlı kayıtlarda çakışma olmaz; işlem geri alınsa da tüketilen sıra değeri geri gelmez (numara boşluğu olabilir, benzersizlik esastır).
- **Doğrulanan tasarım:** `status` kapalı küme `CHECK` (`active`/`suspended`/`terminated`/`archived`); `CHECK (termination_date IS NULL OR termination_date >= hire_date)`; `department_id` **zorunlu**
- **TENANT ISOLATION:** `tenant_id` RLS + `user_id`/`department_id`/`position_id` kiracı guard'ı · **PERSONAL DATA LEVEL:** **K0/K1** — bilinçli olarak PII **içermez**
- **RLS:** ✅ + FORCE · **AUDIT:** yüksek (`employee.create`, `employee.department_change`)
- **İndeksler:** `(tenant_id, department_id)`, `(tenant_id, user_id)`, `(tenant_id, status)`

### `hr.employee_personal_data` (1:1) ✅ (M12)
- **PURPOSE:** Kişisel veriyi ayrı tabloda tutar → maskeleme ve erişim **tablo kapsamında** kontrol edilir.
- **PRIMARY KEY:** `employee_id` (1:1 — ayrı `id` kolonu **yoktur**) · **FK:** `tenant_id`, `employee_id → employees.id` (CASCADE)
- **Kolonlar (K2):** `first_name, last_name, birth_date, national_id, address, personal_email, personal_phone, iban, emergency_contact_phone, emergency_contact_relation, citizenship, work_permit_type, work_permit_valid_until, bank_name` (migration **0010** ile 5 kolon + **0011** ile `bank_name`; `work_permit_valid_until` nullable, diğerleri boş geçilebilir) — `citizenship <> 'TR'` ise `work_permit_type` **zorunlu** (servis kuralı, DB `CHECK` değil) · **(K3, şifreli):** `blood_type` (**0012**; kapalı küme A+/A-/B+/B-/AB+/AB-/0+/0-), `health_notes`, `disability_status`, `biometric_ref` (Fernet; `apps/common/fields.py`) — şifreli kolonlar `text`'tir ve değer üzerinden **arama/eşitlik yapılamaz**
- **TENANT ISOLATION:** `tenant_id` RLS + `employee_id` kiracı guard'ı · **PERSONAL DATA LEVEL:** **K2 + K3**
- **RLS:** ✅ + FORCE · **AUDIT:** **çok yüksek** (okuma dahil; `employee.sensitive.read` + `MFA_OK`/`PURPOSE_GIVEN`)
- **Erişim notu:** organizasyon kapsamı (UNIT) bu tabloya **tek başına erişim hakkı vermez** (OD5 §5.7) — karar `can_access_employee_personal_data()` içindedir.
- **Yetki turu:** app rolü `SELECT/INSERT/UPDATE` alır, **`DELETE` almaz** (OD21 + saklama politikası; `ON DELETE CASCADE` zinciri de böylece runtime'da kurulamaz).

### `hr.employee_manager` (raporlama ilişkisi) ✅ (M13)
- **PURPOSE:** Yönetici–çalışan ilişkisi (geçmişli, çoklu yönetici destekli). **RBAC değildir — yetki üretmez.**
- **PRIMARY KEY:** `id` · **UNIQUE (kısmi):** `(employee_id) WHERE relation_type='primary' AND effective_to IS NULL AND deleted_at IS NULL` (**tek aktif birincil yönetici**) + `(employee_id, manager_employee_id) WHERE effective_to IS NULL AND deleted_at IS NULL`
- **FOREIGN KEYS:** `tenant_id`, `employee_id`, `manager_employee_id`, `created_by_id`
- **Kolonlar:** `id, created_at, updated_at, deleted_at, tenant_id, employee_id, manager_employee_id, relation_type, effective_from, effective_to, created_by_id`
- **Doğrulanan tasarım:** `relation_type` kapalı küme `CHECK` (`primary`/`secondary`/`delegate`); `CHECK (employee_id <> manager_employee_id)`; `CHECK (effective_to IS NULL OR effective_to >= effective_from)`; `effective_from` zorunlu
- **TENANT ISOLATION:** `tenant_id` + 3 guard (+ `trg_employee_manager_active_guard`: **silinmiş çalışan/yönetici ile yeni aktif ilişki kurulamaz**) · **PERSONAL DATA LEVEL:** K0
- **RLS:** ✅ + FORCE · **AUDIT:** yüksek (`employee.primary_manager_set`, `employee.manager.close`, `employee.manager.change`)
- **İndeksler:** `(tenant_id, manager_employee_id)` ("ekibim"), `(tenant_id, employee_id)`

### `hr.workplaces` / `hr.employee_employment_terms` / `hr.employee_assignments` / `hr.form_field_definitions` 🔜 **P1 — karar KAPALI (2026-09-13)**

> **Durum: ✅ UYGULANDI (P1-A, 2026-09-13)** — şema + izin altyapısı + RLS + guard + backfill.
> Migration'lar: `organization/0014_p1a_people_lifecycle` (şema), `0015_p1a_backfill` (veri),
> `0016_rls_p1a` (RLS+FORCE), `0017_p1a_same_tenant_guards`, `0018_p1a_open_ref_guard`;
> `accounts/0006_p1a_people_lifecycle` (`users.auth_version`); `rbac/0012_p1a_permissions_roles`
> (8 izin + sistem rol senkronu).
> **P1-B ✅ UYGULANDI (2026-09-14):** lifecycle servisleri (`apps/organization/lifecycle.py`:
> suspend/restore/terminate/archive/change_manager — state machine + erişim kesme),
> geçmiş servisleri (`change_assignment`, `change_employment_terms`, `employee_history`),
> test UI uçları (`/test-ui/api/employees/<id>/lifecycle/<islem>`, `/assignment`,
> `/employment-terms`, `/manager`, `/history`) ve Çalışan Durumu / Geçmiş bölümleri.
> **P1-C bekliyor:** form konfigürasyon uçları + arayüz, bildirim/e-posta, belge, bordro,
> offboarding. P1.1 (onboarding görev/checklist tabloları, §5) henüz uygulanmadı.

| Tablo | Kolonlar (özet) | Kısıt | RLS |
|---|---|---|---|
| `hr.workplaces` | `id`, `tenant_id`, `code`, `name`, `type` (`office\|branch\|remote\|client_site`), `country_code` (ISO alpha-2, NOT NULL), `city`, `address`, `timezone` (IANA, NOT NULL), `is_active`, audit kolonları | `UNIQUE(tenant_id, code) WHERE deleted_at IS NULL` | ✅ ENABLE+FORCE + `ensure_same_tenant_ref` |
| `hr.employee_employment_terms` | `id`, `tenant_id`, `employee_id`, `effective_from`, `effective_to`, `employment_type`, `working_model`, `workplace_id`, `contract_type`, `contract_start_date`, `contract_end_date`, `probation_end_date`, `reason`, `created_by`, audit | **tek açık satır:** `UNIQUE(employee_id) WHERE effective_to IS NULL AND deleted_at IS NULL`; tarih CHECK'leri | ✅ |
| `hr.employee_assignments` | `id`, `tenant_id`, `employee_id`, `department_id`, `position_id` (null), `effective_from`, `effective_to`, `reason`, `created_by`, audit | **tek açık satır** (aynı desen) | ✅ |
| `hr.form_field_definitions` | `id`, `tenant_id`, `form_code` (`employee_onboarding`), `field_code` (**kod allowlist**), `section_code`, `section_label`, `sort_order`, `is_visible`, `is_required`, `label_override`, `helper_text`, `lock_level` (sunucu hesaplar), `applicability` (JSONB), `created_by`, audit | `UNIQUE(tenant_id, form_code, field_code)`; bilinmeyen `field_code` → 400; A seviyesi gizleme/opsiyonel → 400; K3 `is_required=true` → 400 | ✅ |

**Mevcut tablolarda P1 değişiklikleri:**

| Tablo | Değişiklik | Gerekçe |
|---|---|---|
| `hr.employees` | `+ notice_end_date date NULL` (CHECK: `>= termination_date`) | OD41: ihbar dönemi **türetilmiş** etiket |
| `hr.employees` | `+ termination_reason_code varchar(40) NULL` (kapalı küme: `resignation\|employer_termination\|mutual_agreement\|contract_end\|retirement\|death\|other`), `+ termination_note text NULL` | İşten ayrılış nedeni (item 2); `status='terminated'` ise zorunlu |
| `hr.employees` | **kısmi benzersizlik:** `UNIQUE(user_id) WHERE deleted_at IS NULL AND status <> 'archived'` | OD59: 1 kullanıcı ↔ en fazla 1 aktif çalışan kaydı |
| `hr.users` | `+ auth_version integer NOT NULL DEFAULT 0` | OD59: askıya alma/çıkışta mevcut oturumları düşürme |
| `hr.api_tokens` | `+ auth_version_snapshot integer NULL` (`accounts/0007`) | P1-B (OD59): token üretim anındaki sürüm; uyuşmazsa token 401 (NULL = eski token, kontrol yapılmaz) |

**Kural:** `employees.department_id` / `position_id` **korunur** ("güncel değer" okuması) ve
`hr.employee_assignments` ile **aynı servis yolundan** yazılır (çift kaynak ayrışması test edilir).
`employees` tablosuna ücret (P6), belge (P3) veya ülkeye özgü kolon **eklenmez**.

### `hr.documents` + `hr.document_access`
- **`documents`:** `id, tenant_id, owner_employee_id, category, title, storage_key, checksum_sha256, mime_type, size_bytes, classification (public/internal/confidential/restricted), contains_personal_data (yes/no/unsure), personal_data_categories text[], retention_until, version, uploaded_by_id, created_at, deleted_at`
- **`document_access`:** `id, tenant_id, document_id, grantee_kind (user/role/platform_user), grantee_id, access (read/download), expires_at, granted_by_id, reason`
- **TENANT ISOLATION:** `tenant_id` RLS + guard (`owner_employee_id`, `uploaded_by_id`, `document_id`, `grantee_id` kiracı içi doğrulanır)
- **PERSONAL DATA LEVEL:** **K4** (içerik) · **RLS:** ✅ + FORCE · **AUDIT:** **çok yüksek** (yükleme, görüntüleme, indirme, paylaşım, silme, sınıflandırma)

### `hr.leave_types`, `hr.leave_policies`, `hr.leave_policy_rules`, `hr.leave_balances`, `hr.leave_balance_entries`, `hr.leave_requests`, `hr.approval_steps` ✅ (M14)

- **PURPOSE:** İzin süreci (tür → politika → hakediş/bakiye → talep → onay) ve **genel**
  onay adımları (`ASSIGNED`/`SEPARATE_ACTOR` koşullarının veri kaynağı).
- **`leave_types`:** `id, tenant_id, code, name, category, is_paid, uses_balance,
  requires_approval, requires_document, min_advance_notice_days, max_consecutive_days,
  is_system, status, created_at, updated_at, deleted_at`
  · **kısmi UNIQUE** `(tenant_id, code) WHERE deleted_at IS NULL` (OD21)
  · `status` kapalı küme `CHECK` · **sistem kodları rezerve** (`is_system`; kod değiştirilemez).
- **`leave_policies`:** `accrual_method` (`fixed`/`tenure_based`), `fixed_days`,
  `allow_negative_balance`, `allow_refund_after_approval`, `status` (+ OD21)
  · **kısmi UNIQUE** `(tenant_id, leave_type_id) WHERE deleted_at IS NULL`
  → **tür başına tek yaşayan politika** · `fixed_days` tutarlılığı `CHECK` ile.
- **`leave_policy_rules`:** `min_service_months`, `max_service_months` (NULL = açık uçlu),
  `entitlement_days`; aralıklar **[min, max)** yarı açıktır ve **çakışamaz**
  (`trg_leave_policy_rules_overlap`).
- **`leave_balances`:** `period_year, entitled_days, carried_forward_days, adjustment_days,
  used_days, reserved_days` + **ÜRETİLEN** `available_days`
  (`entitled + carried_forward + adjustment - used - reserved`, STORED)
  · **kısmi UNIQUE** `(employee_id, leave_type_id, period_year) WHERE deleted_at IS NULL`
  · `trg_leave_balances_nonnegative`: politika izin vermiyorsa **negatif bakiye reddedilir**.
- **`leave_balance_entries`:** **append-only** olay defteri — `entry_kind`
  (`accrual`/`carryover`/`adjustment`/`reservation`/`reservation_release`/`usage`/`refund`),
  `delta_days`, `leave_request_id`, `reason`, `actor_id`
  · **idempotans kısıtları:** `(balance_id, entry_kind, period_year) WHERE entry_kind='accrual'`
  ve `(leave_request_id, entry_kind)` (çift tüketim engeli)
  · `adjustment` için **gerekçe zorunlu** (`CHECK`).
- **`leave_requests`:** `employee_id, leave_type_id, start_date, end_date, start_time,
  end_time, is_half_day, days_requested` (**sunucu hesaplar**), `reason`,
  `status` (kapalı küme: `draft`/`pending`/`approved`/`rejected`/`canceled`/`taken`),
  `submitted_at, decided_at, decided_by_id, canceled_at, cancel_reason, document_ref`
  (M16 entegrasyon noktası; FK **yok**) + OD21
  · `trg_leave_requests_state`: **yasak durum geçişleri**, `decided_at`/`canceled_at`
  zorunluluğu ve **belge zorunlu türde belgesiz onay yasağı** (fail-closed).
- **`approval_steps`:** **genel** onay adımı — `subject_kind` + `subject_id` (**FK değil**,
  polimorfik), `step_no`, `approver_user_id` **veya** `approver_role_code`, `status`,
  `decided_at`, `decision_reason`, `delegated_from_id` + OD21
  · **kısmi UNIQUE** `(subject_kind, subject_id, step_no) WHERE deleted_at IS NULL`
  · `trg_approval_steps_order`: **adım sırası atlanamaz**.
- **TENANT ISOLATION:** `tenant_id` RLS + FORCE + kiracı-içi FK guard'ları (14 tetikleyici)
  · **PERSONAL DATA LEVEL:** K2 (izin gerekçesi) — denetim kaydına **yazılmaz**.
- **RLS:** ✅ + FORCE (7 tablo; toplam FORCE RLS tablo sayısı **19**)
- **Yetki turu:** `SELECT/INSERT/UPDATE`; **`DELETE`/`TRUNCATE` yok** (OD21).
  `leave_balance_entries` **append-only**: yalnız `SELECT/INSERT` (UPDATE de yok).
- **AUDIT:** yüksek — `leave.type.*`, `leave.policy.*`, `leave.request.create/submit/approve/
  reject/cancel/override/taken`, `leave.balance.accrual/adjustment` (serbest metin gerekçe
  audit'e yazılmaz; `has_reason` bayrağı tutulur).

### `hr.shifts`, `hr.shift_assignments`, `hr.attendance_records`
- **PURPOSE:** Vardiya ve devamsızlık; düzeltme onayı.
- **TENANT ISOLATION:** `tenant_id` RLS + guard · **PERSONAL DATA LEVEL:** K1/K2 (devamsızlık gerekçesi)
- **RLS:** ✅ + FORCE · **AUDIT:** yüksek (özellikle devam düzeltmesi)

### `hr.access_requests` / `hr.access_grants` (kiracı içi)
- **PURPOSE:** Kiracı içi kişisel veri erişimi (talep → onay → süreli izin). OD4 kararı.
- **`access_requests`:** `id, tenant_id, requested_by_id → users, subject_employee_id, field_list text[], purpose text NOT NULL, status, created_at, decided_at, decided_by_id, decision_reason`
- **`access_grants`:** `id, tenant_id, request_id, granted_to_id → users, field_list text[], starts_at, expires_at NOT NULL, revoked_at, use_count, last_used_at`
- **TENANT ISOLATION:** `tenant_id` RLS + guard (`requested_by_id`, `decided_by_id`, `granted_to_id`, `subject_employee_id`)
- **PERSONAL DATA LEVEL:** K1 (talep gerekçesi K2 içerebilir ⚠️) · **RLS:** ✅ + FORCE
- **AUDIT:** **çok yüksek** — 5 olay (talep, onay, red, başlangıç, bitiş)

### `hr.platform_access_notices` (kiracıya görünürlük)
- **PURPOSE:** Platform erişimlerinin kiracıya görünür yansısı (şeffaflık; OD15/OD18).
- **Kolonlar:** `id, tenant_id, platform_actor_public_id, action (request/approve/access_start/access_end/reject), resource_summary, purpose, grant_id, occurred_at`
- **TENANT ISOLATION:** `tenant_id` RLS · **RLS:** ✅ + FORCE · **Yetkiler:** kiracı tarafı **yalnızca SELECT**; yazma yalnızca platform servisi (kiracı bağlamı kurarak)
- **PERSONAL DATA LEVEL:** K1 (kişisel veri değeri taşımaz — yalnızca genel kimlik + amaç)

## B.5 İlişki haritası (hedef)

```
platform.users ──< platform.user_roles >── platform.roles ──< platform.role_permissions >── hr.permissions
       │                                              │
       │                                              └── (katalog paylaşılır, salt okunur)
       ├──< platform.tokens
       ├──< platform.access_requests >──< platform.access_grants
       └──  platform.audit_logs

hr.tenants ──< hr.tenant_directory            (yansı, giriş)
     │      ──< platform.tenant_registry      (yansı, platform — OD20 kapalı)
     ├──< hr.users ──< hr.user_roles >── hr.roles ──< hr.role_permissions >── hr.permissions
     │        │            └── scope_org_unit_id ──┐
     │        ├──< hr.api_tokens                   │
     │        └──< hr.user_permission_overrides    │
     ├──< hr.departments ──(parent_id, self)───────┘
     │        └──< hr.positions
     ├──< hr.employees ──< hr.employee_personal_data
     │        ├──< hr.employee_manager (manager_employee_id, self)
     │        ├──< hr.leave_requests ──< hr.approval_steps
     │        ├──< hr.shift_assignments / hr.attendance_records
     │        └──< hr.documents ──< hr.document_access
     ├──< hr.access_requests ──< hr.access_grants       (kiracı içi kişisel veri erişimi)
     ├──< hr.platform_access_notices                    (platform erişiminin kiracıya yansısı)
     └──< hr.audit_logs
```

**Kiracıya bağlı her yeni tablo** (yukarıdaki `hr.*` iş tabloları) şu üçünü
birlikte taşımak zorundadır: `tenant_id NOT NULL FK`, RLS + **FORCE**,
`tenant_id` dışındaki her kiracı-içi FK için **guard tetikleyicisi**.

---

# C) MIGRATION PLAN

Her iş kalemi **tek bir Django migration** (veya `RunSQL` bloğu) olarak yazılır;
**mevcut migration dosyaları düzenlenmez**. Sıra bağımlılığa göredir.

| # | İş | Şema/Şema dışı | Bağımlılık | Geri alma |
|---|---|---|---|---|
| **M1** | `platform` şeması + `persopyro_platform` rolü + yetkiler (betik: `scripts/03_platform_roles.sql`) — **✅ UYGULANDI (2026-09-11):** şema migration ile (fresh install'da tek başına çalışır), rol/yetkiler süper kullanıcı betiğiyle (`CREATE ROLE` süper kullanıcı gerektirir; `persopyro_owner` `NOCREATEROLE`). Rol: `NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS`; `PUBLIC`'in şemada yetkisi yok; `persopyro_app` şemayı **göremez**; yetki turu **tablo bazlı allowlist** (geniş `ALL TABLES` ve `ALTER DEFAULT PRIVILEGES` **bilinçli olarak yok**) | DDL (sahip+süper) | — | `DROP SCHEMA platform CASCADE` + `DROP ROLE` |
| **M2** | `platform.users`, `platform.roles`, `platform.role_permissions`, `platform.user_roles` + **`platform.users.public_id`** (rezerve `plt` ön ekiyle üretim; kiracı kullanıcılarından ayrı ad alanı — OD28 kapalı) — **✅ UYGULANDI** (`apps/platform/migrations/0001_od3_platform_users_roles.py`): `tenant_id` kolonu **yok**; `is_staff`/`is_superuser` kolonu **yok**; rol kodları kapalı küme (`CHECK code IN ('superadmin','platform_admin')`); rol/permission FK'leri `platform.roles` + `hr.permissions` (kiracı RBAC'ine bağlanmaz); `user_roles.expires_at` opsiyonel ama `> granted_at` | Yeni | M1, M5 (yardımcı fonksiyonlar; sıra bağımlılığı) | DROP TABLE (yeni, veri kaybı yalnızca platform hesabı) |
| **M3** | `platform.tokens` (OD22 kararı sonrası) — **✅ UYGULANDI** (`0002_od22_platform_tokens.py`): ham token yok (SHA-256, UNIQUE); `purpose` ≥10 karakter; `scope` NOT NULL ve **boş olamaz** (`cardinality >= 1`), joker `*` yasak; `token_kind` kapalı küme; `expires_at` NOT NULL **ve TTL tavanı DB CHECK'iyle** (`interactive` ≤ 24 saat, `service` ≤ 90 gün); iptal tek yönlü; kapsam/purpose/süre/tür/hash/owner değişmez; **service token superadmin izin kümesiyle üretilemez** (tetikleyici). Servis katmanı: `apps/platform/services.py` (§ OD22 TTL/kapsam kuralları) | Yeni | M2 | DROP TABLE |
| **M4** | `platform.tenant_registry` + `hr.tenants` üzerine yansı tetikleyicisi (SECURITY DEFINER) + backfill — **✅ UYGULANDI** (`0003_od20_tenant_registry.py`): yalnızca minimum alanlar (PII **yok**: `name/legal_name/tax_number/email/phone/address/settings` kopyalanmaz); tek yönlü `hr.tenants` → registry, `platform.sync_tenant_registry()` `SECURITY DEFINER` + **sabit `search_path = pg_catalog, pg_temp`** + tam nitelemeli SQL; `plan_code`/`billing_ref`/`internal_note` yalnızca platformun (yansı onları ezmez); yansı yazımı başarısızsa kaynak yazımı **geri alınır** (test: kasten çakıştırılmış registry slug'ı → kiracı INSERT'i tümden rollback) | Yeni + tetikleyici | M1 | Tetikleyici + tablo DROP (hr.tenants'a dokunulmaz) |
| **M5** | `hr.tenants.public_id_prefix` (nullable ekle → slug'tan türet/çakışmada sonek → `NOT NULL` + tam `UNIQUE` + değişmezlik tetikleyicisi) **ve** `hr.users.public_id` (kiracının prefix'inden üretim → `NOT NULL` + global benzersiz indeks). **Ayrıca (OD28 tamamlayıcı işler):** üretim fonksiyonları (`hr.generate_public_id_prefix(slug)`, `hr.generate_public_id(tenant_id)`; çakışmada yeniden dener), format `CHECK` (`^[a-z0-9]{3,20}-[0-9A-HJKMNP-TV-Z]{8}$`), **rezerve ad listesi** (slug/prefix olarak reddedilir: `www, api, app, admin, portal, mail, support, hr, platform, plt, system`) — **✅ UYGULANDI** (`tenancy/0008` yardımcılar + `tenancy/0009` prefix + `accounts/0005` public ID). Ölçüm (canlı DB): kiracı prefiksleri `demo`, `askida`; kullanıcı `public_id` örnekleri `demo-3A8EAQ9B`, `askida-1CY29715` (Crockford, `I/L/O/U` yok); `public_id'siz` kullanıcı **0**. Kalıcı rezerve: prefix **tam UNIQUE** (soft-delete'te geri alınmaz); değişmezlik iki tetikleyiciyle (`tenants_public_id_prefix_guard`, `users_public_id_guard`); üretim tetikleyicilerle (`..._assign`) — kiracı bağlamı yoksa `NULL` → `NOT NULL` ihlali (fail-closed, sessiz üretim yok). NOT: `hr.generate_public_id`'nin çakışma denemesi **RLS görünürlüğüyle** sınırlıdır; global tekillik garantisi `users_public_id_uniq_idx` UNIQUE indeksidir (kalan çakışma olasılığı ~32⁻⁸) | Şema + veri + fonksiyon | — | İndeks/kolon/tetikleyici/fonksiyon DROP |
| **M6** | `hr.permissions`: `platform.*` kodları (yeni migration, frozen liste) + `REVOKE INSERT/UPDATE/DELETE ON hr.permissions FROM persopyro_app` — **✅ UYGULANDI (2026-09-11)**: altı kod eklendi (`platform.tenant.read`, `platform.tenant.manage`, `platform.settings.manage`, `platform.role.assign`, `platform.audit.read`, `platform.support.manage`) → katalog 29 → **35**; kodlar sabit/benzersiz/jokersiz ve **kiracı şablon rollerine girmez** (`PLATFORM_PERMISSIONS` ayrı liste; `ALL_PERMISSION_CODES` değişmedi). ÖLÇÜM (öncesi/sonrası): app rolü `hr.permissions` üzerinde SELECT/INSERT/UPDATE/DELETE → `t/t/t/t` **iken** artık `t/f/f/f`; aynı daraltma `scripts/02_grant_app_privileges.sql` içinde de vardır (betik yetkiyi geri açmaz — regresyon testi) | Veri + yetki | — | Kodlar silinebilir; yetki geri verilebilir (⚠️ istenmez) |
| **M7** | Rol kodu göçü: `manager → dept_manager` (kiracı başına, veri) + `tenant_officer` şablon rolünün oluşturulması — **✅ UYGULANDI (2026-09-11)** (`rbac/0005_od5_rol_katalogu_gocu`): göç **yerinde** yapılır — rolün **kimliği, atamaları ve izin bağları korunur** (yeni rol üretilmez), izin seti §6.2 **DY** kolonuna **explicit** getirilir (ölçüm: `+7/-0` → **7→14 izin**); `tenant_officer` §6.2 **ŞYT** setiyle oluşturulur (**19 izin**, `◐ ❓OD9` işaretli kodlar fail-closed olarak **alınmaz**). **Fail-closed kararlar:** (a) aynı kiracıda `manager`+`dept_manager` birlikte varsa migration **açık hata** verir (sessiz birleştirme yok), (b) soft-delete edilmiş `manager` **sessizce canlandırılmaz** (adı değiştirilmez, yeni rol üretilmez), (c) hedef sette olmayan eski izinler **çıkarılır** (kalıntı yetki yok), (d) katalogda olmayan izin kodu → **açık hata**. İdempotent; çıktı olarak kiracı başına rol/rol-izin sayıları **raporlanır** | Veri | M6, M8 | Yerinde ters adlandırma (atama varsa `tenant_officer` silinmez → açık hata) |
| **M8** | `hr.roles.scope_type` (OD5 ✅): kapalı küme `TENANT`, `UNIT`, `UNIT_DIRECT`, `ASSIGNED`, `SELF` (yeni kod yok; **`SYS` hariç** — yalnız platform rolleri) — **✅ UYGULANDI (2026-09-11)** (`rbac/0006_od5_role_scope_type`): kolon **NOT NULL** + DB `CHECK` (`roles_scope_type_valid`); backfill doküman eşlemesiyle: `tenant_admin`/`tenant_officer`/`hr_manager` = `TENANT`, `dept_manager` = `UNIT`, `employee` = `SELF`, bilinen kod dışı **custom** roller = `TENANT` (§4.1) ve **raporlanır**. `scope_type` rolün **azami** kapsamıdır; izin **üretmez** (karar `izin ∧ kapsam`) | Şema | — | Kolon + kısıt düşürülür |
| **M9** | `hr.user_roles.scope_org_unit_id` + **guard tetikleyicisi** + kısmi benzersizlikler — **✅ KISMİ UYGULANDI (2026-09-11)** (`rbac/0007_od5_user_role_scope`; `M10` bağımlılığı nedeniyle bilinçli bölme): **uygulanan** (a) kolon (uuid, nullable), (b) `UNIQUE NULLS NOT DISTINCT (user_id, role_id, scope_org_unit_id)` → kapsamsız rollerde çift atama engellenir, aynı rol farklı birimlere atanabilir, (c) `trg_user_role_scope_guard`: `scope_type ∈ {UNIT, UNIT_DIRECT}` iken kapsam **zorunlu** ("NULL = tüm tenant" **değildir**), diğer tiplerde kapsam **NULL olmalı**, rol kiracı bağlamında görünmüyorsa yazım **reddedilir**. **Ertelenen (M10 sonrası):** `departments(id)` **FK'si**, "kapsam aynı kiracının **aktif** birimi olmalı" guard'ı ve **alt ağaç çözümlemesi** (sahte FK/tablo üretilmedi). **Fail-closed köprü:** `apps/rbac/services/scope.py` — M10 öncesi `UNIT`/`UNIT_DIRECT` atamaları **izin üretmez** (aksi hâlde birim yöneticisi izinleri tenant-wide görünürdü) | Şema + tetikleyici | M10 (departments) | Kolon/tetikleyici DROP |
| **M10** | `hr.departments` + `hr.positions` + RLS + FORCE + guard'lar + indeksler. **OD27 kapalı: `company_id`/`legal_entity_id` EKLENMEZ** — tablolar doğrudan `tenant_id`'ye bağlıdır — **✅ UYGULANDI (2026-09-11)** (`organization/0001_od24_departments_positions`): `departments` (`code`/`name`/`status` + `parent_id` adjacency list), `positions` (`code`/`name`/`level`; birime bağlı **değil**), kısmi benzersizlikler (`WHERE deleted_at IS NULL`), `(tenant_id, parent_id)` indeksi, `status` kapalı küme `CHECK` (`organization/0009`); RLS + FORCE + `tenant_isolation` politikası (`0006_rls_organization`), kiracı-içi FK guard'ları (`0007`) | Yeni | M1 (şema zaten var) | DROP TABLE (veri kaybı kabul, yeni modül) |
| **M11** | **Organizasyon ağacı garantileri (OD24 kapalı):** `CHECK (parent_id IS DISTINCT FROM id)`, `BEFORE INSERT/UPDATE` **döngü reddi** tetikleyicisi (ata zinciri yürüyüşü, derinlik sınırı 20), `employee_manager` için `CHECK (employee_id <> manager_employee_id)` + aynı tetikleyici deseni, `relation_type='primary'` kısmi benzersizliği. **`path`/closure kolonu EKLENMEZ** — **✅ UYGULANDI** (`organization/0002_od24_tree_guards` + `0008_employee_manager_active_guard`): `hr.departments_tree_guard()` döngüyü, **derinliği (≤ 20)** ve **silinmiş ata** altına yeni aktif birim bağlanmasını reddeder; `hr.employee_manager_active_guard()` silinmiş çalışan/yöneticiyle yeni aktif ilişkiyi engeller; `employee_manager` kısmi benzersizlikleri `0004`'te | Şema + tetikleyici | M10 | Tetikleyici/kısıt DROP |
| **M12** | `hr.employees` + `hr.employee_personal_data` (K3 şifreli kolonlar) + RLS + guard'lar. **OD27 kapalı: `company_id` EKLENMEZ** — **✅ UYGULANDI** (`organization/0003_od24_employees`): `employees` = **iş kaydı, PII içermez**; `department_id` **NOT NULL** (OD24 §4), `user_id`/`position_id` nullable, kısmi `(tenant_id, employee_no)` benzersizliği, `status` kapalı küme, `termination_date >= hire_date`; `employee_personal_data` **PK `employee_id` (1:1)**, K2 açık + **K3 Fernet şifreli** (`apps/common/fields.py`) | Yeni | M10 | DROP TABLE |
| **M13** | `hr.employee_manager` + RLS + guard + kısmi benzersizlik — **✅ UYGULANDI** (`organization/0004_od24_employee_manager`): `relation_type` (`primary`/`secondary`/`delegate`) kapalı küme, `effective_from`/`effective_to` (tarih aralığı `CHECK`), `created_by_id`; **tek aktif birincil yönetici** kısmi benzersiz indeksi, `(employee_id, manager_employee_id)` kısmi benzersizliği, self-manager `CHECK`'i, kiracı guard'ları + **silinmiş çalışanla yeni aktif ilişki yasağı**. **RBAC değildir — yetki üretmez** | Yeni | M12 | DROP TABLE |
| **M14** | `hr.leave_*`, `hr.approval_steps` + RLS + guard | Yeni | M12 | DROP TABLE | **✅ UYGULANDI 2026-09-11** — yedi tablo (`leave_types`, `leave_policies`, `leave_policy_rules`, `leave_balances`, `leave_balance_entries`, `leave_requests`, `approval_steps`) + RLS/FORCE (12 → **19**) + 4 DB guard'ı + 14 kiracı guard'ı + izin kataloğu delta'sı (`rbac/0011`) · migration'lar: `leave/0001…0004` |
| **M15** | `hr.shifts`, `hr.shift_assignments`, `hr.attendance_records` + RLS + guard | Yeni | M12 | DROP TABLE |
| **M16** | `hr.documents` + `hr.document_access` + sınıflandırma/kişisel veri işaretleri + RLS + guard | Yeni | M12 | DROP TABLE |
| **M17** | `hr.access_requests` + `hr.access_grants` (kiracı içi) + RLS + guard + `PURPOSE_QUALITY` CHECK | Yeni | M12 | DROP TABLE |
| **M18** | `platform.access_requests` + `platform.access_grants` + onay kayıtları (çift onay) | Yeni | M2 | DROP TABLE |
| **M19** | `hr.platform_access_notices` + platform servisinin kiracı bağlamıyla yazma yolu | Yeni | M18 | DROP TABLE |
| **M20** | `hr.audit_logs` genişletme (OD23): `actor_kind` (NOT NULL, default `tenant_user`), `platform_actor_id`, `actor_label`, `access_grant_ref`, `purpose` + `CHECK (actor_kind <> 'platform_user' OR (purpose IS NOT NULL AND access_grant_ref IS NOT NULL))` + **`user_id` FK davranışı `PROTECT` ✅ uygulandı** (`audit/0002`, F9 kapandı; kalan kolonlar OD23 iş kalemi) (+ zincirleme alanları OD13 kararına bağlı) | Şema | M19 | Kolonlar/kısıt DROP |
| **M21** | `hr.tenant_directory`'nin `platform.tenant_registry` ile tutarlılığı için test + (OD20 kararı) birleştirme | Test/kod | M4 | — |
| **M22** | **`/admin/` yolunun kaldırılması + `django.contrib.admin`'in uygulama listesinden çıkarılması** (OD19 kapalı) — **✅ UYGULANDI** (ölçüm: `GET /admin/` ve `/admin/login/` → **404**; uygulama kayıtlı değil) | Kod + ayar | — | Route/uygulama geri eklenebilir |
| **M22b** | `auth_group`, `auth_group_permissions`, `auth_permission`, `django_admin_log` tablolarında `persopyro_app` yetkilerinin **tamamen geri alınması** (`REVOKE ALL`) — **✅ UYGULANDI** (migration `accounts/0004` + `scripts/02_grant_app_privileges.sql`; ölçüm: dördünde SELECT/INSERT/UPDATE/DELETE/TRUNCATE/REFERENCES/TRIGGER → **hepsi `false`**) | Yetki | M22 | Yetki geri verilebilir (istenmez) |
| **M22c** | `create_superuser`'ın `is_staff=True` atamaması + üretimde `is_staff = true` olan **aktif kullanıcı bulunmaması** için denetim sorgusu ve test — **✅ UYGULANDI**: `create_superuser` açık hata verir (Django Admin erişimi üretmez), `User.save` guard'ı + `users_is_staff_disabled` DB kısıtı, denetim komutu `audit_is_staff`; ölçüm (canlı DB): `is_staff=True` kayıt **0** | Kod + test | M22b | Kod geri alınır |
| **M22d** | Kaldırma sonrası **sıfırdan kurulum testi** (boş DB → migrate → doğrulama): admin çıkarılınca `django_admin_log` oluşmaz ve kurulum farkı gözlemlenir — **✅ UYGULANDI**: geçici DB'de sıfırdan kurulumda `django_admin_log` **hiç oluşmadı** (admin migration kaydı 0), yetkiler kapalı, tam suite yeşil | Test | M22c | — |
| **M23** | Test genişletmesi: RLS yapısal testine yeni tablolar; T15–T18 davranış testleri; guard testleri | Test | M10–M20 | — |
| **M24** | İndeks turu (bölüm E) + `ANALYZE` | Şema | M10–M20 | İndeks DROP |
| **M25** | **Soft-delete yönetici standardı** (OD21): ortak temelde `objects` = silinmişi **gizler**, `all_objects` = tümü; mevcut modellere (User, Tenant, Role, UserRole, UserPermissionOverride) uygulanır · **DURUM: uygulanmadı — F7 için gerekli değil** (yetki filtreleri sorgularda açık ve test edilmiş; bu adım ergonomi) | Kod (şema değişmez) | — | Yönetici tanımı geri alınır |
| **M26** | **Yetki çözümlemesinde `deleted_at IS NULL`** (`UserRole`, `Role`, `UserPermissionOverride`) ✅ **UYGULANDI (2026-09-11) — F7 KAPANDI**; kalan: `*.restore` izni + silme/geri alma denetim kaydı (silme özelliği yazıldığında). NOT: `RolePermission` bağ tablosudur, `deleted_at` kolonu **yoktur** (ölçüm düzeltmesi) | Kod + test | — (M25'e bağımlı değil) | Kod geri alınır (bulgu yeniden açılır) |
| **M27** | `users` e-posta benzersizliğinin **kısmi** hale getirilmesi (`users_tenant_email_uniq` tam indeksi düşürülür, `WHERE deleted_at IS NULL` kısmi kurulur); `tenants` slug/domain **kalıcı rezerve** istisnası olarak korunur | Şema | M5 | Tam indeks geri kurulur |
| **M28** | Kodlayan: `create_default_roles` içindeki `get_or_create` davranışının silinmiş rolü **canlandırmaması** ✅ **UYGULANDI (2026-09-11):** açık sorgu + ön kontrol; silinmiş rol/izin bağı bulunursa `SoftDeletedDefaultRoleError` (kopya üretilmez, otomatik restore yok) | Kod + test | — | — |
| **M29** | **Tenant API token modeli (OD26 — kapalı):** `hr.api_tokens`'a `token_kind` (CHECK `interactive`/`service`), `purpose` NOT NULL (`length(btrim())>=10`), `scopes text[]` NOT NULL (boş kapsam yalnızca iptal edilmiş satırda; wildcard yasak), `revoked_at` + `revoked_reason` (kapalı küme, ikisi birlikte NULL/NOT NULL). **İki tetikleyici:** (a) kapsam doğrulama — her değer `hr.permissions.code` içinde olmalı, `platform.*` yasak, tekrar yok; (b) değişmezlik — `scopes`/`purpose`/`expires_at`/`token_kind`/`token_hash`/`tenant_id`/`user_id` değişemez, `revoked_at` NULL'a dönemez (tek yönlü iptal) — **✅ UYGULANDI** (`apps/accounts/migrations/0003_apitoken_od26.py`; canlı DB'de doğrulandı) | Şema | — | Kolon/CHECK/tetikleyici DROP |
| **M30** | **`api_tokens` yetki turu ve erişim yolu:** uygulama rolü `api_tokens` üzerinde UPDATE **yalnızca** `last_used_at` (ve iptal akışı için `revoked_at`/`revoked_reason`); `delete()` yolu kaldırılır (`TokenLogoutView` → iptal). RLS dışı kalma gerekçesi A.5'te sabit — **✅ UYGULANDI** (migration + `scripts/02_grant_app_privileges.sql`; ölçüm: tablo UPDATE/DELETE `false`, kolon UPDATE üç alan için `true`) | Yetki + kod | M29 | Yetki/kod geri alınır |

| **M31** | **Platform default rolleri ve izin bağları (OD3/§6.2A; "M7" olarak istenen platform işi):** `platform.roles` (`superadmin`, `platform_admin`) + izin bağları — **✅ UYGULANDI (2026-09-11)** (`platform/0004_od3_platform_soft_delete` + `platform/0005_od3_platform_rolleri_ve_izinleri`): `roles`/`role_permissions`/`user_roles` tablolarına **`deleted_at`** eklendi (K25 standardı; silinen kayıt yetki vermez); seed **idempotent** (UNIQUE + var olan bağ atlanır → çift kayıt yok); soft-delete edilmiş rol/bağ bulunursa **sessiz restore yok** (migration açık hata verir); kod eksikse sessiz geçilmez. **İzin devralma YOK** — her rolün kümesi açık bağdır: `superadmin` **6/6**, `platform_admin` **4/4** (`settings.manage` ve `role.assign` yok). Ölçüm: `role_permissions` = **10 satır**; `platform.users` = **0** (gerçek kullanıcı seed edilmez) | Şema + veri | M6 | Bağlar silinir; kolon DROP |
| **M32** | **Platform authorization servisi (OD3/§4.3; "M8" olarak istenen platform işi):** tek merkezi çözümleyici `apps/platform/permissions.py` — **✅ UYGULANDI (2026-09-11)**: etkin izin kümesi tek sorguyla hesaplanır (aktif kullanıcı + aktif/süresi geçmemiş atama + aktif rol + aktif rol-izin bağı; **soft-delete edilen hiçbir kayıt yetki vermez**); **izin devralma yok**; Django `auth_permission`/`is_staff` **kullanılmaz**; tenant RBAC kod yoluna **düşülmez** (ortak katalogda kiracı izni bağlansa bile `platform.*` süzgeci nedeniyle yetki üretmez); fail-closed (karar verilemezse izin yok). **Self-elevation koruması:** kullanıcı kendi rolünü atayamaz/değiştiremez; `platform.role.assign` gerekir (yalnız superadmin); aktör atayacağı rolün **tüm** izinlerine sahip olmalı (sahip olmadığı yetki dağıtılamaz) → `platform_admin` kendini/kimseyi superadmin yapamaz; yönetim hiyerarşisi (üst rol alt rolü atar) kontrol edilir; atama `granted_by_id` ile kayıtlanır, kaldırma **soft-delete**tir (denetim izi kalır). Token kapısı: **pasif/silinmiş platform kullanıcısının token'ı çalışmaz** (`authenticate_platform_token`) | Kod + test | M31 | Kod geri alınır |
| **M33** | **İlk platform SUPERADMIN bootstrap komutu (OD3; "M9" olarak istenen platform işi):** `manage.py create_platform_superadmin` — **✅ UYGULANDI (2026-09-11)**: Django Admin kullanılmaz; **gerçek kullanıcı migration'a seed edilmez**; parola yalnızca `--password-stdin` veya etkileşimli `getpass` ile alınır (`--password` seçeneği **yok** → komut satırına/süreç listesine sızmaz); parola **hash'lenerek** (Argon2id) saklanır, **ham parola loglanmaz**; **varsayılan parola yok**, parola politikası (Django doğrulayıcıları) dayatılır; e-posta zaten varsa (aktif/pasif/**soft-delete**) komut **açık hata** verir ve mevcut kullanıcı **sessizce yükseltilmez**; atama `granted_by_id = NULL` + **zorunlu gerekçe** (≥10 karakter) ile kayıtlanır; platform şemasına erişemeyen rolle çalıştırılırsa fail-closed hata verir | Kod + test | M32 | Kod geri alınır (kullanıcı kaydı ayrıca silinir) |

**Numaralandırma notu (2026-09-11, ölçülmüş):** Bu tablodaki **M7–M9 kiracı RBAC
işleridir** (rol kodu göçü, `hr.roles.scope_type`, `hr.user_roles.scope_org_unit_id`)
ve **M9 `M10`'a (organizasyon/departments) bağlıdır** → organizasyon modülü
kapsam dışı olduğu için **o turda uygulanmadılar**. "Platform authorization paketi"
olarak istenen işler bu tabloda **M31–M33** satırlarıdır (M6 zaten platform izin
kataloğudur). Numaralar çakışmasın diye yeni satırlar M31+'a eklendi; M7–M9'un
tanımı/anlamı **değiştirilmedi**.

**M7–M9 uygulama durumu (2026-09-11, ikinci tur):** **M7 ✅ tam**, **M8 ✅ tam**,
**M9 ✅ kısmi** uygulandı (`rbac/0005`, `0006`, `0007`). M9'un organizasyon
tablosuna bağlı parçaları — `departments(id)` **FK'si**, "aktif birim" guard'ı ve
**alt ağaç çözümlemesi** — **M10 sonrasına bırakıldı**; sahte FK/kolon üretilmedi.
Bu boşluk güvenlik açısından **fail-closed** kapatıldı: `apps/rbac/services/scope.py`
kapsamı çözemediği sürece `UNIT`/`UNIT_DIRECT` atamaları **izin üretmez**, dolayısıyla
bir `dept_manager` izni M10 gelene kadar **kiracı geneli yetkiye dönüşemez**.
Kapsamın **iki görünümü** ayrıldı: `declared_permission_codes()` (rol sahipliği —
yönetim/teşhis) ve `effective_permission_codes()` (kapsama göre **kullanılabilir** —
yetkilendirme kaynağı).

**Uygulama sırası önerisi (3 parti):**
1. **Parti 1 (platform temeli):** M1–M5, M22 — kiracı verisine dokunmayan, geri
   alması kolay işler. — **M1–M5 ✅ UYGULANDI (2026-09-11)**, M22(M22–M22d) ✅
   UYGULANDI, **M6 ✅ + M31–M33 ✅ UYGULANDI (2026-09-11)**. **Sıra notu (ölçülmüş):**
   M2 migration'ı `hr.crockford_suffix`
   yardımcısını kullandığı için **M5'in yardımcı fonksiyonları M2'den önce**
   çalışır (`tenancy/0008` → `platform/0001`); M5'in kolon işleri iki uygulamaya
   dağıldığı için `tenancy/0009` (prefix) + `accounts/0005` (public_id) olarak
   bölündü. M1'in rol adımı (`scripts/03_platform_roles.sql`) **süper kullanıcı**
   ile çalıştırılır; migration tarafı şemayı ayrıca garanti eder.
2. **Parti 2 (kimlik/rol/kapsam):** M6–M9, M17, M20 — mevcut tablolara ekleme +
   veri göçü (küçük hacim).
3. **Parti 3 (iş tabloları):** M10–M16, M18–M19, M21, M23–M24.

**Parti 2 ve sonrası için zorunlu ön adım:** parola/yetki verisi içeren tablolar
üzerinde çalışıldığı için **şifreli yedek alınır** ve geri yükleme denenir
(geri yükleme testi yapılmadan bu partiler başlatılmaz).

---

# D) RLS PLAN

## D.1 Değişmeyenler (kesinleşmiş karar)

- Mevcut 7 tablonun politikası **aynen kalır** (`tenant_id = hr.current_tenant_id()`,
  `tenants` için `id = ...`). Platform için politika **gevşetilmez**; `BYPASSRLS`
  **kullanılmaz** (OD1/OD3 kararı).
- `FORCE ROW LEVEL SECURITY` tüm kiracı tablolarında **zorunludur**; yeni tablo
  FORCE'suz eklenirse yapısal test kırmızıya döner.
- Kiracıya bağlı her yeni tablo için `tenant_id` **NOT NULL** + FK zorunludur.

## D.2 Yeni kiracı tabloları için politika kalıbı

```sql
-- Her yeni hr.* iş tablosu için (migration içinde, tablo oluşturulduktan sonra):
ALTER TABLE hr.<tablo> ENABLE ROW LEVEL SECURITY;
ALTER TABLE hr.<tablo> FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON hr.<tablo>
    USING (tenant_id = hr.current_tenant_id())
    WITH CHECK (tenant_id = hr.current_tenant_id());
```

## D.3 Yeni guard tetikleyicileri (kiracılar arası referans yasağı)

| Tablo | Sütun(lar) |
|---|---|
| `departments` | `parent_id → departments`, `manager_employee_id → employees` |
| `employees` | `user_id → users`, `department_id → departments`, `position_id → positions` |
| `employee_personal_data` | `employee_id → employees` |
| `employee_manager` | `employee_id`, `manager_employee_id`, `created_by_id` |
| `documents` | `owner_employee_id`, `uploaded_by_id` |
| `document_access` | `document_id`, `grantee_id` (kiracı kullanıcısıysa), `granted_by_id` |
| `leave_requests`, `approval_steps`, `shift_assignments`, `attendance_records` | ilgili `*_id` kolonları |
| `access_requests`, `access_grants` | `requested_by_id`, `decided_by_id`, `granted_to_id`, `subject_employee_id` |
| `user_roles` (yeni kolon) | `scope_org_unit_id → departments` |

**Kural:** Yeni bir kiracı-içi FK eklenirse `apps/tenancy/migrations/0003_...` içindeki
`GUARDS` listesi **düzenlenmez**; guard, ilgili tablonun **yeni migration'ında**
tanımlanır.

## D.4 Platform şemasının erişim modeli

| Katman | Mekanizma |
|---|---|
| Şema | `platform` şemasına `persopyro_app`'in USAGE yetkisi **yok** |
| Rol | Yalnızca `persopyro_platform` (NOSUPERUSER, NOBYPASSRLS) |
| Kiracı verisi | Platform rolü, `hr` tablolarına **RLS'e tabi** erişir; kiracı bağlamını yalnızca onay kaydı doğrulandıktan sonra, tek işlem için kurar |
| Kayıt | Her kullanım platform denetim akışına + `hr.platform_access_notices`'a |

## D.5 RLS'siz tablolar (gerekçeli liste, genişletilecek)

| Tablo | Gerekçe | Yetki |
|---|---|---|
| `permissions` | Global tanım (kiracı verisi yok) | SELECT (yazma geri alınacak — M6) |
| `tenant_directory` | Giriş öncesi eşleme | SELECT |
| `api_tokens` | Token bağlamı taşır; doğrulama bağlamdan önce | INSERT, SELECT + kolon düzeyi UPDATE (`last_used_at`/`revoked_at`/`revoked_reason`) |
| `platform.*` | Platform şeması; erişim şema yetkisiyle | yalnızca `persopyro_platform` |
| Django teknik tabloları | Altyapı | mevcut hâliyle (OD19 ile sadeleşecek) |

## D.6 Yetki hijyeni (yeni tablolar)

`scripts/02_grant_app_privileges.sql` içindeki `ALTER DEFAULT PRIVILEGES`,
`hr`'a eklenen **her yeni tabloya** uygulama rolüne tam DML verir. Bu nedenle her
yeni tablo migration'ı sonunda **açık bir yetki turu** yapılır:

| Tablo türü | `persopyro_app` yetkisi |
|---|---|
| Normal iş tablosu | SELECT, INSERT, UPDATE, DELETE |
| **OD21 soft-delete tablosu** (`departments`, `positions`, `employees`, `employee_personal_data`, `employee_manager` — M10–M13; `leave_types`, `leave_policies`, `leave_policy_rules`, `leave_balances`, `leave_requests`, `approval_steps` — M14) | **SELECT, INSERT, UPDATE** (DELETE/TRUNCATE revoke — silme `deleted_at` ile; K2/K3 ve `employees` fiziksel silinmez, `ON DELETE CASCADE` zinciri runtime'da kurulamaz) |
| **Append-only olay tablosu** (`leave_balance_entries` — M14) | **SELECT, INSERT** (UPDATE/DELETE/TRUNCATE revoke — bakiye defteri yalnız ekleme; denetim izi) |
| Denetim/olay tablosu (`audit_logs`, `platform_access_notices`) | **INSERT, SELECT** (UPDATE/DELETE revoke) |
| Yansı/dizin tablosu (`tenant_directory`) | **SELECT** |
| Global tanım tablosu (`permissions`) | **SELECT** |
| Django teknik tabloları — kullanılanlar (`django_session`, `cache_table`, `django_migrations`, `django_q_*`, `django_content_type`) | Gereken en az yetki (mevcut hâli) |
| Django teknik tabloları — kullanılmayanlar (`auth_group`, `auth_group_permissions`, `auth_permission`, `django_admin_log`) | **Hiç yetki (`REVOKE ALL`)** — OD19 kapalı |

---

# E) INDEX PLAN

İlkeler: (1) kiracı kolonu **her** sorgunun ilk filtresi olduğu için bileşik
indeksler `tenant_id` ile başlar; (2) dışa aktarma/sıralama indeksleri
`ORDER BY ... DESC` ile uyumludur; (3) benzersizlik soft-delete'i dikkate alır.

| Tablo | İndeks | Gerekçe |
|---|---|---|
| `users` | UNIQUE `public_id` (tam — **yeniden kullanılmaz**, OD21) | Genel kimlik ile tek kayıt arama; kimlik kalıcı |
| `users` | **kısmi** UNIQUE `(tenant_id, email) WHERE deleted_at IS NULL` | Soft-delete sonrası e-posta yeniden kullanılabilir (OD21) |
| `users` | `(tenant_id, status)` (mevcut) | Aktif kullanıcı listeleri |
| `roles` | **kısmi** UNIQUE `(tenant_id, code) WHERE deleted_at IS NULL` | OD21 |
| `user_roles` | `(tenant_id, user_id)`, `(tenant_id, scope_org_unit_id)` | Etkin izin çözümlemesi + kapsam |
| `user_roles` | **kısmi** UNIQUE `(user_id, role_id) WHERE deleted_at IS NULL` | OD21 |
| `user_permission_overrides` | **kısmi** UNIQUE `(user_id, permission_id) WHERE deleted_at IS NULL` | OD21 |
| `tenants` | `slug`, `domain` **tam** benzersiz (**istisna**, OD21) | Şirket slug'ı kalıcı rezerve; destek/denetim belirsizliğini ve `public_id` ön ek kaymasını önler |
| `departments` | **kısmi** UNIQUE `(tenant_id, code) WHERE deleted_at IS NULL`, `(tenant_id, parent_id)` **ağaç sorguları için** (OD24: recursive CTE; `path`/closure kolonu **yoktur**) | `UNIT` kapsam sorguları (OD24) + kısmi benzersizlik (OD21) |
| `positions` | **kısmi** UNIQUE `(tenant_id, code) WHERE deleted_at IS NULL` | OD21 |
| `employees` | **kısmi** UNIQUE `(tenant_id, employee_no) WHERE deleted_at IS NULL`; `(tenant_id, department_id)`, `(tenant_id, user_id)`, `(tenant_id, status)` | Kapsam + listeler + OD21 |
| `employee_personal_data` | PK `employee_id`; `(tenant_id)` | 1:1 erişim |
| `employee_manager` | **kısmi** UNIQUE `(employee_id) WHERE relation_type='primary' AND effective_to IS NULL AND deleted_at IS NULL` (**tek aktif birincil yönetici**, ✅ M13), **kısmi** UNIQUE `(employee_id, manager_employee_id) WHERE deleted_at IS NULL AND effective_to IS NULL`, `(tenant_id, manager_employee_id)` | Raporlama zinciri + "ekibim" sorgusu (OD21/OD24) |
| `leave_types` | **kısmi** UNIQUE `(tenant_id, code) WHERE deleted_at IS NULL`, `(tenant_id, status)` | Sistem kodları rezerve + OD21 |
| `leave_policies` | **kısmi** UNIQUE `(tenant_id, leave_type_id) WHERE deleted_at IS NULL` | Tür başına **tek yaşayan** politika |
| `leave_policy_rules` | `(policy_id, min_service_months)` | Kural eşleşmesi (çakışma guard'ı ayrıca) |
| `leave_balances` | **kısmi** UNIQUE `(employee_id, leave_type_id, period_year) WHERE deleted_at IS NULL`, `(tenant_id, employee_id, period_year)` | Dönem bakiyesi tekilliği + sorgular |
| `leave_balance_entries` | **kısmi** UNIQUE `(balance_id, entry_kind, period_year) WHERE entry_kind='accrual'`, **kısmi** UNIQUE `(leave_request_id, entry_kind) WHERE leave_request_id IS NOT NULL`, `(tenant_id, employee_id, period_year)` | Hakediş idempotansı + **çift tüketim engeli** |
| `leave_requests` | **kısmi** `(tenant_id, status, start_date)`, `(tenant_id, employee_id, start_date)` | Onay kuyruğu + çalışan geçmişi |
| `approval_steps` | **kısmi** UNIQUE `(subject_kind, subject_id, step_no) WHERE deleted_at IS NULL`, `(tenant_id, approver_user_id, status)`, `(subject_kind, subject_id)` | Adım tekilliği + onay kuyruğu |
| `documents` | **kısmi** UNIQUE (belge numarası varsa) `WHERE deleted_at IS NULL`; `(tenant_id, owner_employee_id)`, `(tenant_id, classification)`, `(tenant_id, contains_personal_data)`, `(tenant_id, retention_until)` | Yetki + saklama işleri + OD21 |
| `document_access` | `(tenant_id, document_id)`, **kısmi** `(expires_at) WHERE expires_at IS NOT NULL` | Erişim kontrolü + süre temizliği |
| `attendance_records` | `(tenant_id, employee_id, work_date)` | Devam sorguları |
| `access_requests` (her iki şema) | `(status, created_at)`, `(tenant_id, requested_by_id)` | Onay kuyruğu |
| `access_grants` (her iki şema) | **kısmi** `(expires_at) WHERE revoked_at IS NULL` | Süreli izinlerin otomatik kapanması |
| `audit_logs` | `(tenant_id, created_at DESC)` (mevcut), `(tenant_id, actor_kind, created_at DESC)`, `(tenant_id, platform_actor_id)`, `(tenant_id, action)` | Denetim ekranı + platform erişimi soruşturması (OD23) |
| `tenants` | `public_id_prefix` **UNIQUE** (tam — kalıcı rezerve, OD25) | Ön ek üretiminde çakışma engeli ve kimlik kararlılığı |
| `platform.users` | UNIQUE `email`, UNIQUE `public_id` | Giriş + genel kimlik |
| `platform.tenant_registry` | UNIQUE `slug`, `domain`; `(status)` | Platform listeleri |
| `platform.audit_logs` | `(created_at DESC)`, `(actor_platform_user_id)` | Platform denetimi |

---

# F) DATA MIGRATION / BACKFILL

| # | Veri işi | Yöntem | Geri alma |
|---|---|---|---|
| **F1** | `tenants.public_id_prefix` ve `users.public_id` geri doldurma: önce prefix (mevcut `slug`'tan türet, çakışmada sonek) → sonra `public_id = <prefix>-<8×Crockford>`; her ikisi **idempotent** (`WHERE IS NULL`), ardından `NOT NULL` + `UNIQUE` | Kolon/tetikleyici DROP |
| **F2** | `manager → dept_manager` | Kiracı bazında `UPDATE hr.roles SET code='dept_manager' WHERE code='manager'`; çakışma denetimi (`dept_manager` zaten varsa atlanır ve raporlanır) | Ters UPDATE + rapor |
| **F3** | `tenant_officer` şablon rolü | Her kiracı için yoksa oluştur + izin seti ata (frozen liste) | Rol silinir (atama varsa pasifleştirilir) |
| **F4** | İzin kataloğu genişletmesi | Yeni `platform.*` + `dataaccess.*` + `org.*` kodları **yeni migration** ile eklenir (mevcut seed migration düzenlenmez) | Kodlar silinir (rol bağı yoksa) |
| **F5** | Varsayılan rollerin izin setine yeni kodlar | `tenant_admin`e yeni izinler; `dept_manager`e kapsam daraltması; **hiçbir role otomatik genişletme yapılmaz** | Rol-izin bağları silinir |
| **F6** | `platform.tenant_registry` backfill | `INSERT ... SELECT id, slug, domain, status, max_users, max_storage_mb FROM hr.tenants WHERE deleted_at IS NULL` (yansı tetikleyicisi sonrası) | Tablo DROP |
| **F7** | Kiracı dizini tutarlılığı | `tenant_directory` ile `hr.tenants` karşılaştırması; eksik/fazla satır raporu (şu an: 1 satır, tutarlı) | — |
| **F8** | Denetim kaydı yeni kolonları | `actor_kind` mevcut kayıtlar için `tenant_user` (user_id doluysa) / `system` olarak doldurulur | Kolon DROP |
| **F9** | İlk platform yöneticisi (SUPERADMIN) | Migration **değil**: CLI/betik ile elle oluşturulur (parola ekrana yazılır, kayda geçmez). İkinci yönetici ataması OD17 kararına bağlı | Hesap pasifleştirilir |
| **F10** | **Mevcut tenant token'larının iptali (OD26 — kapalı):** `UPDATE hr.api_tokens SET revoked_at=now(), revoked_reason='policy_migration', scopes='{}', purpose='policy_migration (kapsamsız dönem)'` → ardından `NOT NULL`/CHECK/tetikleyiciler uygulanır. Kapsamsız token **hiçbir koşulda "tüm izinler" sayılmaz**; kullanıcı yeniden giriş yapıp kapsamlı token üretir. Ölçüm: bugün **0 satır** — **✅ UYGULANDI** (0003 migration'ının veri adımı; 0 satır iptal edildi) | Geri alma gerekmez (iptal bilinçli; yeni kapsamlı token üretilir) |

**Backfill kuralları:** (1) Her geri doldurma **idempotent** yazılır
(`WHERE ... IS NULL`); (2) büyük tablolarda partili (`LIMIT`/`ctid`) çalışır;
(3) geri doldurma sonrası kolon `NOT NULL` yapılırken **önce** kalan NULL var mı
diye sayılır ve varsa migration hata verir; (4) veri göçü ve şema değişikliği
**aynı** migration'da yapılmaz (M5, F1 ayrımı).

---

# G) ROLLBACK STRATEJİSİ

1. **Kural:** Mevcut migration dosyaları değiştirilmez; hata düzeltmesi **yeni**
   migration ile yapılır.
2. **Şema ekleme (M1–M4, M10–M19):** Geri alma DROP ile yapılır; veri kaybı
   yalnızca yeni oluşan tablolarda ve **geliştirme verisinde** kabul edilebilir
   (üretimde önce yedek).
3. **Mevcut tabloya kolon ekleme (M5, M8, M9, M20):** Geri alma = kolon DROP.
   `public_id` gibi dış sistemlere verilmiş olabilecek değerler için geri alma
   **yalnızca kullanım başlamadan önce** güvenlidir; sonrasında geri alma yerine
   ileri düzeltme tercih edilir.
4. **Veri göçleri (F2, F5, F8):** Ters eşleme **kayıt altına alınarak** yapılır
   (eski kod → yeni kod tablosu migration içinde `RETURNS`/rapor olarak yazılır).
5. **RLS ekleme:** Yeni tabloda geri alma = `DROP POLICY` + `ALTER TABLE ... DISABLE
   ROW LEVEL SECURITY` + tablo DROP. **Mevcut 7 tablonun politikası geri alınmaz**
   — politika değişmedi.
6. **Yetki geri alma (M6, D.6):** Geri alma (yetkiyi tekrar vermek) teknik olarak
   mümkündür ama **güvenlik açısından yapılmaz**; gerekirse açık gerekçe + kayıt.
7. **Test ortamı:** Her partiden önce boş bir veritabanında **sıfırdan kurulum**
   (`CREATE DATABASE persopyro_fresh` → `migrate` → doğrulama → `DROP DATABASE`)
   çalıştırılır. Bu yöntem daha önce sıfırdan kurulumu bozan bir hatayı yakaladı;
   her partide tekrarlanır.
8. **Üretim geri alma:** Uygulama kodu geri alınabilir (git), şema geri alma
   planlı ve yedekli yapılır; RLS'in devre dışı kaldığı bir ara durum **kabul
   edilmez** (migration, politika + FORCE'u aynı işlemde kurar).

---

# H) RİSKLER

| # | Risk | Etki | Azaltım |
|---|---|---|---|
| **H1** | Platform şeması/rolü yanlış yapılandırılırsa kiracı verisi sızabilir | Felaket | `persopyro_app`'e `platform` USAGE verilmez; `BYPASSRLS` yasak; T16/T17 testleri; erişim yalnızca onay kaydıyla |
| **H2** | Yansı tabloları (`tenant_directory`, `platform.tenant_registry`) `hr.tenants`'tan sapabilir | Orta | **OD20 kararı:** tek yönlü yansı + aynı işlem içinde tetikleyici (**fail-closed**: yansı yazılamazsa kaynak yazımı geri alınır); tutarlılık testi (M21) |
| **H3** | Django admin/`auth_*` paralel yetkilendirme yolu (`is_staff`) uygulamanın RBAC'ini atlar | **Yüksek** | **KAPATILDI (OD19, 2026-09-11):** `/admin/` yolu ve `django.contrib.admin` kaldırıldı (`GET /admin/` → **404**), `auth_*`/`django_admin_log` yetkileri geri alındı (uygulama rolünde **hiç yetki yok**), `is_staff` üretimde `True` olamaz (uygulama guard'ı + `users_is_staff_disabled` DB kısıtı + `audit_is_staff` denetimi), `is_superuser` alanı yoktur — M22–M22d uygulandı ✅ |
| **H4** | Soft-delete + benzersizlik: silinen kaydın benzersiz değeri yeni kaydı engeller | Orta | **OD21 kararı:** kısmi benzersiz indeksler (`WHERE deleted_at IS NULL`); `public_id` ve tenant slug'ı bilinçli istisna (kalıcı rezerve) — M8/M9/M27 |
| **H5** | `public_id` geri doldurma çakışması/biçim değişikliği | Orta | Global benzersiz indeks + yeniden deneme; biçim OD2 ile sabit |
| **H6** | Yeni tabloda RLS/guard unutulması | **Yüksek** | Yapısal test (RLS+FORCE) + guard testleri; `M23` zorunlu adım |
| **H7** | `ALTER DEFAULT PRIVILEGES` tüm yeni tablolara tam DML veriyor | Orta | Her migration sonunda yetki turu (D.6) |
| **H8** | Uzun işlem: kiracı bağlamı `yield` boyunca açık tutuluyor; ağır migration/backfill ile çakışırsa kilit | Orta | Migration'lar **sahip rolle ve bağlamsız** çalışır; ağır işler düşük trafikte, partili |
| **H9** | Organizasyon alt ağacı sorguları (recursive CTE) büyüdükçe yavaşlar | Orta | **OD24 kararı:** `(tenant_id, parent_id)` indeksi + kısa TTL'li kapsam önbelleği + derinlik sınırı; **geri dönüş tetikleyicileri** (derinlik > 20, birim > 5.000, p95 > 50 ms) ölçümle izlenir. **OD5 kapalı (2026-09-11):** kapsam **güncel alt ağaçtır** → birim taşıma/yönetici değişiminde önbellek **geçersiz kılınır** (bayat önbellek yetki kapsamını yanlış gösterir = güvenlik riski); taşıma denetim kaydına yazılır |
| **H10** | Platform ↔ `hr` şema bağımlılığı (katalog paylaşımı) kırılgan | Düşük | Katalog salt-okunur; platform rolü katalogda yalnızca SELECT |
| **H11** | Kiracı silme/arşivleme sırasında FK zinciri ve yansılar | Orta | `ON DELETE CASCADE` yalnızca yansılarda; kiracı verisi soft-delete (OD6) |
| **H12** | `hr` şemasında Django'nun kendi tabloları olması (ölçüldü) yanlışlıkla mimari sayılabilir | Düşük | Belgede ayrım yapıldı (A.1.2); OD19 ile sadeleştirme |

---

# I) OPEN DECISION (veritabanı)

Yetkilendirme kararları `docs/AUTHORIZATION.md` bölüm 14'te tutulur; bu turda
ortaya çıkanlar aynı numaralandırmayla **orada** kayıtlıdır:

| # | Konu | Öneri |
|---|---|---|
| **OD2** | Genel kimlik biçimi — **kullanıcı kararıyla KAPATILDI** (kiracı ön eki + Crockford Base32 + 8 rastgele karakter) | Uygulanacak |
| **OD19** | Django admin / `auth_*` paralel yetki yolu — **KAPATILDI** (2026-09-11) | `/admin/` + `django.contrib.admin` kaldırılır; `auth_*`/`django_admin_log` yetkileri geri alınır; `is_staff`/`is_superuser` tek başına yetki vermez (M22–M22d) |
| **OD20** | Kiracı üst verisinin sahibi — **KAPATILDI** (2026-09-11) | `hr.tenants` canonical; `platform.tenant_registry` minimum referans indeksi; tek yönlü yansı, fail-closed (M4/M21) |
| **OD21** | Soft delete ve benzersizlik politikası — **KAPATILDI** (2026-09-11) | `deleted_at` = silme, `status` = yaşam döngüsü; kısmi benzersiz indeksler; `public_id` + tenant slug'ı kalıcı rezerve; denetim kaydı soft-delete edilmez (M8/M9/M25–M28, F7) |
| **OD22** | Platform API token'larının yeri ve kuralları — **KAPATILDI** (2026-09-11) | Yalnızca `platform.tokens`; zorunlu `scope`/`purpose`, `expires_at` NOT NULL, `revoked_at`; interactive ≤ 24 saat, service ≤ 90 gün, superadmin kapsamı yasak; RLS atlama yok (M3) |
| **OD23** | Denetim kaydında platform aktörünün temsili — **KAPATILDI** (2026-09-11) | `actor_kind` (4 değerli kapalı küme) + `platform_actor_id`/`actor_label`/`access_grant_ref` (FK'sız) + `purpose`; platform aktörü için zorunlu `CHECK`; `user` FK → `PROTECT`; `source`/`changed_fields` eklenmez (M20, F9) |
| **OD24** | Organizasyon ağacı DB modeli — **KAPATILDI** (2026-09-11) | Adjacency list (`parent_id`) + özyinelemeli CTE; path/nested set/closure **yok**; döngü DB tetikleyicisiyle yasak (derinlik 20); tek birincil yönetici `employee_manager` ile; ağaç yetki mirası üretmez (M10/M11) |
| **OD25** | Tenant slug ve public ID prefix ilişkisi — **KAPATILDI** (2026-09-11) | Immutable `tenants.public_id_prefix` (tam UNIQUE, yeniden tahsis yok); `users.public_id` immutable; prefix DB tetikleyicisiyle değişmez; public ID yetki sınırı değil, RLS aynen geçerli (M5/M27, F1) |
| **OD26** | Tenant API token modelinin hizalanması (kapsam + iptal izi) — **bulgu F8 — KAPATILDI** (2026-09-11) | Tenant token: `token_kind` + `purpose` NOT NULL + `scopes` (katalog doğrulamalı, `platform.*` yasak, wildcard yok) + `revoked_at`/`revoked_reason`; **kesişim kuralı** (`current_user_permissions ∩ token_scopes`, fail-closed); interactive ≤ 30 gün / service ≤ 90 gün, süresiz yok; iptal tek yönlü (M29/M30, veri göçü F10) |
| **OD27** | Kiracı içinde birden fazla tüzel şirket (`companies`) — **KAPATILDI** (2026-09-11) | v1: `1 tenant = 1 tüzel şirket`; `companies`/`legal_entities` **yok**, `company_id` hiçbir tabloda yok; gelecekte ayrı katman (tenant sınırı korunur); tetikleyiciler OD27'de |
| **OD28** | `public_id_prefix` yeniden markalama istisnası — **KAPATILDI** (2026-09-11) | v1: **immutable**, değiştirme uç noktası yok, istisna **kodlanmaz**; ileride yalnızca platform operasyonu + denetim + `public_id` sabit + eski prefix rezerve (M5 tamamlayıcı işler) |

---

## J) Kurallar (bağlayıcı — mevcut bölümden korunmuştur)

1. Yeni tablo eklenirse `tenant_id` **zorunludur** (platform geneli tablolar hariç;
   bunlar açıkça gerekçelendirilir ve `platform` şemasında yaşar).
2. Yabancı anahtarlar kiracılar arası referans üretemez (guard tetikleyicisi zorunlu).
3. Kişisel veri kolonları açıkça işaretlenir ve maskelenebilir olmalıdır.
4. Şema değişikliği geri alınabilir (ileri/geri migration) olarak yazılır.
5. Var olan kolon adları ve tipleri gerekçesiz değiştirilmez (uygulanmış
   migration dosyaları **hiç** değiştirilmez).
6. İndeks ihtiyacı gerçek sorgu desenlerine göre eklenir.
7. Kişisel veri içeren tablolar (`employee_personal_data`, `documents`,
   `access_*`) için erişim **her okumada** kayıtlanır.

## K) Yedeklilik ve saklama

- Günlük otomatik yedek + nokta-zamanalı geri yükleme hedeflenir; yedekler
  **şifrelidir** ve geri yükleme testi yapılır.
- Silme yerine arşivleme önceliklidir; saklama süresi kiracı sözleşmesi ve
  mevzuata göre tanımlanır (`retention_until` alanı evrak ve kişisel veride
  zorunludur).
- Yedekler de kiracı verisini karıştırmayacak şekilde geri yüklenebilmelidir;
  yedeğe erişim **ayrıcalıklı bir işlemdir** ve kayıtlanır.
