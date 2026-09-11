# Değişiklik Kaydı — Persopyro

Kayıt biçimi: en yeni sürüm en üstte. Her giriş tarih, sürüm, eklenen /
değişen / doğrulanan / kalan başlıklarını içerir.

## [0.15.0] — 2026-09-11 — F7 ve F9 kapatıldı (kod + migration + test)

**Baseline (değişiklikten önce):** `bash scripts/run_tests.sh` → **97 passed**.
Bu girişte ilk kez **kod** değişti; diğer bulgulara (F8/F11/M22/organizasyon)
dokunulmadı.

### Eklenen / değişen

- **F7 — soft-delete yetki çürümesi (KAPATILDI).**
  `apps/rbac/services/permissions.py`: yetki çözümlemesindeki **her sorgu**
  `deleted_at IS NULL` koşulunu **açıkça** uygular (`UserRole` → atama; ardından
  `Role` → rol; `UserPermissionOverride` → istisna). Silinmiş rol, atama, `allow`
  istisnası yetki vermez; silinmiş `deny` istisnası **aktif karar sayılmaz**; etkin
  `deny` yine `allow`'u ezer. Pasif/silinmiş kullanıcı için izin kümesi **boş**
  döner (fail-closed). Gizli bir manager davranışına güvenilmedi — filtre sorguda
  görünür ve test edilebilir.
  `apps/rbac/services/roles.py`: `get_or_create` yerine açık sorgu + **ön kontrol**;
  silinmiş varsayılan rol bulunursa **otomatik restore edilmez, kopya üretilmez**,
  `SoftDeletedDefaultRoleError` yükseltilir (OD21: geri alma açık `*.restore` izni +
  denetim kaydı gerektirir).
- **F9 — denetim satırı bütünlüğü (KAPATILDI).**
  `apps/audit/models.py`: `AuditLog.user` → **`PROTECT`**. Yeni migration:
  `apps/audit/migrations/0002_alter_auditlog_user.py` (mevcut migration'lar
  değiştirilmedi).

### Ölçüm / doğrulama

- **Migration'ın gerçek PostgreSQL sonucu:** `sqlmigrate audit 0002` → **no-op**.
  Nedeni ölçüldü: Django FK DDL'inde `ON DELETE` **yazmaz**
  (`sql_create_fk` şablonu; `on_delete` tamamen ORM katmanında). Veritabanı kısıtı
  bu yüzden zaten `NO ACTION`'dı; migrate öncesi/sonrası değişmedi. Koruma artık
  ORM katmanında: kullanıcı silinmek istendiğinde **hiç SQL üretilmeden**
  `ProtectedError`. Ham SQL DELETE de `NO ACTION` nedeniyle engellenir, ancak FK'lar
  `DEFERRABLE INITIALLY DEFERRED` olduğu için denetim **commit anında** yapılır.
- `audit` migration'ları uygulandı: `[X] 0001_initial`, `[X] 0002_alter_auditlog_user`.
- `persopyro_app` yetkileri yeniden doğrulandı: `audit_logs` → UPDATE: **yok**,
  INSERT/SELECT: var.
- **Ölçüm düzeltmesi (doküman):** `hr.role_permissions` bir **bağ tablosudur** ve
  `deleted_at` kolonu **yoktur** (kolonlar: `id, created_at, updated_at,
  permission_id, role_id, tenant_id`). Önceki F7 metni bu tabloda bir `deleted_at`
  filtresinin eksik olduğunu varsayıyordu; **yanlıştı** — bağ kayıtları fiziksel
  silinir ve silinen kayıt yetki veremez (OD21 bağ tabloları için soft-delete
  öngörmez). Doküman düzeltildi.

### Test

- Yeni **`tests/test_rbac_soft_delete.py`** (8 test): silinmiş rol · silinmiş
  `UserRole` · kaldırılmış rol-izin bağı · silinmiş `ALLOW` · aktif `DENY` kazanır ·
  silinmiş `DENY` karar sayılmaz · silinmiş varsayılan rol otomatik restore
  edilmez · kaldırılmış bağ yeniden kurulabilir.
- Yeni **`tests/test_audit_integrity.py`** (5 test): ORM ile silme → `ProtectedError`
  · ham SQL DELETE → `IntegrityError` (`SET CONSTRAINTS ALL IMMEDIATE`) · denetim
  satırı değişmez (`user_id`, `updated_at`, `changes`) · `persopyro_app` yazma
  yetkisi yok · denetim kaydı `deleted_at` almaz.
- **RED→GREEN kanıtı:** düzeltme geçici olarak geri alındığında F7 testlerinden 4'ü
  kırmızı (`test_silinmis_rol_yetki_vermez`, `test_silinmis_user_role_yetki_vermez`,
  `test_silinmis_allow_override_izin_eklemez`,
  `test_silinmis_deny_aktif_karar_sayilmaz`); düzeltmeyle yeşil.
- **Final:** `bash scripts/run_tests.sh` → **110 passed** (97 + 13). `ruff check`
  temiz, `ruff format` uygulandı, `bandit` 0 bulgu, `manage.py check` temiz,
  `makemigrations --check` "No changes detected".

### Değişmeyenler (bilinçli)

- API token modeli ve F8/OD26 işleri (M29/M30), F11, Django Admin kaldırma (M22b–22d),
  organizasyon modülü, `SoftDeleteManager` standardı (M25: F7 için gerekli değil),
  `*.restore` izni (M26'nın kalan yarısı; silme özelliği yazıldığında).
- Küçük not (bulgu değil): `UserRole.granted_by` / `UserPermissionOverride.granted_by`
  hâlâ `SET_NULL` — fiziksel silmede "kim verdi" bilgisi kaybolur; yetki kararı
  vermediği için F9 kapsamına alınmadı.

### Değişen dosyalar

| Dosya | Değişiklik |
|---|---|
| `apps/rbac/services/permissions.py` | Açık `deleted_at IS NULL` filtreleri + pasif kullanıcı fail-closed |
| `apps/rbac/services/roles.py` | `get_or_create` kaldırıldı; `SoftDeletedDefaultRoleError` ön kontrolü |
| `apps/audit/models.py` | `AuditLog.user` → `PROTECT` |
| `apps/audit/migrations/0002_alter_auditlog_user.py` | **Yeni migration** (SQL no-op) |
| `tests/test_rbac_soft_delete.py` | Yeni (F7 regresyon) |
| `tests/test_audit_integrity.py` | Yeni (F9 regresyon) |
| `docs/SECURITY.md` | F7/F9 kapandı; T20 kapandı; ölçüm düzeltmesi |
| `docs/DATABASE.md` | `audit` 0001→0002; `role_permissions` soft-delete yok; M20/M25/M26/M28 durumu |
| `docs/AUTHORIZATION.md` | Açık bulgu listesi güncellendi |
| `docs/CHANGELOG.md` | Bu kayıt |

---

## [0.14.0] — 2026-09-11 — OD26 KAPATILDI: tenant API token modeli (F8 çözüm kararı; kod yok)

Yalnızca doküman. **Kod, migration ve veritabanı değiştirilmedi** (doğrulandı:
`git status` yalnızca `.md` gösterir).

### Karar (OD26 — KAPALI)

- **Model:** `hr.api_tokens` genişletilir; **yeni/ikinci token sistemi kurulmaz**
  (OD22 ile aynı alan anlamları). Eklenenler: `token_kind` (`interactive`/`service`),
  `purpose` NOT NULL (≥ 10 karakter), `scopes text[]` NOT NULL (boş kapsam yalnızca
  iptal edilmiş satırda; **wildcard yasak**; değerler `hr.permissions.code`'dan
  doğrulanır; **`platform.*` yasak**), `revoked_at` + `revoked_reason` (kapalı küme).
- **Kesişim kuralı (fail-closed):**
  `effective = current_user_permissions ∩ token_scopes` — tek kilit nokta
  (`apps/rbac/permissions.py`). Token sahibinin sahip olmadığı yetkiyi taşıyamaz;
  sonradan verilen izni kazanmaz; izin geri alınırsa o izni kullanamaz → rol
  değişiminde ayrı token iptali gerekmez.
- **TTL:** `interactive` ≤ 30 gün (720 sa), `service` ≤ 90 gün (2160 sa), **süresiz
  yok**; tavan üstü istek açık hatayla reddedilir. `API_TOKEN_TTL_HOURS=720`
  varsayılan olarak korunur.
- **İptal:** `token/logout` artık **silmez, işaretler**; yeni `token/revoke-all`;
  süre bitimi, sahip pasif/silinmiş, **kiracı askıda/iptal/silinmiş**, güvenlik
  olayında toplu iptal. İptal **tek yönlü**, izli (`revoked_at`+`revoked_reason`+
  denetim).
- **Değişmezlik:** `scopes`/`purpose`/`expires_at`/`token_kind` üretimden sonra
  değişmez → değişiklik = iptal + yeniden üretim (sessiz yetki kayması olmaz).
- **Geriye dönük uyumluluk:** mevcut kapsamsız token'lar **"tüm izinler" sayılmaz**;
  migrasyonda **iptal edilir** (`revoked_reason='policy_migration'`, sentinel
  `scopes='{}'`) ve kullanıcı yeniden giriş yapıp kapsamlı token üretir. Ölçüm:
  bugün `hr.api_tokens` **0 satır**.

### Yeni iş kalemleri

- **M29** — `hr.api_tokens` kolonları + CHECK'ler + iki tetikleyici (kapsam
  doğrulama; değişmezlik + tek yönlü iptal).
- **M30** — yetki turu: UPDATE `last_used_at` (+ iptal akışı); `delete()` yolu
  kaldırılır.
- **F10 (veri göçü)** — mevcut token'ların iptali.

### SECURITY FINDING

- **F11 (Yüksek) — Kiracı askıya alınsa/iptal edilse bile erişim kesilmiyor.**
  Ölçüm: `TenantStatus` (`active`/`trial`/`suspended`/`canceled`) tanımlı ama
  **hiçbir kod bu değeri okumuyor**. Askıya alınmış/iptal edilmiş kiracının
  kullanıcıları **yeni giriş yapabiliyor** (dizin satırı yalnızca fiziksel silmede
  kalkar); soft-delete edilmiş kiracıda yeni giriş engelliyor ama **mevcut
  oturum/token çalışmaya devam ediyor** (RLS yalnızca `tenant_id` eşitliğine bakar,
  kiracı durumuna bakmaz). Etki: ticari kontrol (askıya alma/iptal) ve hesap
  kapatmanın güvenlik karşılığı yok. Kapatma yolu OD26 kapsamına eklendi
  (`status NOT IN ('active','trial')` veya `deleted_at IS NOT NULL` → reddet +
  askıya almada toplu token iptali). Kodlanmadı.
- **F8** — çözüm kararı verildi; kod bekliyor. **F9/F10** açık kalmaya devam ediyor.

### Ek düzeltme (doküman hatası, kod değil)

- `docs/API.md` §2 yanlış biçimde `Authorization: Bearer` ve `/api/v1/auth/refresh`
  yazıyordu; kod `Token` anahtar sözcüğünü kullanır ve **refresh ucu yoktur** →
  düzeltildi, token sözleşmesi OD26 kararıyla güncellendi.
- `docs/DATABASE.md`'ye **etiket çakışması** notu eklendi: `F#` bu belgede veri göçü,
  `SECURITY.md`'de güvenlik bulgusu anlamına gelir.

### Yeni OPEN DECISION

- **Yok.** Karar sayıları: kapalı **14** (OD1–OD4, OD19–OD28), açık **14**
  (OD5–OD18). Engelleyici açık karar yoktur; **OD5** organizasyon modülünden önce
  netleşmelidir.

### Değişen dosyalar

