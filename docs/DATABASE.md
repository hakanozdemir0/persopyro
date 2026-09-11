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

İlgili belgeler: `docs/AUTHORIZATION.md` (yetki modeli, OD listesi),
`docs/SECURITY.md` (kontroller, tehdit modeli), `docs/ARCHITECTURE.md` (K1–K22).

---

## 0. Özet: mevcut durum haritası

| Ölçüm | Sonuç |
|---|---|
| Şemalar | `hr`, `public` (başka şema yok; platform şeması **yok**) |
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
| `auth_permission`, `auth_group`, `auth_group_permissions` | `django.contrib.auth` | ❌ **kullanılmıyor** — kendi RBAC'imiz var; **yetkiler geri alınacak** (OD19 kapalı) |
| `django_admin_log` | `django.contrib.admin` | ❌ kullanılmıyor; `/admin/` **kaldırılacak** (OD19 kapalı) |

**Karar (OD19 — kapalı, 2026-09-11):** `/admin/` yolu kaldırılır,
`django.contrib.admin` uygulama listesinden çıkarılır, `create_superuser` artık
`is_staff=True` atamaz ve bu tabloların (`auth_*`, `django_admin_log`) uygulama
rolü yetkileri **tamamen geri alınır**. Tablolar teknik olarak kalabilir
(`django.contrib.auth`; `AbstractBaseUser`, parola hash'leme ve oturum kimlik
doğrulaması için gereklidir) ama **izin kaynağı olamaz**. Belirleyici ölçüm:
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
- **Kolonlar (18):** `id, name, legal_name, tax_number, email, phone, country, city, address, status, max_users, max_storage_mb, settings(jsonb), created_at, updated_at, deleted_at, slug, domain`

### `users`
- **PURPOSE:** Kiracı kullanıcısı; kimlik doğrulama + organizasyon üyeliği.
- **PRIMARY KEY:** `id` (uuid) · **UNIQUE:** `(tenant_id, email)`
- **FOREIGN KEYS:** `tenant_id → tenants.id`, `created_by_id → users.id` (self)
- **TENANT ISOLATION:** `tenant_id = hr.current_tenant_id()`; `created_by_id` guard tetikleyicisi
- **PERSONAL DATA LEVEL:** **K2** (`first_name`, `last_name`, `email`, `phone`) · K3'e yakın: `totp_secret`
- **RLS:** ✅ etkin + FORCE · **AUDIT REQUIREMENT:** yüksek (oluşturma, güncelleme, pasifleştirme, MFA değişimi, rol ataması)
- **Kolonlar (21):** `password, last_login, id, email, first_name, last_name, phone, status, is_staff, email_verified_at, password_changed_at, failed_login_count, lockout_until, multi_factor_enabled, totp_secret, last_login_at, created_at, updated_at, deleted_at, created_by_id, tenant_id`
- **Eksik:** `public_id` ❌ (hedef şemada eklenecek — bölüm B)

### `roles`
- **PURPOSE:** Kiracıya bağlı rol (varsayılan şablon veya kiracının oluşturduğu özel rol).
- **PRIMARY KEY:** `id` (uuid) · **UNIQUE:** `(tenant_id, code)`
- **FOREIGN KEYS:** `tenant_id → tenants.id` (guard tetikleyicisi **yok**; `created_by_id` kolonu **yok** — dokümanın eski 3.3 listesi bu satırda yanlıştı, düzeltildi)
- **TENANT ISOLATION:** `tenant_id = hr.current_tenant_id()`
- **PERSONAL DATA LEVEL:** K0
- **RLS:** ✅ etkin + FORCE · **AUDIT REQUIREMENT:** yüksek (rol oluşturma/silme, izin seti değişimi)
- **Kolonlar (10):** `id, created_at, updated_at, deleted_at, name, code, role_type, description, is_assignable, tenant_id`
- **Doğrulanan veri:** `tenant_admin` (29 izin), `hr_manager` (22), `manager` (7), `employee` (6)

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