| Dosya | Değişiklik |
|---|---|
| `docs/AUTHORIZATION.md` | OD26 **KAPALI** (9 başlıklı kesin karar: model, kapsam kuralı, TTL/`token_kind`, iptal, migration, F8 iş listesi, çakışmalar, gerekçe/güvenlik etkisi); durum satırları ve sayılar |
| `docs/DATABASE.md` | `api_tokens` kartı hedef modelle güncellendi; B.3 satırı; **M29/M30**; veri göçü **F10**; bölüm I'de OD26 kapalı; etiket çakışması notu |
| `docs/SECURITY.md` | **§3.5 Kiracı API token'ları** (yeni); T21 karar verildi; F8 "çözüm kararı verildi"; **F11** bulgusu; §15'e G10 |
| `docs/ARCHITECTURE.md` | **K32** karar kaydı |
| `docs/API.md` | §2 kimlik doğrulama düzeltildi + token gövdesi/iptal sözleşmesi; §4.1 uç tablosu (`revoke-all`, iptal semantiği) |
| `docs/CHANGELOG.md` | Bu kayıt |

### Doğrulanan / doğrulanmayan

- **Doğrulandı (kaynak okuma + ölçüm):** `ApiToken` alanları ve `issue()` imzası;
  `TokenLogoutView`'ın `delete()` çağrısı; `authenticate_credentials`'ta
  `revoked_at`/kiracı durumu kontrolünün **yokluğu**; `permission_catalog.py`'de
  **29 izin kodu** ve gerçek ad alanı; `TenantStatus`'un hiçbir kodda okunmadığı;
  `API_TOKEN_TTL_HOURS=720`; `tenant_directory` tetikleyicisinin yalnızca
  fiziksel silmede satır kaldırması.
- **Doğrulanmayan (kasıtlı):** hiçbir kod/migration/DB değişikliği yapılmadı;
  kesişim kuralı, tetikleyiciler ve uçlar **yazılmadı** (M29/M30/F10 iş kalemleri).

### Kalan

M2, M5, M10–M13, M25–M28, **M29/M30** ve F10; F7, F9, F11 açık. Açık kararlar:
OD5–OD18.

---

## [0.13.0] — 2026-09-11 — OD27 ve OD28 KAPATILDI (mimari karar; kod yok)

Yalnızca doküman. **Kod, migration ve veritabanı değiştirilmedi** (doğrulandı:
`git status` yalnızca `.md` gösterir).

### Kapatılan kararlar

- **OD27 — Tenant içinde birden fazla tüzel şirket.** v1: **`1 tenant = 1 tüzel
  şirket`**; ayrı `companies`/`legal_entities` tablosu **eklenmez**; `departments`,
  `positions`, `employees` doğrudan `tenant_id`'ye bağlı kalır; **RBAC kapsamına
  şirket katmanı eklenmez** ve `company_id` kolonu **hiçbir tabloda oluşturulmaz**.
  Tüzel kişilik bilgisi zaten `hr.tenants` satırındadır (doğrulandı). Mimari
  gelecekte çok tüzel kişiliğe **kapalı değil**: ayrı katman + nullable→backfill→NOT
  NULL geçişi; ancak **tenant güvenlik sınırı olmaya devam eder**. Ölçülebilir
  tetikleyiciler yazıldı (≥ %10 veya ≥ 3 kiracı talebi; sözleşmesel bordro/izin
  ayrımı; ardışık ≥ 2 bloke anlaşma; ≥ 2 işveren sicili).
- **OD28 — `public_id_prefix` yeniden markalama istisnası.** v1'de prefix
  **immutable**; rebrand/slug değişimi mevcut prefix'i ve `public_id`'leri
  değiştirmez; **değiştirme uç noktası yoktur** ve **istisna mekanizması v1'de
  kodlanmaz**. İleride gerekirse yalnızca **platform operasyonu** olarak, denetimli,
  `public_id`'ler sabit tutularak ve eski prefix rezerve kalacak şekilde tasarlanır
  (koşullar yazıldı).

### Ek kontroller (istenen)

- **M10–M13 etkisi: DEĞİŞMEDİ.** Ölçüm: migration planında ve hedef şemada hiçbir
  yerde `company_id`/`legal_entity_id` yoktu; OD27(A) seçildiği için M10
  (`departments`+`positions`), M11 (ağaç garantileri), M12 (`employees`+
  `employee_personal_data`), M13 (`employee_manager`) **aynen kalır**. Tablolara
  "**OD27 kapalı: `company_id` EKLENMEZ**" notu eklendi (yanlışlıkla eklenmesin).
- **OD28 sonrası eksik görülen işler tamamlandı (kural olarak M2/M5'e eklendi):**
  (1) `platform.users.public_id` için **rezerve `plt` ön eki** kuralı (kiracı ad
  alanıyla çakışmaz) → **M2**; (2) üretim fonksiyonları
  (`hr.generate_public_id_prefix`, `hr.generate_public_id`, çakışmada yeniden
  deneme); (3) format `CHECK`
  (`^[a-z0-9]{3,20}-[0-9A-HJKMNP-TV-Z]{8}$`); (4) **rezerve ad listesi**
  (`www, api, app, admin, portal, mail, support, hr, platform, plt, system`) → **M5**.

### Yeni SECURITY FINDING

- **F10 (Düşük) — Prefix/slug taklidi (typosquatting) engellenmiyor.** `slug`
  yalnızca tam eşleşme benzersizliği taşıyor; benzer görünen değerler serbest
  (`acme` / `acme1`). Risk bugün düşük (kiracı açılışı platform operasyonu), ancak
  `PROJECT.md`'de **self-servis kayıt** planlandığı için o yol açıldığında artar.
  Azaltım: rezerve ad listesi, prefix ≥ 3 karakter, açılışta manuel inceleme, çok
  yakın prefix tespitinde uyarı. Kod düzeltilmedi.

### Yeni OPEN DECISION

- **Yok.** Bu turda gerçekten zorunlu yeni bir belirsizlik çıkmadı; iki küçük boşluk
  (platform kullanıcısı prefix'i, üretim fonksiyonları/format/rezerve adlar)
  **kural** olarak kapatıldı.

### Değişen dosyalar

| Dosya | Değişiklik |
|---|---|
| `docs/AUTHORIZATION.md` | OD27 ve OD28 **KAPALI** (karar, gerekçe, şema/migration etkisi, güvenlik etkisi, çakışma, seçenekler + ölçülebilir tetikleyiciler); durum satırı ve özet sayıları (kapalı 13, açık 15) |
| `docs/DATABASE.md` | B.4'e OD27 sonucu notu; M10/M12'ye "`company_id` EKLENMEZ"; **M2**'ye `platform.users.public_id` (rezerve `plt` prefix), **M5**'e üretim fonksiyonları + format `CHECK` + rezerve ad listesi; `platform.users` kartına prefix kuralı; bölüm I'de OD27/OD28 kapalı |
| `docs/ORGANIZATION.md` | §9 **karar** olarak yeniden yazıldı (v1 tek tüzel kişilik, gelecek yolu, tetikleyiciler); referans farklar tablosu ve açık noktalar güncellendi |
| `docs/SECURITY.md` | §8'e prefix immutability + rezerve adlar + typosquatting notu; §5.1'e **F10** bulgusu |
| `docs/ARCHITECTURE.md` | **K30, K31** karar kayıtları |
| `docs/CHANGELOG.md` | Bu kayıt |

### Doğrulanan / doğrulanmayan

- **Doğrulandı (ölçüm):** `docs/` içinde `companies`/`company_id`/`legal_entity`
  geçen **tüm** satırlar tarandı — migration planında ve hedef şemada `company_id`
  **hiç yok** (yalnızca karar maddeleri ve referans atıfları); `hr.tenants` tüzel
  kimlik alanlarını (`legal_name`, `tax_number`, adres/iletişim) zaten taşıyor;
  mevcut slug benzersizliği yalnızca tam eşleşme (`tenants_slug_..._uniq`).
- **Doğrulanmadı (kasıtlı):** kod/migration/DB değişikliği yapılmadı; prefix üretim
  fonksiyonları ve format kısıtı **yazılmadı** (M5 iş kalemi).

### Kalan

M2 (platform kullanıcı public_id), M5 (prefix + public_id + fonksiyonlar + `CHECK` +
rezerve adlar), M27 (kısmi indeks), M10–M13 (organizasyon/çalışan), M25–M28 (F7).
Açık kararlar: **OD5** (organizasyon modülünden önce netleşmeli), OD26 (tenant token).

---

## [0.12.0] — 2026-09-11 — OD23, OD24, OD25 KAPATILDI (mimari karar; kod yok)

Yalnızca doküman. **Kod, migration ve veritabanı değiştirilmedi** (doğrulandı:
`git status` yalnızca `.md` gösterir).

### Kapatılan kararlar

- **OD23 — Denetim kaydında platform aktörü.** `actor_kind` **zorunlu** ve kapalı
  küme (`tenant_user`, `platform_user`, `service`, `system`); `user_id` yalnızca
  tenant kullanıcısı için; platform aktörü **FK'sız** `platform_actor_id` +
  `actor_label` (snapshot) ile temsil edilir; `purpose` ve `access_grant_ref`
  platform aktöründe **DB `CHECK` ile zorunlu** (gerekçesiz/onaysız platform erişimi
  kayıt dışı kalamaz). `source`/`changed_fields` **eklenmez** (sadeleştirme);
  `user` FK `SET_NULL` → **`PROTECT`** (append-only korunur). Kiracısız olaylar
  `platform.audit_logs`'a gider.
- **OD24 — Organizasyon ağacı DB modeli.** Adjacency list (`departments.parent_id`)
  + **özyinelemeli CTE**; materialized path/nested set/closure **v1'de yok**.
  Döngü ve derinlik (≤ 20) **veritabanı tetikleyicisiyle** engellenir; kiracı dışı
  ilişki mevcut guard ile yasak; bir çalışanın **tek birincil yöneticisi** olur
  (`employee_manager` + kısmi benzersizlik); **ağaç yetki mirası üretmez** (RBAC ayrı).
  Ölçek değerlendirmesi ve geri dönüş tetikleyicileri yazıldı.
- **OD25 — Tenant slug ve public ID prefix ilişkisi.** Yeni **immutable**
  `tenants.public_id_prefix` (tam UNIQUE, soft-deleted tenant'ta bile yeniden
  tahsis edilmez, DB tetikleyicisiyle değiştirilemez); `users.public_id` =
  `<prefix>-<8×Crockford>` ve **immutable**; public ID **yetki sınırı değildir**,
  kimlikle arama yapıldığında RLS aynen geçerlidir.

### Yeni SECURITY FINDING

- **F9 (Orta) — Denetim satırı `SET_NULL` ile güncellenebiliyor.** `AuditLog.user`
  ilişkisi `SET_NULL`; kullanıcı silinirse denetim satırına UPDATE yazılır ve bu,
  "denetim kaydı değiştirilemez" kuralıyla çelişir. Bugün fail-closed davranıyor
  (uygulama rolünün UPDATE yetkisi yok → silme hata verir), ama yetki gevşetilirse
  denetim geçmişi sessizce yeniden yazılır. Kapatma: `on_delete` → `PROTECT`
  (OD23 kararı).

### Yeni OPEN DECISION'lar

- **OD27 — Kiracı içinde birden fazla tüzel şirket (`companies`).** v1'de olacak mı?
  Öneri: v1 tek tüzel kişilik; `companies` ihtiyaç doğduğunda eklenir. **Kod
  yazımından önce netleştirilmeli** (kapsam çözümleyiciyi etkiler).
- **OD28 — Yeniden markalama sonrası `public_id_prefix` istisnası.** Öneri: v1'de
  istisna yok; sözleşmesel zorunlulukta denetimli "prefix göçü" (mevcut public ID'ler
  değişmez).

### Değişen dosyalar

| Dosya | Değişiklik |
|---|---|
| `docs/AUTHORIZATION.md` | OD23/OD24/OD25 **KAPALI** (karar, gerekçe, şema/migration etkisi, güvenlik etkisi, mevcut kodla çakışma, seçenekler); OD27 ve OD28 eklendi; durum ve özet sayıları (kapalı 11, açık 17) |
| `docs/DATABASE.md` | `audit_logs` hedef alanları + **F9 notu**; `tenants.public_id_prefix` (B.3, M5, M27, F1, E); `audit_logs` indeks planı; **M20** (audit genişletme + `CHECK` + `PROTECT`) ve **M11** (döngü/derinlik tetikleyicileri, path/closure **yok**) güncellendi; M9 bağımlılığı (M10) düzeltildi; bölüm I'de OD23/24/25 kapalı + OD27/OD28 |
| `docs/SECURITY.md` | §5.1'e **F9** bulgusu; §10 denetim bölümüne aktör modeli, platform aktörü kanıt zorunluluğu, `PROTECT` ve kiracısız olay kuralı |
| `docs/ORGANIZATION.md` | **Baştan yazıldı**: hedef şema (v1) + kişisel veri ayrımı, hiyerarşi modeli (OD24), döngü/tutarlılık garantileri tablosu, kapsam ve rol görünürlüğü (güncel rol kodları), birincil yönetici ve ek ilişkiler, yaşam döngüsü (OD21), referans tasarımdan bilinçli farklar, OD27 notu |
| `docs/ARCHITECTURE.md` | **K27, K28, K29** karar kayıtları |
| `docs/CHANGELOG.md` | Bu kayıt |

### Doğrulanan / doğrulanmayan

- **Doğrulandı (kaynak okuma + ölçüm):** `apps/audit/models.py` (alanlar,
  `SET_NULL`), `apps/audit/services.py` (`log_action` imzası, `tenant_id` zorunluluğu),
  `apps/tenancy/models.py` (`slug` SlugField unique, `public_id_prefix` yok),
  `hr.audit_logs` kısıtları (FK `NO ACTION`, uygulama rolünde yalnızca INSERT/SELECT),
  `hr.tenants` kolonları (`slug`/`domain`/`status`/`deleted_at`), mevcut tam benzersiz
  indeks listesi, `docs/ORGANIZATION.md` referans içeriği.
- **Doğrulanmadı (kasıtlı):** hiçbir kod/migration/DB değişikliği yapılmadı; ağaç
  performansı ve kapsam sorgu süreleri **ölçülmedi** (uygulama sonrası ölçülecek —
  geri dönüş tetikleyicileri OD24'te tanımlı).

### Kalan

`docs/DATABASE.md` M10/M11 (org ağacı + döngü garantileri), M20 (audit genişletme),
M5/M27 (prefix + public ID); M25–M28 (F7 kapatma). Açık kararlar: OD27 (v1 kapsamı
için kritik), OD28, OD26.

---

## [0.11.0] — 2026-09-11 — OD20, OD21, OD22 KAPATILDI (mimari karar; kod yok)

Yalnızca doküman. **Kod, migration ve veritabanı değiştirilmedi** (doğrulandı:
`git status` yalnızca `.md` gösterir).

### Kapatılan kararlar

- **OD20 — Kiracı üst verisinin sahibi.** `hr.tenants` **canonical** kalır;
  `platform.tenant_registry` **minimum referans indeksi**dir (ikinci tam kopya değil).
  Alan sahipliği tablosu yazıldı (kiracı alanları → `hr.tenants`; `plan_code`,
  `billing_ref`, `internal_note` → registry). Senkronizasyon **tek yönlü**
  (`hr.tenants` → registry) ve **fail-closed**: tetikleyici aynı işlemde çalışır,
  yansı yazılamazsa kaynak yazımı geri alınır → sapma oluşmaz. Ters yön yoktur.
- **OD21 — Soft delete ve benzersizlik politikası.** `deleted_at` = silme,
  `status` = yaşam döngüsü, **`is_active` sütunu yok** (yalnızca türeyen özellik).
  Fiziksel `DELETE` yalnızca CASCADE yansıları, KVKK saklama sonu ve geri alınmış
  migration için. **Sessiz yeniden etkinleştirme yasak** (`*.restore` izni + kayıt).
  Benzersizlik **kısmi indekslerle** (`WHERE deleted_at IS NULL`); **istisnalar:
  `public_id` ve kiracı `slug`ı kalıcı rezerve**. `audit_logs` `deleted_at` almaz.
  Tablo bazlı politika (users, employees, departments, positions, roles, documents,
  org ilişkileri) tek tek yazıldı.
- **OD22 — Platform API token'ları.** Yalnızca `platform.tokens`; ham token
  saklanmaz (SHA-256 özeti); zorunlu `scope` + `purpose`; `expires_at` NOT NULL,
  `revoked_at` ile iptal kaydı; `interactive` ≤ 24 saat, `service` ≤ 90 gün ve
  **superadmin kapsamı yasak**; boş kapsam reddedilir. Platform token'ı **tenant RLS
  sınırını aşamaz**; kiracı verisi yalnızca erişim onayı mekanizmasından geçer.

### Ortaya çıkan SECURITY FINDING'ler (kod düzeltilmedi, raporlandı)

- **F7 (Yüksek) — Soft-delete yetkiyi kaldırmıyor.** `SoftDeleteModel` var ama
  filtreleyen yönetici yok; `apps/rbac/services/permissions.py` içindeki
  `_active_role_ids`, `RolePermission`, `UserPermissionOverride` sorguları
  `deleted_at` filtresi kullanmıyor → silinmiş rol/atama/`allow` istisnası yetki
  vermeye devam eder. Bugün sömürü yolu yok (silme servisi yazılmadı) ama ilk
  "rolü sil" özelliğiyle gerçek açık hâline gelir. Ayrıca `create_default_roles`
  içindeki `get_or_create` silinmiş rolü "var" sayıp atlıyor. Kapatma: M25/M26/M28.
- **F8 (Orta) — Tenant token'ı kapsamsız ve iptal izi yok.** `hr.api_tokens`'ta
  `scope`/`revoked_at` yok, TTL sabit 30 gün → token sahibinin tüm yetkilerini
  taşır, iptal kanıtlanamaz. Platform tarafı OD22 ile kapatıldı; tenant tarafı
  **OD26** olarak açıldı (geriye dönük uyumlu ara adım: `revoked_at`).

### Yeni OPEN DECISION

- **OD26 — Tenant API token modelinin hizalanması** (kapsam + iptal izi). Öneri:
  `scope`, `purpose`, `revoked_at` ekle (OD22 ile simetri).

### Değişen dosyalar

| Dosya | Değişiklik |
|---|---|
| `docs/AUTHORIZATION.md` | OD20/OD21/OD22 **KAPALI** (altı başlıklı yapı: karar, gerekçe, şema/migration etkisi, güvenlik etkisi, mevcut kodla çakışma, seçenekler); OD26 eklendi; durum satırı ve özet karar sayıları (kapalı 8, açık 18) |
| `docs/DATABASE.md` | `platform.tenant_registry` (authoritative alan tablosu + tek yönlü fail-closed senkron) ve `platform.tokens` (alanlar, TTL/kapsam kuralları, iptal) kartları güncellendi; indeks planına kısmi benzersizlikler + `tenants` istisnası eklendi; **M25–M28** eklendi (soft-delete yönetici standardı, yetki çözümlemesi `deleted_at`, kısmi e-posta indeksi, `get_or_create` davranışı); H2/H4 karara bağlandı; bölüm I'de OD20/21/22 kapalı + OD26 |
| `docs/SECURITY.md` | Tehdit modeline **T20** (yetki çürümesi) ve **T21** (kapsamsız token) eklendi; **§5.1 Açık bulgular (SECURITY FINDING)** bölümü açıldı (F7, F8); §6'ya platform token kuralları |
| `docs/ARCHITECTURE.md` | **K24, K25, K26** karar kayıtları eklendi |
| `docs/PROJECT.md` | PostgreSQL sürümü (18 / 18.6) ve şema listesi (`hr` + `platform`) düzeltildi; knox kalıntısı giderildi; `DATABASE.md` tanımı güncellendi |
| `docs/CHANGELOG.md` | Bu kayıt |

### Doğrulanan / doğrulanmayan

- **Doğrulandı (kaynak okuma):** `hr.api_tokens` alanları ve `API_TOKEN_TTL_HOURS=720`
  (`config/settings.py`), `ApiToken` modeli (`apps/accounts/models.py`),
  `SoftDeleteModel` (`apps/common/models.py`), yetki çözümlemesi
  (`apps/rbac/services/permissions.py`), rol servisi (`apps/rbac/services/roles.py`),
  `Tenant`/`User` modelleri; mevcut tam benzersiz indeks listesi ve
  `tenant_directory` tetikleyici deseni (önceki turda ölçülmüştü).
- **Doğrulanmadı (kasıtlı):** hiçbir kod/migration/DB değişikliği yapılmadı;
  kararların uygulanması M-listesi iş kalemlerine bırakıldı.

### Kalan

`docs/DATABASE.md` M25–M28 (F7 kapatma) ve M3 (platform token tablosu);
OD26 kararı; mevcut engelleyici açık karar yok.

---

## [0.10.0] — 2026-09-11 — OD19 KAPATILDI: Django admin / `auth_*` paralel yetki yolu (kod yok)

Yalnızca doküman. **Kod, migration ve veritabanı değiştirilmedi** (doğrulandı:
`git status` yalnızca `.md` dosyaları gösterir).

### Kapatılan karar (OD19)

Üretimde **Django Admin kullanıcı yetkilendirmesi kullanılmaz**: `/admin/` yolu ve
`django.contrib.admin` kaldırılır; `auth_*` tabloları **gerçek RBAC kaynağı
değildir**; **`is_staff`/`is_superuser` tek başına hiçbir Persopyro yetkisi
vermez**; gerçek yetkilendirme yalnızca `docs/AUTHORIZATION.md` modeli üzerinden
yapılır. Django'nun teknik tabloları teknik amaçla kalabilir, izin kaynağı olamaz.
İleride admin kullanımı için ön koşullar yazılı olarak tanımlandı (ayrı alan adı/ağ
sınırı, `platform_users` ile kimlik doğrulama, MFA, `platform.*` izinleri, platform
denetim akışı).

**Seçim:** (A) kaldır → seçildi. (B) internal-only → yalnızca ön koşullarla
yeniden değerlendirilebilir. (C) olduğu gibi bırak → reddedildi.

### Kod incelemesi (gerçek dosyalardan)

| Nokta | Ölçüm |
|---|---|
| `/admin/` rotası | **var** — `config/urls.py:30` |
| `django.contrib.admin` | uygulama listesinde (`config/settings.py:78`) |
| `admin.py` / `admin.site.register` | **yok** (hiçbir model kayıtlı değil) |
| `is_staff` alanı | var (`apps/accounts/models.py:153`), varsayılan `False` |
| `create_superuser` | `is_staff=True` atıyor (`apps/accounts/models.py:135`) |
| `has_perm` | kendi RBAC'imize delege ediyor (`models.py:215-218`) |
| `has_module_perms` | `user_has_any_permission(self)` — "herhangi bir izin var mı" (`models.py:220-223`) |
| Django'nun admin kapısı | `AdminSite.has_permission` = `user.is_active and user.is_staff` (kurulu kaynak, `sites.py:207`) |
| `is_superuser` alanı | **yok** (`PermissionsMixin` kullanılmıyor) |
| DRF oturum kimlik doğrulaması | açık (`SessionAuthentication`) — izinler yine kendi sınıflarımızdan |
| `/admin/login/` formu | kiracı olmadan kimlik doğrulama **fail-closed** (`backends.py:59-62`) → form çalışmaz |

### Güvenlik bulguları (kod yazılmadı, yalnızca raporlandı)

- **F1 (Yüksek) — mevcut oturum + `is_staff` admin kapısını açıyor.** Kimlik
  doğrulama admin formuyla yapılamasa da, `is_staff=True` olan **aktif** bir
  kullanıcı, normal girişte aldığı oturumla `/admin/` dizinini açabilir. Bugünkü
  etki: boş admin dizini (0 kayıtlı model) + `django_admin_log` yazımı. İleride
  model kaydedilirse RBAC'i atlayan yol doğar. **Karar OD19 ile kapatıldı;
  uygulama M22–M22c bekliyor.**
- **F2 (Orta) — `has_module_perms` "herhangi bir izin" olarak yorumlanıyor.**
  Admin korunacak olsaydı, elinde tek bir izin bulunan bir kullanıcı modül
  kontrolünü geçerdi. Kaldırma kararı bu bulguyu da kapatıyor.
- **F3 (Orta) — `create_superuser` riski hedefliyor.** Akış hem kiracı bağını
  zorunlu kılıyor hem `is_staff=True` atıyor; yani "superuser" tam olarak riskli
  kombinasyonu (kiracı kullanıcısı + admin kapısı açık) üretiyor. Kodda bu akış
  hiçbir yerde **kullanılmıyor** (scripts/config/tests taraması: çağrı yok).
- **F4 (Düşük) — `is_superuser` alanı yok ama Django admin kodu/şablonları bu
  özniteliği okuyabilir.** Statik incelemede `django/contrib/admin/*.py` ve
  `options.py` içinde `is_superuser` okuması bulunamadı; şablonlarda
  (AttributeError'ı yutan) sessiz boş değer riski sürer. Kaldırma ile konu
  kapanıyor. **Bu madde çalıştırılarak doğrulanmadı** (aşağıya bakınız).
- **F5 (Bilgilendirme) — `SILENCED_SYSTEM_CHECKS = ["auth.E003","auth.W004"]`.**
  Bilinçli (e-posta kiracı başına tekil), gerekçesi `SECURITY.md` bölüm 3'te;
  kabul edilen risk olarak kayıtlı.
- **F6 (Bilgilendirme) — DRF'de `SessionAuthentication` açık.** Tasarım gereği;
  CSRF koruması ve her uçta izin sınıfı zorunluluğu (liste yoksa **reddet**)
  korunuyor.

### Değişen dosyalar

| Dosya | Değişiklik |
|---|---|
| `docs/AUTHORIZATION.md` | OD19 **KAPALI** olarak işaretlendi (yedi maddelik karar + ölçüm + implementasyon kalemleri + ileride admin kullanımı ön koşulları); durum satırı ve özet karar sayıları güncellendi (kapalı 5, açık 20) |
| `docs/SECURITY.md` | Bölüm 3'e **admin kapısı kapalı** + **`is_staff`/`is_superuser` kuralı** + "admin giriş formu kiracısız çalışmaz" maddeleri; bölüm 5 "tek yetkilendirme yolu" **karar verildi** olarak güncellendi; **T18** kontrolü karara bağlandı; **G8 kapatıldı**, G1/G8 kapalı kararlar başlığı altında toplandı |
| `docs/ARCHITECTURE.md` | Django Admin'i gerekçe gösteren 4 yer düzeltildi (teknoloji tablosu, django-q2 gerekçesi, FastAPI reddi, gelişmiş gerekçe bölümü); **K23 eklendi** |
| `docs/DATABASE.md` | A.1.2 tablo satırları + karar notu; **M22 → M22/M22b/M22c/M22d** olarak detaylandırıldı (route/app kaldırma, `REVOKE ALL`, `create_superuser` düzeltmesi + `is_staff` denetimi, sıfırdan kurulum testi); D.6 yetki tablosuna kullanılan/kullanılmayan Django tabloları ayrımı; **H3** riski "uygulama kalemi"ne döndü; bölüm I'de OD19 kapalı |
| `docs/CHANGELOG.md` | Bu kayıt |

### Doğrulanan / doğrulanmayan

- **Doğrulandı (kaynak okuma):** Yukarıdaki "kod incelemesi" tablosunun tamamı
  gerçek dosyalardan ve kurulu Django kaynağından okunarak çıkarıldı
  (`config/urls.py`, `config/settings.py`, `apps/accounts/models.py`,
  `apps/accounts/backends.py`, `apps/rbac/permissions.py`,
  `.venv/.../django/contrib/admin/sites.py`). `scripts/`, `config/`, `tests/`
  içinde `create_superuser`/`is_staff` **kullanımı yok** (tarama).
- **Doğrulanmadı:** `/admin/` erişimini uçtan uca kanıtlayacak çalıştırma
  denemesi **yapılmadı** (komut onay almadı ve hiç çalışmadı). Bu nedenle F1/F4
  "kaynak okumaya dayalı" bulgulardır; kanıt, Django'nun `AdminSite.has_permission`
  gövdesi (`is_active and is_staff`) ve `is_staff` alanının varlığıdır — bu ikisi
  kesin. "Oturumla admin dizininin açıldığı" sonucu bu ikisinden **türetilmiştir**,
  çalıştırılarak gösterilmemiştir.

### Kalan

`docs/DATABASE.md` **M22, M22b, M22c, M22d** (uygulama kalemleri) ve OD19'da
listelenen kod düzeltmeleri (`/admin/` route, uygulama listesi, `create_superuser`,
yetki geri alma, `is_staff` denetim sorgusu/testi). Karar durumu: **kapalı 5**
(OD1–OD4, OD19), **açık 20** (OD5–OD18, OD20–OD25), engelleyici yok.

---

## [0.9.0] — 2026-09-11 — Veritabanı envanteri, hedef şema ve migration planı (kod yok)

Yalnızca doküman. **Uygulama kodu, migration dosyaları ve veritabanı
değiştirilmedi** (doğrulandı: `git status` yalnızca `.md` gösterir). Bu turun
amacı, kesinleşen yetkilendirme/security tasarımını **mevcut veritabanıyla
karşılaştırmak** ve güvenli migration planını yazmaktı.

### Ölçümle çıkarılan mevcut durum

- `hr` şemasında **21 tablo**: 10 uygulama + 11 Django/teknik tablo.
- **7** tablo `RLS + FORCE` (hepsinde politika `tenant_id = hr.current_tenant_id()`;
  `tenants` için `id = ...`). Politikalarda platform istisnası **yok**.
- **9** kiracılar arası referans guard tetikleyicisi (6 tablo), **3** fonksiyon.
- Roller: `persopyro_app` ve `persopyro_owner` (ikisi de `NOSUPERUSER`,
  `NOBYPASSRLS`). `python` rolü yok; `postgres` uygulamada kullanılmıyor.
- Uygulanmış migration: `tenancy` 0001–0007, `accounts` 0001–0002,
  `rbac` 0001–0003, `audit` 0001.
- Gerçek veri (kiracı bağlamı kurularak ölçüldü): 1 kiracı, 1 kullanıcı, 4 rol
  (`tenant_admin` 29, `hr_manager` 22, `manager` 7, `employee` 6 izin),
  64 rol-izin bağı, 29 izin kodu, 2 denetim kaydı.
- `public_id` (genel kimlik) **şemada ve kodda yok** → hedef şemada eklenecek.

### Bulgular (ölçümle bulunan gerçek durumlar)

1. **Kiracı bağlamı olmadan kiracı tabloları 0 satır döndürüyor.** İlk ölçümde
   `tenants/users/roles` "0" göründü; bağlam kurulunca 1/1/4 çıktı. RLS'in
   fail-closed çalıştığının canlı kanıtı — ve her ölçümün bağlamla yapılması
   gerektiğinin.
2. **Django'nun kendi yetki tabloları `hr` şemasında** (`auth_permission`,
   `auth_group`, `auth_group_permissions`, `django_admin_log`) ve `/admin/` yolu
   **rotalı** (`config/urls.py`); `create_superuser` `is_staff=True` atıyor. Bu,
   uygulamanın RBAC'inden **bağımsız ikinci bir yetkilendirme yolu** → **OD19**.
3. **Fazla yetki:** uygulama rolü `hr.permissions` üzerinde INSERT/UPDATE/DELETE
   yetkisine sahip (ölçüldü). Global katalog salt okunur olmalı → T19, G9.
4. **Rol kodu farkı:** mevcut şablonda `manager` var; tasarım `dept_manager` ve
   `tenant_officer` kullanıyor → veri migration'ı gerekiyor (M7/F2/F3).
5. **Soft-delete ile benzersizlik:** `(tenant_id, email)`, `(tenant_id, code)`,
   `(user_id, role_id)` benzersiz indeksleri `deleted_at`'i dikkate almıyor →
   **OD21**.
6. **Ölü kod temizliği doğrulandı:** `tenancy/0007`, `0006`'da oluşturulan
   `hr.tenant_id_by_slug/domain` fonksiyonlarını düşürmüş; veritabanında yalnızca
   3 fonksiyon var (doküman bunu artık doğru gösteriyor).
7. **Doküman düzeltmesi:** `DATABASE.md` eski 3.3 listesindeki `roles.created_by_id`
   guard'ı gerçekte yok (kolon da yok) — düzeltildi.

### Karar

- **OD2 KAPATILDI** (kullanıcı kararı): genel kimlik biçimi
  `<kiracı-slug>-<8×Crockford Base32>`, rastgele, benzersiz, tahmin edilemez.
- Bu turda ortaya çıkan **7 yeni açık karar**: OD19 (Django admin/paralel yetki
  yolu — mevcut risk), OD20 (kiracı üst verisinin sahibi), OD21 (soft-delete +
  benzersizlik), OD22 (platform token'larının yeri), OD23 (denetimde platform
  aktörü), OD24 (organizasyon ağacı mekanizması), OD25 (slug değişince ön ek).
- Karar durumu: **kapalı 4** (OD1–OD4), **açık 21** (OD5–OD25), **engelleyici yok**.

### Değişen dosyalar

| Dosya | Değişiklik |
|---|---|
| `docs/DATABASE.md` | **Baştan yazıldı**: bölüm A (mevcut şema, tablo kartları, RLS/politikalar, guard'lar, fonksiyonlar, roller/yetkiler, indeksler, migration geçmişi, ölçülen veri), B (hedef şema: `platform` şeması + `persopyro_platform` rolü, platform tabloları, kiracı iş tabloları, ilişki haritası), C (M1–M24 migration planı, 3 parti), D (RLS planı + yetki hijyeni), E (indeks planı), F (veri göçü/backfill F1–F9), G (rollback), H (12 risk), I (veritabanı OPEN DECISION listesi) |
| `docs/AUTHORIZATION.md` | OD2 **KAPALI** olarak işaretlendi (biçim kararı); OD19–OD25 eklendi; 8.2 güncellendi; özet karar durumu düzeltildi |
| `docs/SECURITY.md` | Tehdit modeline **T18** (paralel yetki yolu) ve **T19** (fazla yetki) eklendi; bölüm 5'e "tek yetkilendirme yolu" ve "tanım tabloları salt okunur" ilkeleri; G8/G9 açık kararları |
| `docs/CHANGELOG.md` | Bu kayıt |

### Doğrulanan / doğrulanmayan

- **Doğrulandı:** Tüm mevcut-durum iddiaları canlı veritabanından ve dosyalardan
  ölçüldü (tablo/kolon/kısıt/indeks/politika/tetikleyici/fonksiyon/yetki/migration
  listesi/veri sayıları). Hedef şema, kapatılan kararlarla (OD1/OD3/OD4) çelişmiyor:
  RLS politikası gevşetilmiyor, `BYPASSRLS` yok, platform kullanıcıları kiracıya bağlı
  değil.
- **Doğrulanmadı (kasıtlı):** Hedef şemanın SQL'i **yazılmadı**; hiçbir migration
  çalıştırılmadı. Bu turun çıktısı plan, uygulama değil.

### Kalan

`docs/DATABASE.md` bölüm C'deki **M1–M24** iş kalemleri (Parti 1: M1–M5, M22 →
Parti 2: M6–M9, M17, M20 → Parti 3: M10–M16, M18–M19, M21, M23–M24). Parti 2
öncesi şifreli yedek + geri yükleme testi zorunlu; her partiden önce boş
veritabanında sıfırdan kurulum doğrulaması yapılır.

---

## [0.8.0] — 2026-09-11 — Yetkilendirme: OD1, OD3, OD4 KAPATILDI (kod yok)

Yalnızca doküman güncellemesi. Uygulama kodu, migration ve veritabanı
değiştirilmedi (doğrulandı: `git status` yalnızca `.md` dosyaları gösterir).

### Kapatılan kararlar

- **OD1 — SUPERADMIN kişisel veri erişimi: KAPALI.** Varsayılan erişim **yok**.
  Erişim yalnızca gerekçeli talep + onay + **süreli** erişim kaydı ile açılır;
  süre dolduğunda **otomatik kapanır**; erişim ve nedeni denetim kaydına yazılır.
- **OD3 — Platform kullanıcıları: KAPALI.** SUPERADMIN/ADMIN için **ayrı
  `platform_users` modeli**; kiracıya bağlı değil, kiracı tablosuyla
  birleştirilmez. **Kiracı RLS kuralları gevşetilmez**, **`BYPASSRLS`
  kullanılmaz**; platform erişimi **ayrı ve kontrollü servis/authorization
  katmanı** üzerinden tasarlanır.
- **OD4 — Kişisel veri erişim onayı: KAPALI.** Kiracı içinde **şirket yöneticisi /
  onay yetkilisi** temel onay merciidir; platform tarafında **çift onay (ikinci
  kontrol)**. **Boş/anlamsız gerekçeyle talep oluşturulamaz.** Talep, onay, red,
  erişim başlangıcı ve erişim bitişi denetim kaydına yazılır.

### Kararın mimari sonucu (kritik)

RLS politikasında platform istisnası **tasarımı geri çekildi** (önceki taslakta
koşullu bayrak vardı). Yeni tasarım: kiracı politikaları **hiç değişmez**; platform
erişimi ayrı şema + ayrı veritabanı rolü (`persopyro_platform`) ve ayrı servis
katmanıyla yürür; kiracı bağlamını yalnızca platform katmanı, geçerli ve süreli bir
erişim onayıyla **tek işlem** için kurar. Böylece "izolasyonu gevşetmeden platform
erişimi" mümkün olur. Kalan artık risk (bağlam kurulduktan sonra RLS'in platformu
kiracıdan ayırt edememesi) açıkça yazıldı ve uygulama katmanı kontrolleriyle
(ayrı rol, tek işlem, her kullanımda kayıt, kiracıya görünürlük) yönetiliyor.

### Değişen dosyalar

| Dosya | Değişiklik |
|---|---|
| `docs/AUTHORIZATION.md` | OD1/OD3/OD4 KAPALI olarak işaretlendi; bölüm **3.4** yeniden yazıldı (RLS gevşetilmez, ayrı katman/şema/rol); **5.4**'e `PURPOSE_QUALITY` ve `DUAL_APPROVAL` koşulları ve `GRANT_VALID` netleştirmesi; **6.2/A**, **6.2/I**, **6.3**, **7.3**, **10.1** kararlara göre güncellendi; **12**'ye T15–T18 negatif testleri; **13** geçiş planı 12 → 14 iş kalemine çıkarıldı; **14**'e OD17 ve OD18 eklendi; **15** özet karar durumu |
| `docs/SECURITY.md` | **4.2** platform erişimi yeniden yazıldı (politika değişmez, `BYPASSRLS` yasak, ayrı katman/rol, çift onay, gerekçe niteliği, otomatik kapanma); **5** ve **6**'ya karar maddeleri; tehdit **T12** güncellendi; **15**'te **G1 kapalı** olarak kayda geçti, **G7** eklendi |
| `docs/ARCHITECTURE.md` | **K19 kabul edildi** (OD1/OD3/OD4 kapalı); **K21** (ayrı şema + ayrı DB rolü) ve **K22** (ayrı platform denetim akışı) eklendi; `django-rest-knox` kalıntıları düzeltildi (K4 ve teknoloji tablosu — K15 ile tutarlı) |
| `docs/CHANGELOG.md` | Bu kayıt |

### Yeni açık kararlar

- **OD17 — Platform çift onayının uygulanabilirliği:** tek platform yöneticisi
  varsa veya acil durumda ikinci onay alınamıyorsa ne olacak? Öneri: fail-closed
  varsayılan + sınırlı, kayıtlı ve sonradan incelenen break-glass.
- **OD18 — Kiracının platform erişimindeki rolü:** bilgilendirme mi, onay mı?
  Öneri: yalnızca K3 (özel nitelikli) veri ve toplu erişimde kiracı onayı.
- `docs/SECURITY.md` **G7** bu iki kararla ilişkilidir; **G6** (break-glass
  sınırları) OD17'ye bağlandı.

### Doğrulanan

- OD numaralandırma ve atıf bütünlüğü kontrol edildi (tanımsız OD atfı yok;
  OD1, OD3, OD4 "KAPALI" olarak işaretli ve kararları metne işlenmiş).
- Karar durumu: kapalı 3, açık 15 (OD2, OD5–OD18). **Engelleyici açık karar yok.**

### Kalan

Karar bekleyenler: OD2, OD5–OD18 (hiçbiri engelleyici değil). OD17 uygulama
sırasında netleştirilmelidir. Kod aşaması: `docs/AUTHORIZATION.md` bölüm 13'teki
14 iş kalemi.

---

## [0.7.0] — 2026-09-11 — Yetkilendirme ve erişim kontrolü TASARIMI (kod yok)

Yalnızca tasarım: bu sürümde uygulama kodu yazılmadı, veritabanı/API/frontend
değişmedi. Amaç, kodlamadan önce yetki modelini ve sınırlarını netleştirmekti.

### Değişen (doküman)

- `docs/AUTHORIZATION.md` baştan yazıldı: 15 bölüm, 16 "OPEN DECISION".
- `docs/SECURITY.md` yeniden yazıldı: kişisel veri katmanları, ayrıcalıklı erişim
  (platform/destek), genel kimlik güvenliği, evrak sınıflandırması, denetim
  bütünlüğü, olay müdahalesi ve 7 yeni tehdit satırı (T11–T17).

### Tasarım kararları

- **Model:** RBAC + kapsam (scope) + kapalı koşul kümesi (hibrit). Tam ABAC
  (OPA/Cedar benzeri politika motoru) reddedildi; geri gelme tetikleyicileri
  ölçülebilir şekilde yazıldı (koşul türü > 15, kiracı başına > 40 özel rol,
  sözleşmesel politika dosyası talebi).
- **Roller:** 6 rol — 2 platform (`superadmin`, `platform_admin`), 4 kiracı
  (`tenant_admin`, `tenant_officer`, `dept_manager`, `employee`). Hiyerarşi
  izin devralma **yaratmaz**; yalnızca yönetim ilişkisini belirler.
- **Kapsam kodları:** `SYS`, `TENANT`, `UNIT`, `UNIT_DIRECT`, `ASSIGNED`, `SELF`;
  etkin kapsam = en dar olan. Rol kapsamı kişi bazında daraltılabilir,
  genişletilemez.
- **Permission matrisi:** rol özeti (gör/oluştur/değiştir/sil/onayla/sınır) +
  9 modül için ayrıntılı matris; SUPERADMIN ve ADMIN kişisel veri sütunlarında
  `GRANT_VALID`/`—` ile sınırlandı.
- **Kişisel veri:** K0–K4 sınıflandırması; varsayılan maskeleme (sunucu tarafı);
  talep → onay → süreli kullanım → kayıt akışı; boş gerekçeyle erişim yok.
- **Genel kimlik:** `kiracı-XXXXXXXX` (Crockford Base32, 8 rastgele karakter).
  Sıralı `00XXXX` biçimi **reddedildi**: numaralandırmaya açık.
- **Evrak:** yüklemede "kişisel veri içeriyor mu?" + gizlilik düzeyi + saklama;
  "emin değilim" = en kısıtlayıcı politika. "Varlığı bilmek" ≠ "içeriği görmek".
- **Denetim kaydı:** append-only (mevcut ✅), kişisel verinin değerini taşımaz;
  `purpose`, `grant_id`, `changed_fields` eklenecek; zincirleme önerildi.
- **Platform erişimi:** kiracı izolasyonunun tek istisnası; süreli (≤60 dk),
  gerekçeli, çift kontrol, kiracıya görünür ve kayıtlı.

### Mimari açıdan kritik tespit

Mevcut kod ile yeni tasarım arasında **üç çelişki** var; hiçbiri kod yazılmadan
çözülmeli:

1. **Platform kullanıcıları barındırılamıyor** (OD3): `users.tenant_id` zorunlu
   ve RLS korumalı; SUPERADMIN/ADMIN hiçbir kiracıya ait olmamalı. Öneri: ayrı
   `platform_users` tablosu + ayrı giriş yolu (kiracı politikaları hiç değişmez).
2. **Kiracılar arası hiçbir okuma yolu yok**: RLS + FORCE tüm tablolarda etkin;
   SUPERADMIN için koşullu, onay kaydına bağlı politika bayrağı tasarlandı (OD1).
3. **Rol kodları değişiyor**: `manager` → `dept_manager`, `tenant_officer`
   ekleniyor; veri migration'ı gerekiyor (uygulanmış migration değiştirilmez).

### Doğrulanan

- Dokümanların mevcut koda uygunluğu ölçümle kontrol edildi: RLS uygulanan
  tablolar (`tenants`, `users`, `roles`, `role_permissions`, `user_roles`,
  `user_permission_overrides`, `audit_logs`), mevcut 29 izin kodunun tam listesi
  ve mevcut rol şablonları (`tenant_admin`, `hr_manager`, `manager`, `employee`)
  ile karşılaştırıldı; tasarım belgesinde her madde ✅/🔜/⚠️/❓ olarak işaretlendi.
- Kod değişmediği için test koşusu gerekmedi (önceki durum: 97 test yeşil).

### Kalan (kod aşaması)

`docs/AUTHORIZATION.md` bölüm 13'teki 12 iş kalemi ve bölüm 14'teki kararlar.
Engelleyici kararlar: **OD3** (platform kullanıcı barındırma), **OD1**
(SUPERADMIN kişisel veri erişimi), **OD4** (kişisel veri erişimini kim onaylar).

## [0.6.0] — 2026-09-11 — Faz 2 (2. dilim): istek hattı, kimlik doğrulama uçları, MFA ve token modeli

Bu dilimde uygulama ilk kez **çalışan bir giriş akışına** kavuştu: istek hattı
kiracı bağlamını kuruyor, oturum ve token uçları parola + MFA doğruluyor, izinler
kataloğu ve varsayılan roller veritabanında. **Yedi gerçek hata** ölçümle
bulundu ve düzeltildi; biri (2 numaralı bulgu) üretimdeki TÜM girişleri
kırıyordu.

### Eklenen — istek hattı

- `apps/tenancy/middleware.py`:
  - `RequestIdMiddleware` — her isteğe kimlik verir, `X-Request-ID` döner.
  - `TenantContextMiddleware` — kiracıyı OTURUMDAN, yoksa ALAN ADINDAN çözer ve
    istek boyunca bağlamı kurar. Oturum ile alan adı uyuşmazsa istek reddedilir
    (oturum taşıma saldırısına karşı).
- `apps/accounts/middleware.py`: `ApiTokenTenantMiddleware` — token ile gelen
  isteklerde kiracıyı token'dan çözer.
- SIRA: üçü de `AuthenticationMiddleware`'ten ÖNCE çalışır (aksi halde oturumdan
  kullanıcı yüklenemez; RLS satırı gizler).

### Eklenen — kiracı çözümleme

- `Tenant.slug` (tekil) ve `Tenant.domain` (tekil, opsiyonel).
- `hr.tenant_directory`: slug/alan adı → kiracı kimliği eşlemesi. **RLS dışıdır**
  (gerekçe aşağıda), uygulama rolü yalnızca OKUR; dizini tetikleyici
  (`hr.sync_tenant_directory`, SECURITY DEFINER) günceller. Böylece bir kiracı
  başkasının alan adı eşlemesini ele geçiremez.
- `apps/tenancy/context.py`: `tenant_id_by_slug`, `tenant_id_by_domain`.

### Eklenen — kimlik doğrulama uçları (`/api/v1/auth/`)

| Uç nokta | Yöntem | Notlar |
|---|---|---|
| `login` | POST | Oturum çerezi (HttpOnly) + **CSRF korumalı** |
| `logout` | POST | Oturumu kapatır |
| `token` | POST | API token üretir (çerez yok, CSRF gerekmez) |
| `token/logout` | POST | Yalnızca o cihazın token'ını iptal eder |

- Hata mesajları GENEL tutulur (kullanıcı/kiracı numaralandırmasına karşı);
  "MFA gerekli" bilgisi yalnızca parola doğruysa döner.
- Kimlik doğrulama uçları en katı hız sınırı sınıfındadır (`throttle_scope="auth"`).
- Ayrıca `/api/health` (canlılık) ve `/api/ready` (veritabanı hazır olma) uçları.

### Eklenen — MFA (TOTP)

- `apps/accounts/mfa.py`: kod doğrulama (`valid_window=1`), kayıt için anahtar
  üretimi. TOTP sırrı veritabanında **şifreli** saklanır
  (`FIELD_ENCRYPTION_KEY`, Fernet; `apps/common/crypto.py`).
- MFA, OTURUM ve TOKEN yollarının İKİSİNDE de uygulanır (ortak yardımcı).
- Anahtar yoksa/çözülemezse giriş reddedilir (fail-closed).

### Eklenen — API token modeli (knox kaldırıldı)

- `apps/accounts/models.py`: `ApiToken` — SHA-256 özeti, zorunlu süre, etiket,
  son kullanım, cihaz bazlı iptal. Düz metin token **saklanmaz**.
- `apps/accounts/authentication.py`: `ApiTokenAuthentication` (401 dönebilmesi
  için `authenticate_header` tanımlı).
- `django-rest-knox` bağımlılıktan **çıkarıldı** ve knox tablosu migration ile
  düşürüldü.

### Eklenen — RBAC kataloğu ve varsayılan roller

- `apps/rbac/permission_catalog.py` + `rbac.0003_seed_permissions` (29 izin kodu).
- `create_default_roles(tenant)`: `tenant_admin`, `hr_manager`, `manager`,
  `employee`. Kataloğda olmayan izin sessizce atlanmaz, hata verir.
- `apps/rbac/permissions.py`: `HasPermissionCodes` (**varsayılan reddet**; izin
  listesi tanımsızsa erişim verilmez) ve `AnyPermissionCode`.

### Eklenen — altyapı

- `CACHES`: veritabanı cache'i (paylaşımlı; hız sınırlama sayaçları için gerekli).
  Tablo `createcachetable` ile oluşur; `scripts/migrate.sh` bunu çalıştırır.
- `SESSION_ENGINE`: veritabanı oturumları.

### Verilen kararlar

- **K15** — API token'ları için üçüncü parti paket kullanılmaz; kendi `ApiToken`
  modelimiz kullanılır (gerekçe: bulgu 3).
- **K16** — Giriş ÖNCESİ okunması gereken eşleme tabloları RLS dışıdır, ancak
  sıkı kurallarla: `tenant_directory` yalnızca okunur (yazma tetikleyicide);
  `api_tokens` yalnızca özet + kiracı kimliği tutar, düz metin token saklamaz.
- **K17** — Cache veritabanı üzerinden (süreç-yerel bellek yerine), Redis yok.
- Kiracı çözümleme sırası: oturum → alan adı → token. Çakışma (oturum ≠ alan adı)
  reddedilir.

### Doğrulanan (gerçek çalıştırma)

- **pytest: 97 test geçti** (önceki 57 → +40). Yeni testler: kiracı bağlamı ve
  alan adı çözümleme, oturum/token girişi, CSRF, kilitlenme, MFA (kodsuz/yanlış/
  doğru kod, token yolunda da MFA), token hash/süre/iptal, izin sınıfı (izin yok →
  403; izin tanımsız uç → 403), varsayılan roller, hız sınırı, kiracı dizini
  güvenliği.
- **Sıfırdan kurulum doğrulandı**: boş bir veritabanında `migrate` çalıştırıldı;
  7 kiracıya bağlı tabloda `rls=true force=true`, `api_tokens` ve
  `tenant_directory` bilinçli olarak RLS dışı, uygulama rolü `audit_logs` UPDATE
  ve `tenant_directory` INSERT yapamıyor.
- **Uçtan uca duman testi (`scripts/smoke.sh`)**: gerçek HTTP + gerçek veritabanı
  üzerinden demo kiracı/kullanıcı oluşturuldu (4 rol, 29 izin), `/api/health` ve
  `/api/ready` yanıt verdi, `POST /api/v1/auth/token` ile gerçek giriş yapıldı ve
  token alındı, geçerli token ile çıkış `204`, geçersiz token `401` döndü.
- `ruff`, `bandit` (0 bulgu), `manage.py check`,
  `makemigrations --check` ("No changes detected") temiz.

### Bulgular (ölçümle bulunan ve düzeltilen gerçek hatalar)

0. **`tenant_context` gerçek ortamda bağlamı KAYBEDİYORDU (en kritik bulgu).**
   Ayarı kuran işlem, dış bir işlem yoksa hemen commit oluyor ve `SET LOCAL`
   ayarı düşüyordu; yani her sorgu bağlamsız kalıp fail-closed (0 satır)
   dönüyordu. Testler pytest'in dış işlemi sayesinde bu hatayı MASKELİYORDU —
   yalnızca gerçek HTTP + gerçek veritabanı ile yapılan duman testi yakaladı.
   `tenant_context` artık işlemi `yield` boyunca açık tutuyor. (Bu, gerçek
   isteklerde ürünün tamamen çalışmaz olması demekti.)
1. **FORCE RLS, SECURITY DEFINER fonksiyonu da bağlıyor.** Kiracıyı slug ile
   çözen fonksiyon, tablo sahibi olarak çalışsa bile satırı göremedi (FORCE tablo
   sahibini de kapsar). Çözüm: RLS dışı, salt-okunur **dizin tablosu** (K16).
2. **`login()` sinyali kiracı bağlamı dışında çalışıyordu** → kullanıcı satırını
   güncelleyen sinyal 0 satır etkiliyor ve "Save with update_fields did not affect
   any rows" hatası veriyordu. Yani üretimde **hiçbir oturum girişi çalışmazdı.**
   Oturum kurulumu `tenant_context` içine alındı.
3. **knox, RLS ile çalışmıyor**: token doğrularken kullanıcıyı `select_related`
   ile çekiyor, o anda kiracı bağlamı olmadığı için RLS satırı gizliyor ve token
   asla doğrulanamıyordu (401). Kendi token modelimize geçildi (K15).
4. **Aynı tuzak kendi modelimizde tekrarlandı**: `select_related("user")`
   kullanınca token yine doğrulanamadı; ilişki yalnızca bağlam kurulduktan sonra
   yükleniyor.
5. **`authenticate_header` eksikti** → DRF kimlik doğrulama hatalarında 401 yerine
   403 dönüyordu.
6. **Uygulanmış migration'a sonradan sütun eklenmişti** (`tenancy.0002` içindeki
   `role_permissions`), bu yüzden **sıfırdan kurulum bozuluyordu**: temiz bir
   veritabanında RLS, `role_permissions.tenant_id` oluşmadan çalışıyordu. Dosya
   ilk hâline döndürüldü; RLS ayrı migration'da (`0004`). Sıfırdan kurulum artık
   ayrıca test ediliyor.