### `user_roles`
- **PURPOSE:** Kullanıcıya rol ataması (süreli olabilir — `expires_at`).
- **PRIMARY KEY:** `id` · **UNIQUE:** `(user_id, role_id)`
- **FOREIGN KEYS:** `user_id → users.id`, `role_id → roles.id`, `granted_by_id → users.id`, `tenant_id → tenants.id`
- **TENANT ISOLATION:** `tenant_id` + 3 guard tetikleyicisi · **PERSONAL DATA LEVEL:** K1
- **RLS:** ✅ + FORCE · **AUDIT REQUIREMENT:** **yüksek** (yetki değişikliği)
- **Eksik:** `scope_org_unit_id` (kapsam daraltması) ❌ ve `created_by_id` yok (bölüm B)

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
- **Eksik (hedef):** `purpose`, `grant_id`, `changed_fields`, `source`, `actor_kind`, `actor_public_id`, zincir özeti ❌

### `api_tokens`
- **PURPOSE:** API token'ı; yalnızca SHA-256 özeti saklanır.
- **PRIMARY KEY:** `id` · **UNIQUE:** `token_hash`
- **FOREIGN KEYS:** `user_id → users.id`, `tenant_id → tenants.id`
- **TENANT ISOLATION:** RLS yok; bağlam token satırından öğrenilir (gerekçe A.5) · **PERSONAL DATA LEVEL:** K1
- **RLS:** yok (bilinçli) · **Yetkiler:** SELECT/INSERT/DELETE/UPDATE (uygulama ihtiyacı)

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

7/7 tabloda `rls=true` **ve** `force_rls=true`. Politikalarda platform istisnası
**yoktur** (bu, OD3 kararının veritabanı karşılığıdır: politika gevşetilmez).

## A.4 Kiracılar arası referans guard'ları (9 tetikleyici, 6 tablo)

`hr.ensure_same_tenant_ref()` + `BEFORE INSERT OR UPDATE` tetikleyicisi:

| Tablo | Sütun(lar) → hedef |
|---|---|
| `users` | `created_by_id → users` |
| `role_permissions` | `role_id → roles` |
| `user_roles` | `user_id → users`, `role_id → roles`, `granted_by_id → users` |
| `user_permission_overrides` | `user_id → users`, `granted_by_id → users` |
| `audit_logs` | `user_id → users` |
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
| `tenants`, `users`, `roles`, `role_permissions`, `user_roles`, `user_permission_overrides`, `api_tokens` | DELETE, INSERT, SELECT, UPDATE | ✅ beklenen |

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
| `accounts` | 0001 → 0002 | `users`; `api_tokens` (+ knox tablosu düşürüldü) |
| `rbac` | 0001 → 0003 | `roles`, `permissions`, `role_permissions`, `user_roles`, `user_permission_overrides`; `role_permissions.tenant_id`; izin kataloğu seed (29) |
| `audit` | 0001 | `audit_logs` |

**Kural (uygulanmış migration değişmez):** Bu dosyalar tarihsel kayıttır; yeni
ihtiyaç **yeni migration** ile eklenir. Bu belgedeki hiçbir iş kalemi mevcut
migration dosyasını düzenlemez (tek istisna yok — `0002` daha önce bir kez
düzeltilmişti ve o hata sıfırdan kurulumu bozmuştu; tekrarlanmayacak).

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
| `persopyro_platform` | ❌ | ❌ | `platform` şemasında tam DML; `hr` şemasında yalnızca **RLS'e tabi** SELECT/DML (ayrıcalığı yok), `hr.audit_logs` INSERT/SELECT, `hr.tenant_directory` SELECT |

`persopyro_app` **`platform` şemasını göremez** (USAGE yok) → kiracı isteğinden
platform verisine yol yoktur. (Test: T17.)

## B.2 Platform tabloları (yeni) 🔜

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

### `platform.roles`
- **PURPOSE:** `superadmin`, `platform_admin` tanımları.
- **PRIMARY KEY:** `id` · **UNIQUE:** `code` · **FK:** yok
- **TENANT ISOLATION:** kapsam dışı · **PERSONAL DATA LEVEL:** K0 · **RLS:** yok
- **AUDIT:** rol tanımı değişikliği kayıtlanır

### `platform.role_permissions`
- **PURPOSE:** Platform rolü → izin bağı. İzin kataloğu **paylaşılır** (`hr.permissions`, salt okunur).
- **PRIMARY KEY:** `id` · **UNIQUE:** `(role_id, permission_id)` · **FK:** `role_id → platform.roles`, `permission_id → hr.permissions`
- **TENANT ISOLATION:** kapsam dışı · **RLS:** yok · **AUDIT:** yüksek