7. **SlugField'ın `db_index` davranışı**: iki adımlı alan eklemede PostgreSQL'in
   `_like` indeksi iki kez yaratılmaya çalışıldı ("relation already exists").
   İlk adım `CharField`, son adım `SlugField` olacak şekilde düzeltildi.
8. **`fetchone()` boş sonuçta TypeError** → eşlemesi olmayan HER istekte 500
   (ör. localhost). Boş sonuç artık normal kabul ediliyor.
9. **`CACHES` tanımsızdı** → hız sınırlama sayaçları süreç-yerel bellekte
   tutuluyordu; çok işçili kurulumda tutarsız olurdu. Veritabanı cache'ine
   geçildi (K17).
10. **DRF hız oranları sınıf özniteliğine bağlanır**; ayar değiştirmek yetmiyor.
    Testler bu yüzden yanlış kurulmuştu; test kurulumu düzeltildi.
11. **`check --deploy`**: üretilen secret 43 karakterdi (50'den kısa). Üretim artık
    64 karakterlik secret üretir.

### Kalan (Faz 2'nin 3. dilimi)

- Parola sıfırlama akışı ve e-posta gönderimi (kuyruk üzerinden).
- MFA **kayıt** ucu (QR/anahtar gösterimi) ve kapatma akışı.
- Süresi geçmiş oturum/token temizliği (periyodik görev), kullanıcı CRUD uçları.
- Nesne seviyesi yetki kapsamı (kendi kaydı / kendi ekibi) — organizasyon
  modülüyle birlikte (faz 3).

---

## [0.5.0] — 2026-09-11 — Faz 2 (1. dilim): veri modeli, ilk migration, RLS ve kimlik doğrulama çekirdeği

Bu sürümde ilk kez uygulama modelleri ve migration'lar yazıldı; veritabanı
şemasının sahibi artık Django migration'larıdır (`docs/ARCHITECTURE.md` bölüm 5.2).
Ayrıca kimlik doğrulama ve RBAC çekirdeği kuruldu. **Dört gerçek hata testler
tarafından yakalandı** (aşağıda "Bulgular").

### Eklenen — uygulamalar

- `apps/common/models.py` — soyut temeller: `UUIDModel`, `TimeStampedModel`,
  `SoftDeleteModel`, `TenantScopedModel`.
- `apps/tenancy/` — `Tenant` modeli (+`TenantStatus`), kiracı bağlamı yönetimi
  (`apps/tenancy/context.py`: `tenant_context`, `require_tenant_id`,
  `TenantContextMissingError`).
- `apps/accounts/` — özel `User` modeli (e-posta ile giriş, zorunlu `tenant`,
  kilit/MFA/token alanları, `UserManager`) ve `TenantScopedModelBackend`.
- `apps/rbac/` — `Role`, `Permission`, `RolePermission`, `UserRole`,
  `UserPermissionOverride` + `services.py` (etkin izin çözümlemesi).
- `apps/audit/` — `AuditLog` (+`AuditAction`) ve `services.py` (`log_action`,
  `masked_changes`).

### Eklenen — migration ve veritabanı güvenliği

- `tenancy.0001_initial` — `hr` şemasını ve `hr.current_tenant_id()` fonksiyonunu
  oluşturur (tablolar oluşmadan önce).
- `tenancy.0002_enable_rls` — kiracıya bağlı 7 tabloda **RLS + FORCE** ve
  `tenant_isolation` politikası.
- `tenancy.0003_same_tenant_guard` — **kiracılar arası referans guard'ı**:
  `hr.ensure_same_tenant_ref()` fonksiyonu ve 8 tetikleyici. RLS yalnızca satırın
  kendi `tenant_id`'sini denetlediği için, başka kiracının nesnesine bağlanan
  satırlar bu tetikleyicilerle engellenir.
- `rbac.0002_rolepermission_tenant` — `role_permissions` tablosuna zorunlu
  `tenant_id` eklendi (bkz. Bulgular #2).
- `tenancy.0004_rls_role_permissions` — yukarıdaki tablonun RLS'i (bkz. Bulgular #4).
- `scripts/migrate.sh` — migration'ları **sahip rolle** çalıştırır ve ardından
  uygulama rolü yetkilerini tazeler (uygulama rolünün `hr` şemasında DDL yetkisi
  yoktur; bu bilinçlidir).

### Verilen kararlar

- **E-posta kiracı başına tekildir**, global tekil değil. Bu nedenle Django'nun
  `ModelBackend`'i kullanılamaz; kendi backend'i yazıldı ve `auth.E003`/`auth.W004`
  denetimleri bilinçli olarak susturuldu.
- **Kimlik doğrulama kiracı bağlamı içinde çalışır** (RLS nedeniyle zorunludur).
- **Kiracı, isteğin sorumluluğundadır**: istemciden gelen gövde/parametre ile
  belirlenmez; oturum/alan adı üzerinden çözümlenir ve `authenticate()`'e açıkça
  verilir. Kiracı çözümlenemezse giriş yapılmaz (fail-closed).
- **`permissions` tablosu globaldir** (kiracı verisi içermez, RLS dışıdır);
  diğer tüm kiracıya bağlı tablolar `tenant_id` taşır ve RLS'lidir.

### Doğrulanan (gerçek çalıştırma, PostgreSQL 18.6)

- `manage.py migrate`: 4 uygulamanın migration'ları gerçek kümede uygulandı;
  `makemigrations --check`: **"No changes detected"** (model ile migration uyumlu).
- Gerçek kümede sorgulanarak doğrulandı: `tenants`, `users`, `roles`,
  `role_permissions`, `user_roles`, `user_permission_overrides`, `audit_logs`
  tablolarında `rls=true force=true`; `permissions` bilinçli olarak RLS dışı;
  8 guard tetikleyicisi kurulu.
- `audit_logs` yetkileri: uygulama rolü `SELECT=true INSERT=true UPDATE=false
  DELETE=false` → denetim kaydı **değiştirilemez**.
- **pytest: 57 test geçti** (önceki 19 → +38). Yeni testler:
  `tests/test_tenant_isolation.py` (yapısal RLS denetimi, fail-closed davranış,
  çapraz kiracı okuma/yazma/güncelleme/silme reddi, iç içe bağlam, guard'lar,
  denetim kaydı yetkileri) ve `tests/test_auth_rbac.py` (izin çözümlemesi,
  allow/deny istisnaları, süresi geçmiş rol, giriş/kilitlenme/pasif kullanıcı,
  denetim kayıtları).
- `ruff`, `bandit`, `manage.py check`: temiz.

### Bulgular (testlerin yakaladığı gerçek hatalar)

1. **Kimlik doğrulama kiracı bağlamı olmadan çalışıyordu** → RLS kullanıcıyı
   göstermediği için hiç kimse giriş yapamazdı. Backend, tüm akışı
   `tenant_context` içine alacak şekilde düzeltildi.
2. **`role_permissions` tablosu çapraz kiracı açığıydı.** İlk tasarımda "tanım
   tablosu" sayılıp RLS dışı bırakılmıştı; A kiracısı bağlamında B kiracısının
   rolüne izin kaydı eklenebiliyordu. Tabloya zorunlu `tenant_id` eklendi, RLS'e
   alındı. Kural sadeleşti: kiracıya bağlı her tablo `tenant_id` taşır.
3. **RLS tek başına çapraz kiracı referansı engellemiyor.** Yabancı anahtar
   kontrolü RLS'ten etkilenmediği için, `tenant_id` doğru olsa bile satır başka
   kiracının nesnesine bağlanabiliyordu. Guard tetikleyicileri eklendi (#2 ve #3
   aynı kök nedeni paylaşır: "tanım tablosu" varsayımı).
4. **Uygulanmış bir migration dosyası sonradan düzenlenmişti.** `role_permissions`
   RLS'i ilk migration'ın listesine eklenmiş, ancak o migration zaten
   uygulandığı için veritabanına yansımamıştı. Düzeltme ayrı bir migration ile
   yapıldı (`tenancy.0004_rls_role_permissions`). Kural: uygulanmış migration
   dosyası değiştirilmez; düzeltme yeni migration'la gelir.
5. Test kaynaklı yanlış varsayımlar da düzeltildi: beklenen veritabanı hataları
   savepoint içinde üretilmeli (aksi halde işlem kilitleniyor) ve `refresh_from_db`
   kiracı bağlamı gerektirir.

### Kalan (Faz 2'nin 2. dilimi)

- **İstek hattı (middleware)**: kiracı bağlamını istek başına kurmak —
  `AuthenticationMiddleware`'ten ÖNCE çalışmalıdır, aksi halde oturumdan kullanıcı
  yüklenemez (RLS). Ayrıca `request_id` üretimi.
- **Görünümler ve API**: giriş/çıkış uçları, DRF yetki sınıfı (`required_permissions`),
  knox token uçları, OpenAPI şeması.
- **MFA (TOTP)**: alan modelde hazır; kayıt/doğrulama akışı ve testleri yazılacak.
- **Parola sıfırlama** uçları (`password_reset_tokens`), e-posta gönderimi.
- Örnek izin kodları ve varsayılan rollerin veri migration'ı ile eklenmesi.

---

## [0.4.0] — 2026-09-11 — Faz 1: veritabanı, rol ayrımı ve kiracı izolasyonu (RLS)

PostgreSQL 18.6 kullanıcı tarafından kuruldu (sudo gerektirdiği için ajan
kuramadı). Bu sürümde veri katmanının güvenlik temeli kuruldu ve
**çalıştırılarak kanıtlandı**.

### Eklenen

- `scripts/01_bootstrap.sql` — roller (`persopyro_owner`, `persopyro_app`),
  veritabanı, `pgcrypto`. Süper kullanıcı çalıştırır, idempotent.
- `scripts/02_grant_app_privileges.sql` — `hr` şeması, şema yetkileri, varsayılan
  yetkiler (tablo/sequence/fonksiyon/**tip**), mevcut nesnelerin yetkileri,
  denetim tablosu koruması. **Saf SQL** (psql'e özgü komut yok), bu yüzden test
  veritabanında da (conftest) çalıştırılabilir.
- `scripts/03_verify_isolation.sql` — kiracı izolasyonu kanıtı: A1–A9 iddiaları,
  başarısızlıkta `RAISE EXCEPTION`. Geçici `spike` şeması kurar ve siler; `hr`
  şemasına dokunmaz.
- `scripts/bootstrap.sh` — tek komutla kurulum: roller/DB/şema/yetkiler →
  rastgele parola üretimi → `.env` yazımı → izolasyon doğrulaması → uygulama
  rolüyle TCP bağlantı sınaması (parola + `search_path`).
- `scripts/run_tests.sh` — testleri sahip rolüyle çalıştırır (test veritabanı
  oluşturma yetkisi uygulama rolünde bilinçli olarak yoktur).
- `tests/conftest.py` — test veritabanında `hr` şemasını ve uygulama rolü
  yetkilerini uygular (geçiş dönemi çözümü; faz 2'de ilk migration devralacak).
- `tests/test_database_roles.py` — 8 test: uygulama rolü superuser değil, RLS'i
  atlayamaz, veritabanı/rol oluşturamaz, `hr`/`public` şemasında DDL yapamaz,
  `hr` şemasını kullanabilir; tenant bağlamı fail-closed; `search_path` doğru;
  roller ayrı.
- `config/settings.py`: `.env` yükleyici (ek bağımlılık yok, gerçek ortam
  değişkenleri kazanır), `DJANGO_ENV_FILE` ile dosya yolu geçersiz kılınabilir;
  `APP_DB_ROLE` / `OWNER_DB_ROLE` ayarları.

### Verilen kararlar

- **Kimlik doğrulama:** kimlik doğrulama uygulama katmanında kalır;
  veritabanındaki `crypt()` tabanlı fonksiyon üretimde kullanılmaz (önceki sürüm
  kararı doğrulandı).
- **`uuid-ossp` eklentisi gerekmez:** `gen_random_uuid()` PostgreSQL 13+ ile
  çekirdekte. Mevcut tasarımın `uuid-ossp` kullanımı sadeleştirildi.
- **Tenant bağlamı işlem başına** (`set_config(..., is_local=true)`) kurulur;
  oturum boyunca kalan (`false`) kullanım yasaktır.
- **Testler sahip rolüyle çalışır**, uygulama rolüne `CREATEDB` verilmez;
  izolasyon `FORCE RLS` sayesinde yine bağlayıcıdır.

### Doğrulanan (gerçek çalıştırma, PostgreSQL 18.6)

Geçici, ajan tarafından yönetilen bir küme (port 5433, `initdb` ile kuruldu ve
iş bitince silindi) üzerinde:

- `scripts/bootstrap.sh` uçtan uca çalıştı; `SONUC: KIRACI IZOLASYONU
  DOGRULAMASI BASARILI (A1-A9)`.
- **A1** uygulama rolü superuser değil, `rolbypassrls=false`, `CREATEDB`/
  `CREATEROLE` yok, `hr` şemasında `CREATE` yok.
- **A2** uygulama rolü tablo sahibi değil.
- **A3** RLS etkin **ve FORCE etkin**.
- **A4** kiracı bağlamı yokken 0 satır (fail-closed).
- **A5** A kiracısı yalnızca kendi 1 kaydını görür.
- **A6** A kiracısı B kiracısı adına yazamaz (`WITH CHECK` ihlali engellendi).
- **A7** B kiracısının satırı güncellenemedi/silinemedi (0 satır etkilendi).
- **A8** işlem sonunda bağlam sıfırlandı.
- **A9** `FORCE RLS` sayesinde **tablo sahibi rol bile** kiracı sınırını aşamadı.
- Uygulama rolü TCP + parola (scram-sha-256) ile bağlandı; `search_path=hr,public`.
- `pytest`: **19 test geçti** (8'i veritabanı rol/izolasyon testi), gerçek
  PostgreSQL üzerinde; test veritabanı otomatik oluşturuldu ve silindi.
- `manage.py check`, `ruff`, `bandit -c pyproject.toml -r .`, `pip-audit`: temiz.

### Bulgular (düzeltilen gerçek hatalar)

- **PostgreSQL `SET LOCAL` sonrası ayar `NULL` değil boş string'e döner.**
  Bu yüzden tasarımdaki `NULLIF(current_setting('app.tenant_id', TRUE), '')`
  kullanımı zorunludur — süs değil. Kanıt A8'dedir.
- `GRANT ... ON ALL TYPES IN SCHEMA` **PostgreSQL'de yok**; tip yetkileri döngüyle
  verilir (`02_grant_app_privileges.sql`).
- `bootstrap.sh` içinde `:-` varsayılanı, açıkça boş verilen `SUPER_USER_CMD`
  değerini geçersiz kılıyordu; `-` kullanımına çevrildi.
- Dev `.env` dosyası varken "üretim çerezi güvenli mi" testi yanlış sonuç
  veriyordu; test artık ayarları temiz ortamda (`.env` devre dışı) yükler.
- Test veritabanında `hr` şeması ve yetkiler yoktu; conftest ile eklendi.

### Yapılmayan

- `manage.py migrate` çalıştırılmadı: özel kullanıcı modeli (`AUTH_USER_MODEL`)
  ilk migration'dan önce tanımlanmalıdır. Faz 2'nin ilk işidir.
- Uygulama modülleri (kiracı, kullanıcı, organizasyon) kodlanmadı.
- `~/hrsaas_db/` altındaki dosyalara dokunulmadı.

### Sıradaki adım

Kullanıcı sistem kümesinde `bash scripts/bootstrap.sh` çalıştırır; sonra Faz 2:
`tenancy` ve `accounts` uygulamaları, özel kullanıcı modeli, `hr` şemasını
oluşturan ilk migration, kimlik doğrulama + RBAC çekirdeği.

---

## [0.3.0] — 2026-09-11 — Faz 0: ortam kurulumu, depo ve test iskeleti

Karar: Python **3.13** ile devam (bkz. `ARCHITECTURE.md` karar kaydı K11).
Gerekçe: Django 5.2.8+ Python 3.14'ü desteklese de üçüncü parti paketlerin
uyumluluğu 3.13'te daha oturmuş durumda.

### Eklenen

- Python **3.13.15** sanal ortamı (uv; 3.13 zaten uv ile kuruluydu, indirme
  gerekmedi).
- `pyproject.toml` — bağımlılıklar kilitli: Django `~=5.2.0` (LTS), DRF,
  psycopg 3, django-rest-knox, django-q2, boto3, argon2-cffi, pyotp, gunicorn,
  drf-spectacular, sentry-sdk; geliştirme grubunda pytest + pytest-django +
  factory-boy + coverage + ruff + bandit + pip-audit.
- `uv.lock` — tekrarlanabilir kurulum için kilit dosyası.
- Django iskeleti: `manage.py`, `config/` (settings, urls, wsgi, asgi),
  `apps/` (uygulama modülleri için paket), `tests/`.
- `config/settings.py` — mimari kararlara göre yazıldı:
  - DEBUG varsayılan **kapalı** (üretim davranışı). `DJANGO_SECRET_KEY` her
    ortamda (geliştirme dahil) zorunludur; koda gömülü bir yedek değer **yoktur**,
    bu yüzden yanlışlıkla üretime taşınabilecek güvensiz bir varsayılan oluşamaz.
  - Veritabanı PostgreSQL'e sabit; bağlantı seçeneği olarak
    `search_path=hr,public` verildi (şema kararı uygulandı).
  - Uygulama rolü `persopyro_app` (tablo sahibi değil — RLS için kritik).
  - `PASSWORD_HASHERS`: Argon2id birincil, bcrypt uyumluluk amaçlı.
  - DRF: varsayılan izin `IsAuthenticated` (reddet); kimlik doğrulama için knox
    (opak token) + oturum; hız sınırları tanımlı.
  - django-q2: broker `orm` (Redis yok), geliştirmede senkron.
  - E-posta konsol backend'i (geliştirme), nesne depolama ve Sentry anahtarları
    ortamdan; Sentry DSN yoksa tamamen devre dışı.
  - DEBUG kapalıyken güvenlik başlıkları/çerez politikaları (HSTS, Secure,
    HttpOnly, SameSite, nosniff, DENY) otomatik uygulanır.
  - JSON formatlı loglama; kişisel veri loglanmaz.
- `tests/test_smoke.py` — veritabanına bağlanmayan 10 iskelet testi (güvenli
  varsayılanlar, PostgreSQL + hr şeması, Argon2id, reddet politikası, ORM
  broker, oturum çerezi güvenliği, admin URL çözümü, secret yoksa başlamama).
- `.env.example` — yalnızca anahtar isimleri (gerçek değer yok).
- `.gitignore` — `.env` ve türevleri, anahtarlar, sanal ortam, önbellekler hariç.

### Doğrulanan (gerçek çalıştırma)

- `manage.py check`: sorun yok (0 hata).
- `pytest`: **10 test geçti** (0,30 sn).
- `ruff check .`: temiz.
- `bandit -r config apps`: temiz (`# nosec` gerekmedi; güvensiz yedek secret
  tamamen kaldırıldığı için bulgu üretilmiyor).
- `pip-audit`: bilinen güvenlik açığı yok.
- Django sürümü: 5.2.17 (LTS), Python: 3.13.15.

### Yapılmayan / engellenen

- **PostgreSQL kurulmadı.** Kurulum `sudo` şifresi gerektirdiği için bu ortamda
  yapılamadı; komut `docs/ARCHITECTURE.md` bölüm 18'de kullanıcı için yazıldı.
  Bu nedenle `migrate` çalıştırılmadı, veritabanı oluşturulmadı.
- Uygulama modülü (kiracı, kullanıcı, organizasyon vb.) kodlanmadı; faz 1 ve
  sonrasına ait.
- Özel kullanıcı modeli (`AUTH_USER_MODEL`) henüz tanımlanmadı; ilk migration'dan
  önce ayarlanması gerektiği `config/settings.py` içinde not edildi.

### Sıradaki adım

Faz 1 (veritabanı spike'ı): PostgreSQL 17 kurulumu → `hr` şeması ve ayrı
roller (`persopyro_owner`, `persopyro_app`) → RLS + tenant bağlamı
(`SET LOCAL app.tenant_id`) → kiracılar arası erişimin reddedildiğini gösteren
ilk güvenlik testi.

---

## [0.2.0] — 2026-09-11 — Teknik mimari ve teknoloji seçimleri

### Eklenen

- `docs/ARCHITECTURE.md` tamamen yenilendi: teknoloji seçimleri, gerekçeler,
  reddedilen alternatifler, bağımlılık envanteri, karar kaydı, riskler ve
  uygulama fazları (bkz. içindekiler: bölüm 1 karar özeti, 3–16 teknoloji
  bölümleri, 19 bağımlılıklar, 20 karar kaydı, 21 riskler, 22 fazlar).

### Seçilen teknolojiler (özet)

- Backend: Python 3.13 + Django 5.2 LTS + Django REST Framework
- Frontend: Django şablonları + HTMX + Alpine.js + Tailwind CSS
- Veritabanı: PostgreSQL 17 (minimum 15), `hr` şeması, RLS (FORCE) + rol ayrımı
- Kimlik doğrulama: özel User modeli, Argon2id, HttpOnly oturum çerezi (web),
  opak ve hash'li token `django-rest-knox` (API); JWT kullanılmaz
- Yetkilendirme: mevcut RBAC tabloları; HTTP → servis → RLS üç katman
- Dosya saklama: S3 uyumlu özel nesne depolama + boto3 + ön imzalı URL
- Cache: Django DB cache; **Redis ilk sürümde yok**
- Arka plan görevleri: `django-q2` (Django ORM broker) + systemd worker
- E-posta: Django SMTP + sağlayıcı bağımsız relay, kuyruk üzerinden
- Denetim kaydı: `audit_logs` + tetikleyiciler, uygulama rolüne yalnızca INSERT/SELECT
- API: REST/JSON `/api/v1`, DRF + OpenAPI; idempotency anahtarı (kritik yazmalar)
- Test: pytest + pytest-django + factory_boy, gerçek PostgreSQL üzerinde
- Docker: üretimde kullanılmaz (systemd + venv); yerelde de zorunlu değil
- Deployment: tek Ubuntu LTS VPS, nginx + gunicorn + systemd + certbot + yedekleme

### Verilen kararlar (kod yazılmadan)

- Şemanın sahibi **Django migration'ları** olacak; mevcut `hrsaas_db` SQL
  dosyaları salt-okunur referans olarak korunacak, değiştirilmeyecek.
- Kimlik doğrulama **uygulama katmanında** yapılacak; veritabanı seviyesindeki
  `hr.authenticate_user` üretimde kullanılmayacak (parola veritabanına
  gönderilmeyecek).
- Uygulama ve migration için **ayrı veritabanı rolleri**; uygulama rolü tablo
  sahibi olmayacak (aksi halde RLS atlanır).
- Redis, Celery, Docker orkestrasyonu, Kubernetes, SPA sunucusu ve AI bağımlılığı
  bilinçli olarak **dışarıda**; her biri için yeniden değerlendirme tetikleyicisi
  tanımlandı (`ARCHITECTURE.md` bölüm 6.2 ve 20).
- Doğrulanması gereken teknik noktalar kayda geçti: RLS + bağlantı havuzu,
  PostgreSQL `ENUM` ile Django `TextChoices` farkı, `hr` şeması/`search_path`
  yaklaşımı (`ARCHITECTURE.md` bölüm 5.4).

### Değişen

- `docs/PROJECT.md`: durum "mimari planlandı"ya güncellendi; seçilen teknoloji
  yığını özeti, kapsam dışı maddeleri (Redis/Celery/Docker/K8s/AI/SSO) ve başarı
  ölçütleri (düşük maliyet, AI bağımsızlığı) eklendi.
- `docs/SECURITY.md`: parola/parola hash yaklaşımı Argon2id + uygulama katmanı
  kimlik doğrulamasına göre güncellendi; veritabanı rol ayrımı ve MFA zorunluluğu
  eklendi.
- `docs/DATABASE.md`: şema yönetimi kararı (Django migration'ları) ve kimlik
  doğrulama/parola hash kararı yansıtıldı; açık noktalar listesi güncellendi.
- `README.md`: durum satırı ve doküman açıklaması güncellendi.

### Doğrulanan (kaynak taraması)

- Django 5.2 **LTS**; en az 3 yıl güvenlik güncellemesi; Python 3.10–3.14
  destekliyor (5.2.8 itibarıyla).
- PostgreSQL güncel sürüm hattı: 18 (destekli 14–18); proje hedefi 17.
- `django-q2` aktif geliştiriliyor (v1.10.0, Mayıs 2026) ve Django ORM broker
  desteği ile Django 5.2 / Python 3.13 uyumlu.
- `django-rest-knox` (Jazzband, 5.1.0) token'ları hash'li saklıyor, süre
  sınırı ve cihaz bazlı iptal sunuyor.

### Doğrulanmayan / henüz test edilmedi

- Yığının kurulumu ve çalışması test edilmedi (paket kurulmadı, veritabanı
  oluşturulmadı, kod yazılmadı).
- RLS + tenant bağlamı davranışı gerçek PostgreSQL üzerinde denenmedi
  (ortamda `psql` kurulu değil).
- Maliyet aralığı sağlayıcıya göre doğrulanmalı.

### Yapılmayan (kapsam dışı bırakılan)

- Uygulama kodu, API, arayüz yazılmadı; paket kurulmadı; veritabanı
  oluşturulmadı. `~/hrsaas_db/` altındaki hiçbir dosya değiştirilmedi.

---

## [0.1.0] — 2026-09-11 — Proje başlangıcı ve dokümantasyon altyapısı

### Eklenen

- Persopyro projesi için çalışma alanı oluşturuldu: `/home/hakan/persopyro/`
- Proje dokümantasyon yapısı `docs/` altında kuruldu:
  - `PROJECT.md` — amaç, kapsam, durum, doküman dizini
  - `ARCHITECTURE.md` — hedef sistem mimarisi, katmanlar, tenant bağlamı
  - `SECURITY.md` — güvenlik ilkeleri, tehdit modeli, kontroller
  - `AUTHORIZATION.md` — kimlik doğrulama, RBAC, izin kodlama biçimi
  - `DATABASE.md` — mevcut `hrsaas_db` şemasının envanteri ve veri kuralları
  - `API.md` — API sözleşme kuralları ve kaynak grupları
  - `ORGANIZATION.md` — şirket/departman/pozisyon/çalışan yapısı
  - `FEATURES.md` — özellik listesi, durumları ve öncelik sırası
  - `DEVELOPMENT_RULES.md` — zorunlu geliştirme kuralları
  - `CHANGELOG.md` — bu dosya
- Kök dizine `README.md` eklendi (proje tanıtımı ve doküman bağlantıları).

### Kararlar (dokümana yazılan, kod henüz yok)

- Çok kiracılılık modeli: paylaşımlı veritabanı, `hr` şeması, satır bazlı
  izolasyon (`tenant_id`) + PostgreSQL RLS.
- İzolasyon üç katmanda uygulanır: API, servis, veri katmanı (RLS son savunma).
- Kiracı bağlamı istemciden gelen veriye göre belirlenmez.
- Yetkilendirme: RBAC + kullanıcı bazlı istisna; varsayılan davranış reddetmek.
- Kişisel veriler varsayılan görünümde maskelenir.
- Uygulama dili/çatısı henüz seçilmedi — karar bekliyor.

### Doğrulanan

- Çalışma ortamı: Ubuntu 26.04.1 LTS, git 2.53.0, Node v26.8.1, npm 11.19.0,
  Python 3.14.4, uv 0.12.10.
- Bulunmayan araçlar: `psql` (PostgreSQL istemcisi), docker, cargo, go.
- Mevcut varlık: `~/hrsaas_db/` içinde 12 SQL dosyası ve
  `README_DATABASE.md` (yaklaşık 90 tablo, 16 enum, RLS ve audit fonksiyonları).
- `~/ckay/` dizini bu projeyle ilgisizdir (ayrı bir Python sanal ortamı).

### Değiştirilmeyen

- `~/hrsaas_db/` altındaki tüm dosyalar olduğu gibi bırakıldı.
- Hiçbir dosya silinmedi veya taşınmadı.

### Yapılmayan (kapsam dışı bırakılan)

- Uygulama kodu, veritabanı oluşturma, API ve arayüz kodlaması yapılmadı.

### Sıradaki adımlar

1. Uygulama dili ve çatısının seçilmesi (`ARCHITECTURE.md` tablosunun
   güncellenmesi).
2. `hrsaas_db` şemasının gerçek bir PostgreSQL örneğinde doğrulanması
   (bu ortamda istemci kurulu değil).
3. Uygulama veritabanı rolü ve `GRANT` planının yazılması; RLS
   etkinleştirilecek tablo listesinin netleştirilmesi.
4. Sürüm kontrolü kurulumu (git deposu başlatılması) — kullanıcı onayı ile.