### `platform.user_roles`
- **PURPOSE:** Platform kullanıcısı → platform rolü (süreli olabilir).
- **PRIMARY KEY:** `id` · **UNIQUE:** `(platform_user_id, role_id)` · **FK:** `platform_user_id → platform.users`, `role_id → platform.roles`, `granted_by_id → platform.users`
- **RLS:** yok · **AUDIT:** yüksek

### `platform.tokens`
- **PURPOSE:** Platform API token'ı (kiracı token'larından **ayrı** — `hr.api_tokens`'ta `tenant_id` zorunlu) — ❓OD22
- **PRIMARY KEY:** `id` · **UNIQUE:** `token_hash` · **FK:** `platform_user_id → platform.users`
- **RLS:** yok · **AUDIT:** üretim/iptal kaydı

### `platform.tenant_registry`
- **PURPOSE:** Platformun kiracı listesi: `id, slug, domain, status, max_users, max_storage_mb, plan_code, created_at` — `hr.tenants`'tan **tetikleyiciyle** yansıtılır (aynı `tenant_directory` deseni).
- **PRIMARY KEY:** `tenant_id` · **UNIQUE:** `slug`, `domain` · **FK:** `tenant_id → hr.tenants.id` (CASCADE)
- **TENANT ISOLATION:** kapsam dışı (kiracı iş verisi yok) · **RLS:** yok
- **AUDIT:** durum/limit değişikliği (kaynak `hr.tenants` üzerinden)
- ❓OD20: tek kaynak `hr.tenants` mı kalacak, ticari alanlar (`plan_code`) burada mı tutulacak?

### `platform.access_requests` / `platform.access_grants`
- **PURPOSE:** Platform kaynaklı kişisel veri erişim talebi ve süreli izni (OD1/OD4).
- **`access_requests`:** `id, requested_by (platform_user_id), target_tenant_id, resource_type, resource_ids uuid[], field_list text[], purpose text NOT NULL, status (pending/approved/rejected/expired/cancelled), approvals jsonb, created_at, decided_at, expires_after_minutes`
  - **Kısıt:** `purpose` için **asgari uzunluk + anlamlılık** kontrolü (`PURPOSE_QUALITY`) — uygulama katmanı + `CHECK (length(btrim(purpose)) >= 20)`.
- **`access_grants`:** `id, request_id, target_tenant_id, granted_fields text[], starts_at, expires_at NOT NULL, revoked_at, use_count, last_used_at`
  - **Koşul:** `GRANT_VALID` = `revoked_at IS NULL AND now() < expires_at`.
- **TENANT ISOLATION:** kapsam dışı ama **tek kiracıya hedefli** (`target_tenant_id` zorunlu)
- **RLS:** yok (platform şeması) · **AUDIT:** **çok yüksek** — talep, onay, red, başlangıç, bitiş (OD4)

### `platform.audit_logs`
- **PURPOSE:** Platform akışının denetim kaydı (kiracı akışından ayrı — K22).
- **PRIMARY KEY:** `id` · **FK:** `actor_platform_user_id → platform.users`
- **RLS:** yok · **Yetkiler:** `persopyro_platform` yalnızca INSERT/SELECT (append-only)
- **Ek:** platform kaynaklı kiracı erişimleri **ayrıca** `hr.platform_access_notices`'a yazılır (kiracıya görünürlük).

## B.3 Mevcut tablolara eklenecekler / değişecekler 🔜

| Tablo | Değişiklik | Gerekçe |
|---|---|---|
| `users` | `public_id varchar(32) NOT NULL` + **global benzersiz** indeks + üretim fonksiyonu (Crockford Base32, 8 karakter, `gen_random_bytes`) | OD2 (kapalı): genel kimlik |
| `users` | `deleted_at` ile birlikte `(tenant_id, email)` benzersizliğinin **kısmi** olması (`WHERE deleted_at IS NULL`) | H.4 / OD21 |
| `roles` | `scope_type varchar(16) NOT NULL DEFAULT 'TENANT'` (`TENANT`/`ORG_UNIT`/`SELF`/`ASSIGNED`) | Kapsam modeli (AUTHORIZATION 4.4) |
| `roles` | `(tenant_id, code)` benzersizliği kısmi (`WHERE deleted_at IS NULL`) | OD21 |
| `user_roles` | `scope_org_unit_id uuid NULL → departments.id` + guard tetikleyicisi + kısmi benzersizlik | Kapsam daraltma (AUTHORIZATION 4.4) |
| `user_roles` | `(user_id, role_id)` benzersizliği kısmi | OD21 |
| `role_permissions` | `(role_id, permission_id)` benzersizliği zaten var; **ek değişiklik yok** | — |
| `audit_logs` | `actor_kind` (`tenant_user`/`platform_user`/`system`), `actor_public_id`, `purpose`, `grant_id`, `changed_fields jsonb`, `source` (`web`/`api`/`platform`), `prev_hash`/`entry_hash` | OD4, K22, OD23; zincirleme (OD13) |
| `permissions` | `platform.*` kodları eklenecek (modül `platform`); **yazma yetkisi geri alınacak** | Katalog genişletmesi + en az yetki |
| `tenants` | Değişiklik **yok** (RLS aynı kalır); `platform.tenant_registry` yansısı eklenir | OD3: politika gevşetilmez |
| `api_tokens` | Değişiklik yok (kiracı token'ı olarak kalır) | OD22 |

## B.4 Kiracı iş tabloları (yeni, `hr` şemasında, RLS zorunlu) 🔜

Yetkilendirme modelinin ihtiyaç duyduğu minimum küme tasarlanmıştır; bordro,
performans, işe alım, faturalama gibi modüller **bu turun kapsamı dışındadır**
(ileride aynı kurallarla eklenir).

### `hr.departments`
- **PURPOSE:** Organizasyon birimleri; `UNIT` kapsamının temeli.
- **PRIMARY KEY:** `id` · **UNIQUE:** `(tenant_id, code)`
- **FOREIGN KEYS:** `tenant_id → tenants.id`, `parent_id → departments.id` (self), `manager_employee_id → employees.id` (nullable)
- **TENANT ISOLATION:** `tenant_id` RLS + `parent_id`/`manager_employee_id` guard · **PERSONAL DATA LEVEL:** K0
- **RLS:** ✅ + FORCE zorunlu · **AUDIT:** yapı değişikliği, birim taşıma (`org.unit.move`)

### `hr.positions`
- **PURPOSE:** Unvan/pozisyon tanımı.
- **PRIMARY KEY:** `id` · **UNIQUE:** `(tenant_id, code)` · **FK:** `tenant_id`
- **RLS:** ✅ + FORCE · **PERSONAL DATA LEVEL:** K0 · **AUDIT:** orta

### `hr.employees`
- **PURPOSE:** Çalışanın **iş** kaydı (kimlik verisi değil).
- **PRIMARY KEY:** `id` · **UNIQUE:** `(tenant_id, employee_no)`
- **FOREIGN KEYS:** `tenant_id`, `user_id → users.id` (nullable), `department_id → departments.id`, `position_id → positions.id` (nullable)
- **TENANT ISOLATION:** `tenant_id` RLS + tüm FK'lar için guard · **PERSONAL DATA LEVEL:** **K0/K1** — bilinçli olarak PII **içermez**
- **RLS:** ✅ + FORCE · **AUDIT:** yüksek (oluşturma, birim/unvan/manager değişimi, çıkış)

### `hr.employee_personal_data` (1:1)
- **PURPOSE:** Kişisel veriyi ayrı tabloda tutar → maskeleme ve erişim **tablo kapsamında** kontrol edilir.
- **PRIMARY KEY:** `employee_id` (1:1) · **FK:** `tenant_id`, `employee_id → employees.id` (CASCADE)
- **Kolonlar:** K2: `first_name, last_name, birth_date, national_id, address, personal_email, personal_phone, iban` · **K3 (şifreli):** `health_notes`, `disability_status`, `biometric_ref`
- **TENANT ISOLATION:** `tenant_id` RLS · **PERSONAL DATA LEVEL:** **K2 + K3**
- **RLS:** ✅ + FORCE · **AUDIT:** **çok yüksek** — okuma dahil (gerekçeli, `employee.personal_data.read`)

### `hr.employee_manager` (raporlama ilişkisi)
- **PURPOSE:** Yönetici–çalışan ilişkisi (geçmişli, çoklu yönetici destekli).
- **PRIMARY KEY:** `id` · **UNIQUE (kısmi):** `(employee_id, manager_employee_id) WHERE effective_to IS NULL`
- **FOREIGN KEYS:** `tenant_id`, `employee_id`, `manager_employee_id`, `created_by_id`
- **TENANT ISOLATION:** `tenant_id` + 3 guard · **PERSONAL DATA LEVEL:** K0
- **RLS:** ✅ + FORCE · **AUDIT:** yüksek (yönetici ataması yetki etkisi taşır)

### `hr.documents` + `hr.document_access`
- **`documents`:** `id, tenant_id, owner_employee_id, category, title, storage_key, checksum_sha256, mime_type, size_bytes, classification (public/internal/confidential/restricted), contains_personal_data (yes/no/unsure), personal_data_categories text[], retention_until, version, uploaded_by_id, created_at, deleted_at`
- **`document_access`:** `id, tenant_id, document_id, grantee_kind (user/role/platform_user), grantee_id, access (read/download), expires_at, granted_by_id, reason`
- **TENANT ISOLATION:** `tenant_id` RLS + guard (`owner_employee_id`, `uploaded_by_id`, `document_id`, `grantee_id` kiracı içi doğrulanır)
- **PERSONAL DATA LEVEL:** **K4** (içerik) · **RLS:** ✅ + FORCE · **AUDIT:** **çok yüksek** (yükleme, görüntüleme, indirme, paylaşım, silme, sınıflandırma)

### `hr.leave_requests`, `hr.leave_types`, `hr.leave_balances`, `hr.approval_steps`
- **PURPOSE:** İzin süreci + onay adımları (`ASSIGNED` ve `SEPARATE_ACTOR` koşullarının veri kaynağı).
- **`approval_steps`:** `id, tenant_id, subject_kind, subject_id, step_no, approver_user_id (nullable), approver_role_code (nullable), status, decided_at, decision_reason, delegated_from_id`
- **TENANT ISOLATION:** `tenant_id` RLS + guard · **PERSONAL DATA LEVEL:** K2 (izin gerekçesi) · **RLS:** ✅ + FORCE
- **AUDIT:** **yüksek** (talep, onay, red, devir, iptal)

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
     │      ──< platform.tenant_registry      (yansı, platform)   ❓OD20
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
| **M1** | `platform` şeması + `persopyro_platform` rolü + yetkiler (betik: `scripts/03_platform_roles.sql`) | DDL (sahip+süper) | — | `DROP SCHEMA platform CASCADE` + `DROP ROLE` |
| **M2** | `platform.users`, `platform.roles`, `platform.role_permissions`, `platform.user_roles` | Yeni | M1 | DROP TABLE (yeni, veri kaybı yalnızca platform hesabı) |
| **M3** | `platform.tokens` (OD22 kararı sonrası) | Yeni | M2 | DROP TABLE |
| **M4** | `platform.tenant_registry` + `hr.tenants` üzerine yansı tetikleyicisi (SECURITY DEFINER) + backfill | Yeni + tetikleyici | M1 | Tetikleyici + tablo DROP (hr.tenants'a dokunulmaz) |
| **M5** | `hr.users.public_id`: kolon (nullable) → üretim fonksiyonu → backfill → `NOT NULL` + global benzersiz indeks | Şema + veri | — | İndeks + kolon DROP |
| **M6** | `hr.permissions`: `platform.*` kodları (yeni migration, frozen liste) + `REVOKE INSERT/UPDATE/DELETE ON hr.permissions FROM persopyro_app` | Veri + yetki | — | Kodlar silinebilir; yetki geri verilebilir (⚠️ istenmez) |
| **M7** | Rol kodu göçü: `manager → dept_manager` (kiracı başına, veri) + `tenant_officer` şablon rolünün oluşturulması | Veri | M6 | Ters eşleme (kayıt tutulur) |
| **M8** | `hr.roles.scope_type` + kısmi benzersizlik (`WHERE deleted_at IS NULL`) | Şema | — | Kolon + indeks geri alınır |
| **M9** | `hr.user_roles.scope_org_unit_id` (FK) + guard + kısmi benzersizlikler | Şema + tetikleyici | M14 (departments) | Kolon/tetikleyici DROP |
| **M10** | `hr.departments` + `hr.positions` + RLS + FORCE + guard'lar + indeksler | Yeni | M1 (şema zaten var) | DROP TABLE (veri kaybı kabul, yeni modül) |
| **M11** | Organizasyon ağacı mekanizması (OD24 kararı: `path`/closure tablosu) | Şema + backfill | M10 | Kolon/tablo DROP |
| **M12** | `hr.employees` + `hr.employee_personal_data` (K3 şifreli kolonlar) + RLS + guard'lar | Yeni | M10 | DROP TABLE |
| **M13** | `hr.employee_manager` + RLS + guard + kısmi benzersizlik | Yeni | M12 | DROP TABLE |
| **M14** | `hr.leave_*`, `hr.approval_steps` + RLS + guard | Yeni | M12 | DROP TABLE |
| **M15** | `hr.shifts`, `hr.shift_assignments`, `hr.attendance_records` + RLS + guard | Yeni | M12 | DROP TABLE |
| **M16** | `hr.documents` + `hr.document_access` + sınıflandırma/kişisel veri işaretleri + RLS + guard | Yeni | M12 | DROP TABLE |
| **M17** | `hr.access_requests` + `hr.access_grants` (kiracı içi) + RLS + guard + `PURPOSE_QUALITY` CHECK | Yeni | M12 | DROP TABLE |
| **M18** | `platform.access_requests` + `platform.access_grants` + onay kayıtları (çift onay) | Yeni | M2 | DROP TABLE |
| **M19** | `hr.platform_access_notices` + platform servisinin kiracı bağlamıyla yazma yolu | Yeni | M18 | DROP TABLE |
| **M20** | `hr.audit_logs` genişletme: `actor_kind`, `actor_public_id`, `purpose`, `grant_id`, `changed_fields`, `source` (+ zincirleme OD13 kararına bağlı) | Şema | M19 | Kolonlar DROP |
| **M21** | `hr.tenant_directory`'nin `platform.tenant_registry` ile tutarlılığı için test + (OD20 kararı) birleştirme | Test/kod | M4 | — |
| **M22** | **`/admin/` yolunun kaldırılması + `django.contrib.admin`'in uygulama listesinden çıkarılması** (OD19 kapalı) | Kod + ayar | — | Route/uygulama geri eklenebilir |
| **M22b** | `auth_group`, `auth_group_permissions`, `auth_permission`, `django_admin_log` tablolarında `persopyro_app` yetkilerinin **tamamen geri alınması** (`REVOKE ALL`) | Yetki | M22 | Yetki geri verilebilir (istenmez) |
| **M22c** | `create_superuser`'ın `is_staff=True` atamaması + üretimde `is_staff = true` olan **aktif kullanıcı bulunmaması** için denetim sorgusu ve test | Kod + test | M22b | Kod geri alınır |
| **M22d** | Kaldırma sonrası **sıfırdan kurulum testi** (boş DB → migrate → doğrulama): admin çıkarılınca `django_admin_log` oluşmaz ve kurulum farkı gözlemlenir | Test | M22c | — |
| **M23** | Test genişletmesi: RLS yapısal testine yeni tablolar; T15–T18 davranış testleri; guard testleri | Test | M10–M20 | — |
| **M24** | İndeks turu (bölüm E) + `ANALYZE` | Şema | M10–M20 | İndeks DROP |

**Uygulama sırası önerisi (3 parti):**
1. **Parti 1 (platform temeli):** M1–M5, M22 — kiracı verisine dokunmayan, geri
   alması kolay işler.
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
| `api_tokens` | Token bağlamı taşır; doğrulama bağlamdan önce | SELECT/INSERT/DELETE |
| `platform.*` | Platform şeması; erişim şema yetkisiyle | yalnızca `persopyro_platform` |
| Django teknik tabloları | Altyapı | mevcut hâliyle (OD19 ile sadeleşecek) |

## D.6 Yetki hijyeni (yeni tablolar)

`scripts/02_grant_app_privileges.sql` içindeki `ALTER DEFAULT PRIVILEGES`,
`hr`'a eklenen **her yeni tabloya** uygulama rolüne tam DML verir. Bu nedenle her
yeni tablo migration'ı sonunda **açık bir yetki turu** yapılır:

| Tablo türü | `persopyro_app` yetkisi |
|---|---|
| Normal iş tablosu | SELECT, INSERT, UPDATE, DELETE |
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
| `users` | UNIQUE `public_id` | Genel kimlik ile tek kayıt arama |
| `users` | **kısmi** UNIQUE `(tenant_id, email) WHERE deleted_at IS NULL` | Soft-delete sonrası e-posta yeniden kullanılabilir (OD21) |
| `users` | `(tenant_id, status)` (mevcut) | Aktif kullanıcı listeleri |
| `roles` | **kısmi** UNIQUE `(tenant_id, code) WHERE deleted_at IS NULL` | OD21 |
| `user_roles` | `(tenant_id, user_id)`, `(tenant_id, scope_org_unit_id)` | Etkin izin çözümlemesi + kapsam |
| `user_roles` | **kısmi** UNIQUE `(user_id, role_id) WHERE deleted_at IS NULL` | OD21 |
| `user_permission_overrides` | **kısmi** UNIQUE `(user_id, permission_id) WHERE deleted_at IS NULL` | OD21 |
| `departments` | `(tenant_id, parent_id)`, `(tenant_id, code)` UNIQUE, ağaç kolonu (`path text_pattern_ops` **veya** closure tablosu `(ancestor_id, descendant_id)`) | `UNIT` kapsam sorguları (OD24) |
| `employees` | `(tenant_id, department_id)`, `(tenant_id, user_id)`, `(tenant_id, status)`, UNIQUE `(tenant_id, employee_no)` | Kapsam + listeler |
| `employee_personal_data` | PK `employee_id`; `(tenant_id)` | 1:1 erişim |
| `employee_manager` | **kısmi** UNIQUE `(employee_id, manager_employee_id) WHERE effective_to IS NULL`, `(tenant_id, manager_employee_id)` | Raporlama zinciri + "ekibim" sorgusu |
| `documents` | `(tenant_id, owner_employee_id)`, `(tenant_id, classification)`, `(tenant_id, contains_personal_data)`, `(tenant_id, retention_until)` | Yetki + saklama işleri |
| `document_access` | `(tenant_id, document_id)`, **kısmi** `(expires_at) WHERE expires_at IS NOT NULL` | Erişim kontrolü + süre temizliği |
| `leave_requests` / `approval_steps` | `(tenant_id, status)`, `(tenant_id, approver_user_id, status)` | Onay kuyruğu |
| `attendance_records` | `(tenant_id, employee_id, work_date)` | Devam sorguları |
| `access_requests` (her iki şema) | `(status, created_at)`, `(tenant_id, requested_by_id)` | Onay kuyruğu |
| `access_grants` (her iki şema) | **kısmi** `(expires_at) WHERE revoked_at IS NULL` | Süreli izinlerin otomatik kapanması |
| `audit_logs` | `(tenant_id, created_at DESC)` (mevcut), `(tenant_id, actor_public_id)`, `(tenant_id, action)` | Denetim ekranı + soruşturma |
| `platform.users` | UNIQUE `email`, UNIQUE `public_id` | Giriş + genel kimlik |
| `platform.tenant_registry` | UNIQUE `slug`, `domain`; `(status)` | Platform listeleri |
| `platform.audit_logs` | `(created_at DESC)`, `(actor_platform_user_id)` | Platform denetimi |

---

# F) DATA MIGRATION / BACKFILL

| # | Veri işi | Yöntem | Geri alma |
|---|---|---|---|
| **F1** | `users.public_id` geri doldurma | `UPDATE hr.users SET public_id = hr.generate_public_id(tenant_id)` — kolon önce nullable eklenir, doldurulur, sonra NOT NULL + UNIQUE | Kolon DROP |
| **F2** | `manager → dept_manager` | Kiracı bazında `UPDATE hr.roles SET code='dept_manager' WHERE code='manager'`; çakışma denetimi (`dept_manager` zaten varsa atlanır ve raporlanır) | Ters UPDATE + rapor |
| **F3** | `tenant_officer` şablon rolü | Her kiracı için yoksa oluştur + izin seti ata (frozen liste) | Rol silinir (atama varsa pasifleştirilir) |
| **F4** | İzin kataloğu genişletmesi | Yeni `platform.*` + `dataaccess.*` + `org.*` kodları **yeni migration** ile eklenir (mevcut seed migration düzenlenmez) | Kodlar silinir (rol bağı yoksa) |
| **F5** | Varsayılan rollerin izin setine yeni kodlar | `tenant_admin`e yeni izinler; `dept_manager`e kapsam daraltması; **hiçbir role otomatik genişletme yapılmaz** | Rol-izin bağları silinir |
| **F6** | `platform.tenant_registry` backfill | `INSERT ... SELECT id, slug, domain, status, max_users, max_storage_mb FROM hr.tenants WHERE deleted_at IS NULL` (yansı tetikleyicisi sonrası) | Tablo DROP |
| **F7** | Kiracı dizini tutarlılığı | `tenant_directory` ile `hr.tenants` karşılaştırması; eksik/fazla satır raporu (şu an: 1 satır, tutarlı) | — |
| **F8** | Denetim kaydı yeni kolonları | `actor_kind` mevcut kayıtlar için `tenant_user` (user_id doluysa) / `system` olarak doldurulur | Kolon DROP |
| **F9** | İlk platform yöneticisi (SUPERADMIN) | Migration **değil**: CLI/betik ile elle oluşturulur (parola ekrana yazılır, kayda geçmez). İkinci yönetici ataması OD17 kararına bağlı | Hesap pasifleştirilir |

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
| **H2** | Yansı tabloları (`tenant_directory`, `platform.tenant_registry`) `hr.tenants`'tan sapabilir | Orta | Tek kaynak `hr.tenants`; tek yansı tetikleyicisi deseni; tutarlılık testi (M21) |
| **H3** | Django admin/`auth_*` paralel yetkilendirme yolu (`is_staff`) uygulamanın RBAC'ini atlar | **Yüksek** | **Karar verildi (OD19 — kapalı):** `/admin/` + `django.contrib.admin` kaldırılır, `auth_*`/`django_admin_log` yetkileri geri alınır, `is_staff`/`is_superuser` tek başına yetki vermez. Uygulama M22–M22d ile yapılır → **uygulama kalemi**, açık karar değil |
| **H4** | Soft-delete + benzersizlik: silinen kaydın benzersiz değeri yeni kaydı engeller | Orta | ❓OD21: kısmi benzersiz indeksler |
| **H5** | `public_id` geri doldurma çakışması/biçim değişikliği | Orta | Global benzersiz indeks + yeniden deneme; biçim OD2 ile sabit |
| **H6** | Yeni tabloda RLS/guard unutulması | **Yüksek** | Yapısal test (RLS+FORCE) + guard testleri; `M23` zorunlu adım |
| **H7** | `ALTER DEFAULT PRIVILEGES` tüm yeni tablolara tam DML veriyor | Orta | Her migration sonunda yetki turu (D.6) |
| **H8** | Uzun işlem: kiracı bağlamı `yield` boyunca açık tutuluyor; ağır migration/backfill ile çakışırsa kilit | Orta | Migration'lar **sahip rolle ve bağlamsız** çalışır; ağır işler düşük trafikte, partili |
| **H9** | Organizasyon alt ağacı sorguları (recursive CTE) büyüdükçe yavaşlar | Orta | ❓OD24: materialized path/closure; indeks planı (E) |
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
| **OD20** | Kiracı üst verisinin sahibi: `hr.tenants` tek kaynak mı, `platform.tenant_registry` ticari alanları mı taşıyacak? | `hr.tenants` tek kaynak; registry yalnızca **yansı + ticari** alanlar, tetikleyiciyle |
| **OD21** | Soft-delete ile benzersizlik çakışması | Kısmi benzersiz indeksler (`WHERE deleted_at IS NULL`) |
| **OD22** | Platform API token'ı nerede tutulacak (`platform.tokens` mi, `hr.api_tokens`'a nullable tenant mı?) | Ayrı `platform.tokens` (kiracı tablosuna platform kaydı karıştırılmaz) |
| **OD23** | `hr.audit_logs`'ta platform aktörünün temsili (`user_id` NULL + `actor_kind` mi, ayrı tablo mu?) | `user_id` NULL + `actor_kind`/`actor_public_id` (K22 ikinci akış korunur) |
| **OD24** | Organizasyon ağacı mekanizması: `parent_id` + özyinelemeli CTE mi, materialized path mı, closure tablosu mu? | Önce `parent_id` + özyinelemeli CTE; ölçülen yavaşlama veya derinlik > 6'da closure |
| **OD25** | Kiracı `slug` değişirse mevcut `public_id` ön eki ne olur? | Ön ek **sabit kalır** (kimlik değişmez); yeni kullanıcılar yeni ön eki alır |

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
