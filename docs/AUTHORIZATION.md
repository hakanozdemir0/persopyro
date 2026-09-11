# Yetkilendirme ve Erişim Kontrolü — Persopyro

Bu belge, Persopyro'nun yetkilendirme (authorization) ve erişim kontrol
sisteminin **tasarımıdır**. Kod içermez; uygulama kararlarını ve sözleşmeleri
tanımlar.

**Belge işaretleri**

| İşaret | Anlamı |
|---|---|
| ✅ | Tasarlanan yapı **hâlihazırda kodda uygulanmış** ve test edilmiş |
| 🔜 | Tasarlandı, **henüz kodlanmadı** (uygulanacak) |
| ⚠️ | Mevcut kodla **çelişiyor**; uygulanmadan önce karar gerekiyor |
| ❓ | **OPEN DECISION** — belirsiz/çelişkili nokta (bkz. bölüm 14) |

İlgili belgeler: `docs/SECURITY.md` (güvenlik kontrolleri ve tehdit modeli),
`docs/DATABASE.md` (RLS, tetikleyiciler, tablo yapısı), `docs/ARCHITECTURE.md`
(karar kaydı K1–K17), `docs/DEVELOPMENT_RULES.md` (bağlayıcı kurallar).

---

## 1. Kapsam, ilkeler ve değişmezler

### 1.1 Kapsam

Bu belge şunları tanımlar: rol modeli, izin (permission) yapısı, kapsam (scope)
modeli, kiracı izolasyonu, kişisel veriye erişim, genel (public) kullanıcı
kimliği, evrak erişim politikası ve denetim kaydı.

Kapsam dışı: kimlik doğrulama mekaniği (bkz. `docs/SECURITY.md` bölüm 3),
ağ/altyapı güvenliği, KVKK hukuki metinleri.

### 1.2 Değişmez ilkeler

Bu ilkeler tartışmaya kapalıdır; her yeni özellik bunlara uymak zorundadır.

1. **Varsayılan reddet (deny by default).** İzin verilmediği sürece erişim
   yoktur. Yeni bir izin kodu eklendiğinde hiçbir rol onu otomatik almaz.
2. **En az yetki (least privilege).** Bir rol, işini yapmak için gereken en dar
   izin ve en dar kapsamla çalışır.
3. **Kiracı izolasyonu mutlaktır.** A şirketindeki hiçbir kullanıcı — rolü ne
   olursa olsun — B şirketinin verisine erişemez. Tek istisna, bölüm 3.4'te
   tanımlanan **platform erişimi**dir ve o da kayıt altındadır.
4. **Kişisel veri varsayılan olarak görünmez.** Gerçek ad/e-posta/telefon gibi
   alanlar varsayılan olarak maskelenir; açılması ayrı izin + gerekçe ister.
5. **İki katmanlı zorlama.** Yetki kontrolü hem uygulama katmanında (servis)
   hem veritabanında (RLS) yapılır. Uygulama katmanı atlanırsa veritabanı
   fail-closed davranır.
6. **Her önemli işlem denetim kaydına yazılır** (bkz. bölüm 10).
7. **Ayrılık ilkesi (separation of duties).** Talebi oluşturan kişi, kural
   olarak o talebi onaylayamaz.

---

## 2. Karar özeti: RBAC yeterli mi, ABAC gerekli mi?

### 2.1 Karar

**Hibrit model uygulanır: RBAC (rol bazlı) + kapsam (scope) + kapalı koşul
kümesi (conditions).**

| Katman | Soru | Örnek |
|---|---|---|
| **RBAC** | *Kim, hangi işlemi yapabilir?* | `leave.request.approve` |
| **Kapsam (scope)** | *Hangi kayıtlar üzerinde?* | yalnızca kendi departman alt ağacı |
| **Koşul (condition)** | *Hangi durumda geçerli?* | "yalnızca kendisine atanmış onay adımı" |

Üçü birlikte değerlendirilir ve **hepsinin geçmesi** gerekir:

```
izin verildi  ⟺  kod ∈ etkin izinler
              ∧  kapsam ⊆ rolün kapsamı ∧ kaydın kapsamı
              ∧  tüm koşullar sağlandı
```

### 2.2 Neden saf RBAC yetmez

Şu gereksinimler yalnızca izin koduyla ifade edilemez:

- "Çalışan **kendi** izin talebini görür" (kayıt sahipliği → `SELF`)
- "Departman yöneticisi **kendi biriminin alt ağacını** yönetir" (yapı → `UNIT`)
- "Onaycı yalnızca **kendisine atanmış** adımı onaylar" (ilişki → `ASSIGNED`)
- "Kişisel veri yalnızca **onaylı, süreli ve amaç bağlı** erişimle açılır"
  (politika → koşul + onay kaydı)
- "Evrak, yükleyenin işaretlediği **kişisel veri** durumuna göre erişilir"

Saf RBAC bunları ya ifade edemez ya da rol patlamasına (her ekip için ayrı rol)
yol açar; bu da denetlenebilirliği bozar.

### 2.3 Neden tam ABAC (politika motoru) değil

Reddedilen: OPA/Cedar benzeri ayrı bir politika motoru, politika dili ve kural
yazarlığı. Gerekçe: (a) tek VPS hedefi ve düşük bakım maliyeti ilkesi,
(b) politika yazarlığını ayrı bir uzmanlık alanına çevirir, (c) RLS'in
veritabanı seviyesindeki backstop'u zaten var.

Bunun yerine **kapalı bir koşul kümesi** tanımlanır (bölüm 5.4). Yeni koşul
türü eklemek bilinçli bir karardır; bu küme küçük tutuldukça sistem denetlenebilir
kalır.

**Fikri değiştirme tetikleyicileri (ölçülebilir):** (1) koşul kümesi 15'i aşarsa,
(2) kiracı başına 40'tan fazla özel rol oluşturulursa, (3) müşteri sözleşmesi
denetlenebilir politika dosyası talep ederse.

---

## 3. Kiracı izolasyon modeli

### 3.1 Üç katmanlı zorlama ✅

| Katman | Mekanizma | Atlatılırsa |
|---|---|---|
| 1. İstek hattı | `TenantContextMiddleware`: kiracı, oturumdan/alan adından/token'dan çözülür | Bağlam kurulmaz |
| 2. Uygulama | Servis katmanı kapsam kontrolü (`tenant_id`, `scope`) | Nesne seviyesi sızma |
| 3. Veritabanı | **RLS + FORCE**: `tenant_id = hr.current_tenant_id()` | 0 satır döner (fail-closed) |

Veritabanı katmanının garantisi: uygulama rolü (`persopyro_app`) tablo sahibi
değildir, `SUPERUSER` değildir, RLS'i atlayamaz. Kiracı bağlamı kurulmamışsa
sorgu **hata vermez, boş küme döner** — sızma yerine sessiz reddetme.

### 3.2 Kiracı sınırı neyi kapsar

Kiracıya bağlı tüm tablolar: `users`, `roles`, `role_permissions`,
`user_roles`, `user_permission_overrides`, `audit_logs`, `tenants` (kendi
satırı) ve ileride eklenecek tüm iş tabloları (organizasyon, çalışan, izin,
vardiya, evrak, süreç).

Ayrıca **kiracılar arası referans yasağı** ✅ vardır: kiracı A'nın kaydı, kiracı
B'nin kaydına yabancı anahtarla bağlanamaz (veritabanı tetikleyicisi ile
zorlanır). Bu, "yanlış FK" ile dolaylı veri sızmasını engeller.

### 3.3 Kiracı çözümleme ve uyuşmazlık ✅

Sıra: oturum → alan adı → API token. Oturumdaki kiracı ile alan adının işaret
ettiği kiracı farklıysa istek **reddedilir** (400).

Kullanıcı adı (e-posta) kiracı **başına** benzersizdir; aynı e-posta farklı
şirketlerde farklı kişiler olabilir. Giriş, kiracı açıkça belirtilmeden yapılmaz.

### 3.4 Platform erişimi: ayrı ve kontrollü katman 🔜 (OD1/OD3 — KARAR VERİLDİ)

SUPERADMIN ve ADMIN'in kiracılar arası işlem yapabilmesi gerekir; ancak bu,
kiracı izolasyonu **gevşetilerek** yapılmaz. Karar (OD1 ve OD3 kapalı):

1. **Kiracı RLS politikaları hiçbir şekilde gevşetilmez.** Politikada platform
   istisnası (koşullu bayrak vb.) **yoktur**; `tenant_id = hr.current_tenant_id()`
   ifadesi olduğu gibi kalır. `BYPASSRLS` yetkili rol **kullanılmaz** (kesin yasak).
2. **Platform kullanıcıları kiracı tarafında barındırılmaz.** Ayrı bir
   `platform_users` modelidir; kiracı kullanıcı tablosuyla **birleştirilmez**,
   `tenant_id` taşımaz. Zorla birleştirme veya teknik bir "platform kiracısı"
   satırı açma yaklaşımı yasaktır.
3. **Platform erişimi ayrı ve kontrollü bir servis/authorization katmanından
   geçer.** Bu katman kiracı istek hattından tamamen ayrıdır ve **ayrı bir
   veritabanı rolüyle** (`persopyro_platform`) bağlanır. Bu rol de süper kullanıcı
   değildir ve RLS'i atlayamaz.
4. **Kiracı bağlamını yalnızca bu katman kurabilir:** geçerli, süreli bir erişim
   onayı (`AccessGrant`) doğrulandıktan sonra, **tek bir işlem** için kiracı
   bağlamı hedef kiracıya ayarlanır. RLS devrede kaldığı için sorgu yine yalnızca
   o kiracının satırlarını görür — değişen şey politika değil, **bağlamı kimin
   kurabildiğidir**.
5. **Varsayılan erişim yoktur (fail-closed).** Onay yoksa, süre dolmuşsa veya
   kapsam dışıysa platform kullanıcısı kiracı verisini **göremez**. Kişisel veri
   için varsayılan **maskeli** görünümdür; maskesiz erişim bölüm 7.3'e tabidir.
6. **Süre ve otomatik kapanma:** erişim varsayılan 60 dakikadır; süre dolduğunda
   **otomatik kapanır**. Uzatma yeni bir onay gerektirir.
7. **Kayıt:** talebin açılması, onay/red, erişim başlangıcı ve **erişim bitişi**
   ayrı ayrı denetim kaydına yazılır (gerekçe dahil).
8. **Kiracı görünürlüğü:** kiracı, kendi verisine yapılan platform erişimini kendi
   denetim akışında görür (bölüm 10.3).

**Verinin yeri.** Platformun kendi verisi (platform kullanıcıları ve rolleri,
kiracı kayıt defteri/limitleri, erişim talepleri ve onayları, platform denetim
akışı) kiracı şemasının **dışında, ayrı bir platform şemasında** tutulur; kiracı
uygulama rolünün (`persopyro_app`) bu şemada hiçbir yetkisi yoktur. Böylece bir
kiracı isteğinden platform verisine ulaşan bir yol oluşamaz. Aynı şekilde platform
rolü, kiracı tablolarında yalnızca RLS'e tabi normal erişime sahiptir.

**Sonuç.** Kiracı yolu ile platform yolu; iki ayrı veritabanı rolü, iki ayrı şema
ve iki ayrı yetki kümesiyle ayrılır. Kiracı izolasyonunun garantisi (RLS + FORCE)
değişmez; platform tarafındaki risk, uygulama katmanındaki talep + onay + süre +
kapsam kontrolleri ve istisnasız denetim kaydıyla yönetilir.

**Kalan artık risk (bilinçli):** platform kullanıcısı bir kez bağlam kurduğunda
veritabanı onu "platform" olarak ayırt edemez (RLS açısından kiracı gibi görünür).
Ayrıt edicilik bu yüzden uygulama katmanındadır: ayrı rol kimlik bilgisi, tek
işlem kapsamı, her kullanımda kayıt, kiracıya görünürlük. Ek olarak platform
katmanı kiracı istek hattından **çağrılamaz** (tek yönlü bağımlılık; bölüm 12'de
test edilir).

### 3.5 İzolasyonun test ile güvencesi ✅

Mevcut testler: kiracı A kullanıcısı ile B'nin kayıtlarının görünmediği,
bağlamsız sorgunun 0 satır döndüğü, tablo sahibinin bile FORCE RLS'i aşamadığı,
kiracılar arası FK kurulamadığı. Yeni roller eklendikçe bu negatif testler
genişletilir (bölüm 12, T1–T14).

Platform katmanı için ayrıca şunlar sınanır (bölüm 12, T15–T18): onay kaydı
olmadan kiracı verisinin görünmediği, süresi geçmiş onayla hiçbir satır
dönmediği, platform katmanının kiracı istek hattından çağrılamadığı ve kiracı
istek hattının platform şemasına erişemediği.

---

## 4. Rol modeli

### 4.1 Rol tipleri

| Tip | Kapsam | Kim oluşturur | Örnek |
|---|---|---|---|
| **platform** | Tüm sistem (SYS) | Sistem (koddan gömülü) | SUPERADMIN, ADMIN |
| **default** | Kiracı | Kiracı oluşturulurken otomatik | Şirket Yöneticisi, Çalışan |
| **custom** | Kiracı | Şirket Yöneticisi (izin kataloğundan seçerek) | "İK Uzmanı" |

Kurallar:

- Platform rolleri **hiçbir kiracıya ait değildir** ve kiracı yöneticisi onları
  göremez/değiştiremez ⚠️ (bkz. OD3).
- Kiracı, **kendi izin kümesinin dışına çıkamaz**: özel rol oluştururken yalnızca
  kendi rollerindeki izinleri seçebilir (yetki yükseltme yasağı).
- Kullanıcı, sahip olduğu bir izni başkasına devredemez; izin atamak
  `role.manage` ve `user.manage` gerektirir.

### 4.2 Rol kataloğu (6 rol)

| Kod | Ad | Kapsam | Özet |
|---|---|---|---|
| `superadmin` | SUPERADMIN | SYS | Sistemin en üst seviyesi; kiracılar, platform ayarları, platform denetimi |
| `platform_admin` | ADMIN | SYS (PII hariç) | Platform operasyonu; talepleri işler, log kontrolü yapar, kişisel veriye **doğrudan erişemez** |
| `tenant_admin` | ŞİRKET YÖNETİCİSİ | TENANT | Kendi şirketinde tam yönetim: kullanıcı, rol, organizasyon, çalışan, süreç |
| `tenant_officer` | ŞİRKET YETKİLİSİ | TENANT (modül kısıtlı) | Yöneticinin verdiği yetkiler dahilinde operasyon: çalışan, evrak, onay |
| `dept_manager` | DEPARTMAN YÖNETİCİSİ | UNIT (alt ağaç) | Kendi organizasyon alanı: izin onayı, vardiya, devamsızlık |
| `employee` | ÇALIŞAN | SELF | Kendi bilgileri, izin talebi, kendi evrakları, kendi vardiyası |

**Mevcut kodla fark:** ✅ **UYGULANDI (M7, 2026-09-11).** Katalog artık
`tenant_admin`, **`tenant_officer`**, `hr_manager`, **`dept_manager`**, `employee`
(kod: `apps/rbac/permission_catalog.py` → `DEFAULT_ROLE_TEMPLATES`).
`manager` rolü **yerinde** `dept_manager`'a dönüştürüldü (kimlik/atama korunur, yeni
rol üretilmedi); izin seti §6.2 **DY** sütununa getirildi → ölçüm **7 → 14** izin.
`tenant_officer` §6.2 **ŞYT** setiyle oluşturuldu → **19 izin** (matriste `◐ ❓OD9`
işaretli kodlar devredilebilirlik kararı verilmediği için **alınmadı**; `document.share`
bu katalogda yok). `hr_manager` **şablon rol** olarak korunur (silinmez; `TENANT`).
`superadmin`/`platform_admin` **platform** şemasındadır (`platform.roles`) ve `hr.roles`'a
**taşınmaz**. Geçiş tablosu: bölüm 13 (7–8 ✅ UYGULANDI).

### 4.3 Rol hiyerarşisi

Hiyerarşi **izin devralma (inheritance) yaratmaz** — devralma, gizli yetki
büyümesinin en yaygın kaynağıdır. Hiyerarşi yalnızca **yönetim ilişkisini**
belirler:

```
SUPERADMIN  ── (platform ayarları, kiracı yaşam döngüsü)
   │
ADMIN       ── (platform operasyonu; PII yok)
   │
ŞİRKET YÖNETİCİSİ   ── (kiracı içi tam yönetim)      ┐
   │                                                  │  kiracı sınırı
ŞİRKET YETKİLİSİ    ── (yetkilendirilmiş modüller)   │  (aşılamaz)
   │                                                  │
DEPARTMAN YÖNETİCİSİ ── (kendi organizasyon alanı)   │
   │                                                  │
ÇALIŞAN             ── (yalnızca kendisi)            ┘
```

Kurallar:

- Üst rol, alt rolü **atayabilir** ama alt rolün kapsamını genişletemez.
- **Kendi kendini yükseltme yasağı:** hiçbir kullanıcı kendi rollerine izin
  ekleyemez, kendi kapsamını büyütemez; kendi talebini onaylayamaz.
- **Son yönetici koruması:** bir kiracıda `tenant_admin` rolündeki son kullanıcı
  kaldırılamaz/pasifleştirilemez.
- Platform rolleriyle kiracı rolleri **aynı kullanıcıda birleşemez** (bir kişi
  hem SUPERADMIN hem ŞİRKET YÖNETİCİSİ olamaz) — çıkar çatışmasını önler ❓OD11.
- **Platform tarafı UYGULANDI ✅ (M8):** hiyerarşi izin devralma üretmez; "üst rol,
  alt rolü atayabilir" kuralı **aktör atayacağı rolün tüm izinlerine sahip olmalı +
  `platform.role.assign` iznine sahip olmalı + kendine atama yasak** üçlüsüyle
  dayatılır (kod: `apps/platform/permissions.py`, `assign_role`/`revoke_role`).
  **Kiracı tarafı ✅ UYGULANDI (M9 kapsamı, 2026-09-11):** guard'lı atama giriş noktası
  `apps/rbac/services/roles.py` → `can_assign_role()` + `assign_role_to_user()`
  (kendine atama **reddedilir**; aktör `role.manage` sahibi olmalı; aktör atayacağı rolün
  **tüm** izinlerine sahip olmalı; `UNIT`/`UNIT_DIRECT` rolünde atama düzeyinde **kapsam
  zorunlu**, diğer tiplerde kapsam verilemez; atama `audit_logs`'a `role.assign` olarak
  yazılır). Rol yönetimi **API/akış** katmanı ileride bu fonksiyonu kullanacaktır.

### 4.4 Kapsam (scope) kodları

| Kod | Anlamı |
|---|---|
| `SYS` | Tüm sistem, kiracılar arası (yalnızca platform rolleri) |
| `TENANT` | Yalnızca kullanıcının kiracısı |
| `UNIT` | Kullanıcının bağlı olduğu organizasyon birimi **ve alt birimleri** ✅ **OD5 kapalı (2026-09-11):** `dept_manager`'ın kapsamı; alt ağaç OD24 (adjacency + recursive CTE, derinlik 20) ile hesaplanır |
| `UNIT_DIRECT` | Yalnızca doğrudan bağlı birim (alt birimler hariç) |
| `ASSIGNED` | Yalnızca kullanıcıya açıkça atanmış kayıtlar (onay adımı, vaka) |
| `SELF` | Yalnızca kullanıcının kendi kaydı |

Etkin kapsam = **en dar** olan: rolün izin verdiği kapsam ∩ kaydın kapsamı ∩
isteğin kapsamı. Örneğin `UNIT` kapsamlı bir onay izni, başka bir birimin
kaydında işe yaramaz.

Kapsam kaynağı: `user_roles.scope_org_unit_id` (kiracı rolleri için) — ✅ **M9 tamamlandı
(2026-09-11): gerçek FK** (`hr.departments.id`); **alt ağaç çözümlemesi ✅ UYGULANDI**
(M10–M13, recursive CTE). Rolün kapsamı kişi bazında daraltılabilir; **genişletilemez**.
✅ **OD5 kesinleştirdi (2026-09-11):** `UNIT`/`UNIT_DIRECT` tipli rol için
`scope_org_unit_id IS NULL` ise atama **hiçbir kapsam üretmez** (fail-closed; NULL =
"tüm kiracı" **değildir**); kiracı geneli yetki yalnızca `TENANT` tipli rolle verilir.
Ayrıntı: bölüm 14 → OD5.

**Kapsam çözümlemesi — ✅ UYGULANDI (`apps/rbac/services/scope.py`):**

| `scope_type` | Etkin kapsam (uygulanan) |
|---|---|
| `TENANT` | kiracının tamamı |
| `UNIT` | atanan birim **+ tüm alt ağaç** (`WITH RECURSIVE`, derinlik ≤ 20) |
| `UNIT_DIRECT` | yalnızca atanan birim |
| `ASSIGNED` | yalnızca açıkça atanmış kayıtlar (nesne düzeyi; onay modülü — 🔜) |
| `SELF` | yalnızca kullanıcının kendi kaydı |

Çoklu atama **birleşir** (tek kiracı içinde); fail-closed: silinmiş birim (ve alt ağacı),
çözülemeyen kapsam, kiracı uyuşmazlığı, silinmiş/süresi geçmiş atama, silinmiş rol,
pasif kullanıcı → **kapsam üretilmez**. Nesne kararı:
`object_in_org_scope()`; çalışan hedefi için merkezi yardımcı
`can_access_employee()` / `can_access_employee_personal_data()`.

**Rol kapsam tipi — ✅ UYGULANDI (M8, 2026-09-11):** `hr.roles.scope_type`
(`NOT NULL` + kapalı küme `CHECK`). Kiracı rollerinde geçerli küme yukarıdaki beş
koddan **`SYS` hariç** olanıdır: `TENANT`, `UNIT`, `UNIT_DIRECT`, `ASSIGNED`, `SELF`
(`SYS` yalnız **platform** rollerine aittir → `platform.roles`; `hr.roles`'a taşınmaz).
`scope_type` **izin üretmez**; karar daima `izin ∧ kapsam`. Şablon eşlemesi:
`tenant_admin`/`tenant_officer`/`hr_manager`/custom = `TENANT`, `dept_manager` = `UNIT`,
`employee` = `SELF`.

**İki kavram ayrıdır (M9, 2026-09-11):**

| Kavram | Fonksiyon | Kullanım |
|---|---|---|
| **İzin sahipliği** (rol neyi *kapsayabilir*) | `declared_permission_codes(user)` | Yönetim/teşhis ekranları — **yetkilendirme kaynağı DEĞİLDİR** |
| **Kullanılabilir izin** (kapsama göre fiilen *kullanılabilir*) | `effective_permission_codes(user)` / `authorized_permission_codes(request)` | **Yetkilendirme kararının tek kaynağı** |

M10 öncesi kapsam çözülemediği için `UNIT`/`UNIT_DIRECT` atamaları **yalnız
`sahiplık` görünümünde** kalır; `effective` kümede yer almaz (fail-closed).
**M10–M13 sonrası ✅:** kapsam **çözülür** → `UNIT`/`UNIT_DIRECT` roller etkin izin
üretebilir; ancak yalnızca **kapsam içindeki** kayıtlar için (`izin ∧ kapsam`).

---

## 5. İzin yapısı

### 5.1 Kod biçimi ✅

```
<kaynak>.<işlem>            örn: leave.request.approve
<kaynak>.<alt>.<işlem>      örn: org.department.manage
```

- Kod **İngilizce**, küçük harf, nokta ayraçlı; kalıcıdır (kayıt ve denetim
  kayıtlarında geçtiği için sonradan değiştirilmez).
- Kapsam ve koşul koda **gömülmez** (`leave.approve.own` gibi kodlar
  reddedilir); ayrı boyutlardır.
- Yeni izin kodu eklemek veri migration'ı gerektirir; **uygulanmış migration
  değiştirilmez**, yenisi yazılır ✅.

### 5.2 İzin kodları — mevcut ve eklenecek

Mevcut ✅ (29 kod): `tenant.read`, `tenant.manage`, `user.read`, `user.manage`,
`role.read`, `role.manage`, `org.department.read`, `org.department.manage`,
`org.position.manage`, `employee.read`, `employee.create`, `employee.update`,
`employee.delete`, `employee.sensitive.read`, `leave.request.read`,
`leave.request.create`, `leave.request.approve`, `leave.type.manage`,
`shift.read`, `shift.assign`, `shift.schedule.manage`, `attendance.read`,
`attendance.manage`, `document.read`, `document.upload`, `document.delete`,
`document.confidential.read`, `audit.read`, `billing.manage`.

Bu tasarımla **eklenecek** 🔜 (mevcut kodlar korunur, yenileri eklenir):

| Modül | Yeni kodlar |
|---|---|
| Platform | `platform.tenant.read`, `platform.tenant.manage`, `platform.settings.manage`, `platform.audit.read`, `platform.support.manage`, `platform.role.assign` — ✅ **eklendi (M6)**: 6 kod `hr.permissions` içine yazıldı (katalog 29 → **35**); kiracı şablon rollerine **girmez** |
| Kiracı | `tenant.export`, `tenant.delete` |
| Kullanıcı/rol | `user.public_id.resolve`, `user.permission_override.manage`, `role.assign` |
| Organizasyon | `org.unit.manage`, `org.unit.move` |
| Çalışan | `employee.export`, `employee.archive`, `employee.personal_data.read` |
| İzin | `leave.balance.read`, `leave.balance.manage`, `leave.request.cancel`, `leave.request.approve.override` |
| Vardiya/devam | `shift.publish`, `shift.swap.approve`, `attendance.correct.approve` |
| Evrak | `document.personal_data.read`, `document.classify`, `document.download.bulk`, `document.share` |
| Süreç/onay | `approval.task.read`, `approval.task.decide`, `approval.delegate`, `approval.flow.manage` |
| Denetim | `audit.read.tenant`, `audit.export` |
| Veri erişimi | `dataaccess.request.create`, `dataaccess.request.approve`, `dataaccess.grant.use`, `dataaccess.request.review` |
| Veri sahibi | `dsr.read`, `dsr.manage` (KVKK başvuruları) |

`employee.read` ile `employee.personal_data.read` ayrımı kritiktir: ilki kimlik
bilgisi olmayan kayıtları (genel kimlik, birim, unvan, durum), ikincisi maskesiz
kişisel veriyi görme hakkını verir.

### 5.3 İzin çözümleme ✅

```
etkin izinler =  (rollerin izinleri)
               ∪ (kullanıcı eklemeleri  : user_permission_overrides.allow)
               − (kullanıcı çıkarmaları : user_permission_overrides.deny)
```

- **Çakışma çözümü: reddet kazanır.** Bir izin hem verilmiş hem yasaklanmışsa
  sonuç **reddedilir** ✅.
- Kullanıcı çıkarmaları, rol değişse bile kalıcıdır (istisna kaydı).
- Roller ve kullanıcı çıkarmaları kiracı kapsamlıdır ✅.
- İzin kümesi her istekte hesaplanır; oturum boyunca önbelleğe alınıyorsa izin
  değişikliğinde önbellek **iptal edilir** (kullanıcıyı hemen atma gereksinimi).

### 5.4 Koşul (condition) türleri — kapalı küme

Yeni koşul türü eklemek karar gerektirir. Tanımlı küme:

| Koşul | Anlamı | Kullanan örnek |
|---|---|---|
| `SELF` | Kayıt, isteği yapan kullanıcıya ait | Çalışan kendi evrakı |
| `ASSIGNED` | Kullanıcı, kayda açıkça atanmış (onay adımı, vaka) | Onaycı yalnızca kendi adımı |
| `UNIT_SUBTREE` | Kaydın birimi, kullanıcının birim alt ağacında | Departman yöneticisi |
| `MFA_OK` | Oturum çok faktörlü doğrulamayla açılmış | Kişisel veri, rol yönetimi |
| `PURPOSE_GIVEN` | İşlem için gerekçe girilmiş | Kişisel veri görüntüleme |
| `GRANT_VALID` | Onaylı, süresi **geçmemiş** ve iptal edilmemiş erişim kaydı var (süre bitince kendiliğinden geçersiz) | Platform/destek erişimi, maskesiz görüntüleme |
| `POLICY_OK` | Evrakın erişim politikası izin veriyor | Kısıtlı/karanlık evrak |
| `SEPARATE_ACTOR` | İşlemi yapan, talebi/onayı oluşturandan farklı | Onay adımları |
| `PURPOSE_QUALITY` | Gerekçe asgari niteliği sağlıyor (boş, tek kelime, şablon/kopya metin reddedilir) | Kişisel veri erişim talebi (OD4) |
| `DUAL_APPROVAL` | Platform tarafında birbirinden **farklı iki** onaylayıcı onayladı | Platform kişisel veri erişimi (OD4) |

### 5.5 İzin kontrolünün zorunlu olduğu yerler ✅ (genişletilecek)

1. HTTP katmanı — uç nokta düzeyinde izin sınıfı.
2. Servis katmanı — iş kuralı + kapsam + koşul (asıl kontrol burada).
3. Nesne katmanı — kayıt bazlı sahiplik/kapsam (IDOR koruması).
4. Veritabanı — RLS (kiracı sınırı) ✅ ve rapor/export sorgularında ek filtre.

**Kural:** Dördünden biri eksikse özellik tamamlanmış sayılmaz
(`docs/DEVELOPMENT_RULES.md`).

---

## 6. Permission matrisi

**Gösterim:** ● izin var · ◐ kapsam/koşulla sınırlı · ○ yalnızca kendi/atandığı
kayıt · — izin yok. Parantez içi kapsam kodudur.

### 6.1 Rol özet matrisi

| | SUPERADMIN | ADMIN | ŞİRKET YÖN. | ŞİRKET YETK. | DEPT. YÖN. | ÇALIŞAN |
|---|---|---|---|---|---|---|
| **Görebilir** | Tüm kiracılar, sistem ayarları, platform denetimi, rol kataloğu (PII hariç) | Kiracı listesi ve limitleri, destek/yönetim talepleri, maskeli işlem logları | Kendi şirketinde her şey (çalışanların gerekli bilgileri dahil) | Yetkili modüller: çalışan listesi, evrak, süreç | Kendi birimi ve alt birimlerinin çalışan/süreç kayıtları | Kendi profili, izin/vardiya/devam bilgileri, kendi evrakları, şirket organizasyon ağacı |
| **Oluşturabilir** | Kiracı, platform admini, platform ayarları, platform rol ataması | (Talep kapsamında kayıt) | Kullanıcı, özel rol, departman, unvan, çalışan, izin türü, onay akışı | Çalışan kaydı, evrak, süreç notu (yetkili modüllerde) | Vardiya kaydı, devam düzeltme talebi, süreç notu | İzin talebi, kendi evrakı, süreç yanıtı |
| **Değiştirebilir** | Kiracı durumu/limitleri, platform rolleri, sistem ayarları | Kendisine atanan yönetim işlemleri | Şirket ayarları, kullanıcı/rol/izin, organizasyon, çalışan kaydı, süreç | Yetkili modüllerde çalışan bilgisi, evrak üst verisi | Vardiya, devam kaydı (gerekçeli), kendi birimi süreçleri | Kendi iletişim bilgileri (izinli alanlar), taslak evrakı |
| **Silebilir** | Kiracı (iptal/arşiv), platform kullanıcıları | — | Kullanıcı (pasifleştirme), unvan, boş departman (arşiv), evrak (politikaya göre) | Yetkisi dahilindeki evrak | — | Kendi onaysız taslak evrakı |
| **Onaylayabilir** | Platform erişim talepleri (çift kontrol), kiracı yöneticisi ataması | — (talebi işler, onaylamaz) | İzin talepleri, kişisel veri erişim talepleri (kendi şirketi), rol atamaları | Kendisine atanan onay adımları | Kendi biriminin izin talepleri, devam düzeltmeleri | — |
| **Sınırı** | `SYS` | `SYS` (PII yok) | `TENANT` | `TENANT` (modül kısıtlı) | `UNIT` (alt ağaç) | `SELF` |
| **Kişisel veri** | Yalnızca onaylı/süreli erişimle; varsayılan maskeli | **Erişemez**; talebe bağlı | Şirketinin çalışan verisi: gerekli alanlar; hassas alanlar ayrı izin | Gerekli alanlar maskeli; hassas alan yok | Maskeli; yalnızca süreç için gereken alanlar | Yalnızca kendisi |
| **MFA** | Zorunlu | Zorunlu | Zorunlu ❓OD10 | Zorunlu ❓OD10 | Önerilir | İsteğe bağlı |

### 6.2 Modül bazlı detay

**Kısaltmalar:** `SA` = SUPERADMIN · `PA` = ADMIN · `ŞY` = ŞİRKET YÖNETİCİSİ ·
`ŞYT` = ŞİRKET YETKİLİSİ · `DY` = DEPARTMAN YÖNETİCİSİ · `Ç` = ÇALIŞAN.

#### A. Platform ve kiracı yaşam döngüsü

| İzin kodu | SUPERADMIN | ADMIN |
|---|---|---|
| `platform.tenant.read` | ● (SYS, PII maskeli) | ● (SYS, PII maskeli) |
| `platform.tenant.manage` (oluştur/durum/limit) | ● (SYS) | ◐ (yalnızca atanmış talep kapsamında) |
| `platform.settings.manage` | ● (SYS) | — |
| `platform.role.assign` (platform admini atama) | ● (SYS) | — |
| `platform.audit.read` | ● (SYS) | ◐ (maskeli; kişisel veri erişim kayıtları **görünür**) |
| `platform.support.manage` (destek oturumu) | ● (SYS) | ◐ (onaylı, süreli, kayıtlı) |
| `tenant.delete` | ● (SYS) | — |
| `billing.manage` | ● (SYS) | ◐ (atandığı kiracı, talep kapsamında) |

Kurallar:

- ADMIN, kiracı iş verisine **doğrudan** yazamaz; yalnızca kendisine atanan
  yönetim talebini yürütür ve her adım kayda geçer ❓OD8.
- Kiracı verisine erişim, SUPERADMIN ve ADMIN için **varsayılan olarak yoktur**;
  gerekçeli talep + onay + süreli kayıt olmadan açılmaz (OD1, OD3 — kapalı).
- Platform tarafındaki kişisel veri erişiminde **çift onay** zorunludur: talebi
  açan onaylayamaz, iki farklı platform kullanıcısı onaylar (OD4 — kapalı).
- Platform kullanıcıları ayrı modeldir (`platform_users`); kiracı kullanıcı
  tablosuyla birleştirilmez ve kiracı RLS politikaları onlar için gevşetilmez.

**Uygulama durumu (M6–M8, 2026-09-11):**

- **Kodlar (M6):** altı `platform.*` izni ortak katalogda (`hr.permissions`); ikinci
  katalog yok. `hr.permissions` uygulama rolünde **salt okunur** (ölçüm: öncesi
  `t/t/t/t` → sonrası `t/f/f/f`).
- **Kümeler (M7):** `superadmin` **6/6**, `platform_admin` **4/4** — açık bağlarla;
  **izin devralma yok** (hiyerarşi yalnız yönetim ilişkisi). `platform_admin`'de
  `platform.settings.manage` ve `platform.role.assign` **yoktur**.
- **Çözümleyici (M8):** `apps/platform/permissions.py` — aktif kullanıcı + aktif
  (süresi geçmemiş) atama + aktif rol + aktif rol-izin bağı; **soft-delete edilmiş
  hiçbir kayıt yetki vermez**; fail-closed. `platform_admin` **kendini superadmin
  yapamaz**, sahip olmadığı izni dağıtamaz; hiçbir kullanıcı kendi rolünü
  atayamaz/değiştiremez.
- **SUPERADMIN ≠ kişisel veri:** platform izinleri **hiçbir** kiracı/PII kodu
  içermez; kiracı verisi yalnızca erişim talebi/onayı (OD1/OD4, M17–M19) ile açılır.
  Bootstrapped ilk superadmin de (M33) bu kuralın dışında değildir.

#### B. Kullanıcı, rol ve izin yönetimi

| İzin kodu | SUPERADMIN | ADMIN | ŞİRKET YÖN. | ŞİRKET YETK. | DEPT. YÖN. | ÇALIŞAN |
|---|---|---|---|---|---|---|
| `user.read` | ● (kiracı sınırı yok) | ◐ (maskeli) | ● (TENANT) | ◐ (TENANT, yetkili modül) | ◐ (UNIT) | ○ (SELF) |
| `user.manage` | ● | — | ● (TENANT) | — ❓OD9 | — | — |
| `role.read` | ● | ◐ | ● | ● | — | — |
| `role.manage` | ● (platform rolleri) | — | ● (yalnızca özel roller, TENANT) | — | — | — |
| `role.assign` | ● | — | ● (TENANT, kendi seviyesinin altı) | ◐ (yalnızca yöneticinin verdiği) | — | — |
| `user.permission_override.manage` | — | — | ● (TENANT ❓OD12) | — | — | — |
| `user.public_id.resolve` | ● (kayıtlı, gerekçeli) | ● (kayıtlı, gerekçeli) | ● (TENANT, kayıtlı) | ◐ (TENANT, kayıtlı, iş gerekçesi) | ◐ (UNIT) | — |

#### C. Organizasyon

| İzin kodu | SA | PA | ŞY | ŞYT | DY | Ç |
|---|---|---|---|---|---|---|
| `org.department.read` | ● | ◐ | ● (TENANT) | ● | ● (UNIT) | ● (TENANT, salt yapı) |
| `org.department.manage` | — | — | ● (TENANT) | ◐ | — | — |
| `org.position.manage` | — | — | ● (TENANT) | ◐ | — | — |
| `org.unit.move` | — | — | ● (TENANT) ❓OD6 | — | — | — |

#### D. Çalışan

| İzin kodu | SA | PA | ŞİRKET YÖN. | ŞİRKET YETK. | DEPT. YÖN. | ÇALIŞAN |
|---|---|---|---|---|---|---|
| `employee.read` (kimliksiz alanlar) | ● | ◐ | ● (TENANT) | ● (TENANT) | ● (UNIT) | ○ (SELF) |
| `employee.create` | — | — | ● (TENANT) | ◐ | — | — |
| `employee.update` | — | — | ● (TENANT) | ◐ | ◐ (UNIT, sınırlı alanlar) | ◐ (SELF, yalnızca iletişim) |
| `employee.personal_data.read` (maskesiz) | ◐ (GRANT_VALID + PURPOSE_GIVEN) | — | ◐ (PURPOSE_GIVEN, gerekçeli; OD4 kapalı) | ◐ (GRANT_VALID) | ○ (UNIT + PURPOSE_GIVEN, süreç alanları) | ○ (SELF) |
| `employee.sensitive.read` (özel nitelikli) | ◐ (GRANT_VALID, çift onay) | — | ◐ (GRANT_VALID, onaylı süreli) | — | — | ○ (SELF) |
| `employee.archive` | — | — | ● (TENANT) | — | — | — |
| `employee.delete` (gerçek silme) | ◐ (SYS + kayıtlı, kiracı talebi) | — | ◐ (TENANT, KVKK saklama kuralı) ❓OD7 | — | — | — |
| `employee.export` | ◐ (kayıtlı) | — | ◐ (TENANT, maskeli varsayılan) | — | — | ○ (SELF) |

#### E. İzin (leave)

| İzin kodu | SA | PA | ŞİRKET YÖN. | ŞİRKET YETK. | DEPT. YÖN. | ÇALIŞAN |
|---|---|---|---|---|---|---|
| `leave.request.read` | — | — | ● (TENANT) | ● (TENANT) | ● (UNIT) | ○ (SELF) |
| `leave.request.create` | — | — | ● (TENANT) | ● | ◐ (UNIT, kendi adına) | ○ (SELF) |
| `leave.request.approve` | — | — | ● (TENANT) | ◐ (ASSIGNED) | ◐ (UNIT + ASSIGNED + SEPARATE_ACTOR) | — |
| `leave.request.approve.override` | — | — | ● (TENANT, kayıtlı gerekçe) | — | — | — |
| `leave.request.cancel` | — | — | ● | ◐ | ◐ (UNIT) | ○ (SELF, onay öncesi) |
| `leave.balance.read` | — | — | ● (TENANT) | ● (TENANT) | ● (UNIT) | ○ (SELF) |
| `leave.balance.manage` | — | — | ● (TENANT) | — | — | — |
| `leave.type.manage` | — | — | ● (TENANT) | — | — | — |

**Uygulama (M14 ✅, 2026-09-11):** bu sekiz kodun **tamamı** katalogdadır
(`leave.request.cancel`, `leave.request.approve.override`, `leave.balance.read`,
`leave.balance.manage` M14 ile eklendi — `rbac/0011_m14_leave_permissions`).
Rol bağları matrise göre kurulur; `hr_manager` matriste sütun olmadığı için
**değiştirilmedi**. Denetim kararı (`apps/leave/services/authorization.py`):

| İşlem | Koşullar (uygulanan) |
|---|---|
| Talebi görme | `leave.request.read` ∧ aynı kiracı ∧ (`SELF` ∨ kapsam ∨ **karar verebildiği** adım) |
| Talep oluşturma | `leave.request.create` ∧ aynı kiracı ∧ (`SELF` ∨ kapsam); çalışan/tür aktif olmalı |
| İptal | `leave.request.cancel` ∧ aynı kiracı ∧ (`SELF` yalnız onay öncesi ∨ kapsam) |
| Onay | `leave.request.approve` ∧ aynı kiracı ∧ (`ASSIGNED` ∨ **TENANT** kapsam) ∧ `SEPARATE_ACTOR` ∧ kapsam |
| Bakiye görme/düzeltme | `leave.balance.read`/`.manage` ∧ aynı kiracı ∧ (`SELF` ∨ kapsam); düzeltmede **gerekçe zorunlu** |

**Onay yönlendirmesi (uygulanan v1):** docs'ta kesin sıra tanımlı olmadığı için
güvenli sıra kullanılır → (1) çalışanın **birincil yöneticisi** (`employee_manager`,
`SEPARATE_ACTOR` ihlali varsa atlanır), (2) **yetkili rol** adımı (`hr_manager` rol
kodu + kapsam). **Birincil yönetici olmak onay yetkisi üretmez**: onay için izin
kodu + kapsam + adım sahipliği gerekir. Sıra atlanamaz (DB guard'ı + servis
`current_step` seçimi); yetkili **override** (`leave.request.approve.override`)
adımları kayıtlı gerekçeyle atlar ve denetim kaydı yazar.

#### F. Vardiya ve devam

| İzin kodu | SA | PA | ŞİRKET YÖN. | ŞİRKET YETK. | DEPT. YÖN. | ÇALIŞAN |
|---|---|---|---|---|---|---|
| `shift.read` | — | — | ● (TENANT) | ● (TENANT) | ● (UNIT) | ○ (SELF) |
| `shift.assign` | — | — | ● (TENANT) | ◐ | ● (UNIT) | — |
| `shift.schedule.manage` | — | — | ● (TENANT) | ◐ | ◐ (UNIT) | — |
| `shift.publish` | — | — | ● (TENANT) | — | ◐ (UNIT) ✅ OD5 kapalı | — |
| `shift.swap.approve` | — | — | ● | ◐ (ASSIGNED) | ◐ (UNIT) | — |
| `attendance.read` | — | — | ● (TENANT) | ● (TENANT) | ● (UNIT) | ○ (SELF) |
| `attendance.manage` (düzeltme) | — | — | ● (TENANT) | ◐ | ◐ (UNIT, gerekçeli) | — |
| `attendance.correct.approve` | — | — | ● (TENANT) | ◐ | ◐ (UNIT) | — |

#### G. Evrak

| İzin kodu | SA | PA | ŞİRKET YÖN. | ŞİRKET YETK. | DEPT. YÖN. | ÇALIŞAN |
|---|---|---|---|---|---|---|
| `document.read` | — | — | ● (TENANT) | ● (TENANT) | ◐ (UNIT + POLICY_OK) | ○ (SELF + POLICY_OK) |
| `document.upload` | — | — | ● | ● | ◐ (UNIT, çalışanı adına) | ○ (SELF) |
| `document.classify` (kişisel veri işareti) | — | — | ● (TENANT) | ◐ | — | ◐ (SELF, beyan) |
| `document.personal_data.read` | ◐ (GRANT_VALID) | — | ◐ (PURPOSE_GIVEN) | — | — | ○ (SELF) |
| `document.confidential.read` | ◐ (GRANT_VALID) | — | ● (TENANT) | ◐ ❓OD9 | — | — |
| `document.download.bulk` | — | — | ● (TENANT, kayıtlı) | — | — | — |
| `document.share` | — | — | ● (TENANT, süreli bağlantı) | ◐ | — | — |
| `document.delete` | — | — | ◐ (TENANT, saklama kuralı) ❓OD6 | ◐ (kendi yüklediği) | — | ○ (SELF, taslak) |

#### H. Süreç ve onay

| İzin kodu | SA | PA | ŞİRKET YÖN. | ŞİRKET YETK. | DEPT. YÖN. | ÇALIŞAN |
|---|---|---|---|---|---|---|
| `approval.flow.manage` | — | — | ● (TENANT) | — | — | — |
| `approval.task.read` | — | — | ● (TENANT) | ● | ● (UNIT) | ○ (SELF) |
| `approval.task.decide` | — | — | ● (TENANT) | ◐ (ASSIGNED) | ◐ (UNIT + ASSIGNED) | — |
| `approval.delegate` | — | — | ● | ◐ | — | — |

#### I. Denetim, kişisel veri erişimi ve veri sahibi hakları

| İzin kodu | SA | PA | ŞİRKET YÖN. | ŞİRKET YETK. | DEPT. YÖN. | ÇALIŞAN |
|---|---|---|---|---|---|---|
| `audit.read.tenant` | ◐ (kayıtlı) | ◐ | ● (TENANT) | ◐ (yetkili modül) | — | — |
| `audit.read` (platform denetimi) | ● | ◐ (maskeli) | — | — | — | — |
| `audit.export` | ◐ (kayıtlı) | — | ◐ (TENANT, kayıtlı) | — | — | — |
| `dataaccess.request.create` | ● | ● | ● | ● | ◐ (UNIT) | ○ (SELF, kendi verisi) |
| `dataaccess.request.approve` | ◐ (platform talepleri; **iki farklı onaylayıcı**, kendi talebini onaylayamaz) | — | ● (kendi şirketi; onay yetkisi devredilebilir) | — | — | — |
| `dataaccess.grant.use` | ◐ (SYS + süre) | ◐ (SYS + süre) | ◐ (TENANT) | ◐ | ◐ | ○ |
| `dsr.read` / `dsr.manage` (KVKK başvurusu) | ◐ (kayıtlı) | ◐ | ● (TENANT) | ◐ | — | ○ (SELF, kendi başvurusu) |

### 6.3 Matrisin yorumu

- **SUPERADMIN** teknik olarak en geniş yetkiye sahiptir ama **kişisel veri**
  sütununda `GRANT_VALID` görür: varsayılan erişim **yoktur**; erişim gerekçeli
  talep + onay + süreli kayıtla açılır ve süre bitince kapanır (OD1 — kapalı).
- **ADMIN** hiçbir satırda kişisel veriyi maskesiz göremez. Destek işi için
  erişim talebi açar; onay geldiğinde **süreli** çalışır.
- **ŞİRKET YÖNETİCİSİ** kendi şirketinde en geniştir; bu, veri sorumlusunun
  (kiracının) kendi verisini yönetmesi ilkesinin sonucudur.
- **DEPARTMAN YÖNETİCİSİ** ve **ÇALIŞAN** satırlarının çoğunda `○ (SELF)` /
  `◐ (UNIT)` görünür: bunlar kapsam ve koşul ile daraltılmış izinlerdir.

---

## 7. Kişisel veri erişim modeli

### 7.1 Veri sınıflandırması 🔜

| Sınıf | Örnek | Varsayılan görünüm | Erişim koşulu |
|---|---|---|---|
| **K0 — İş verisi, kişisel değil** | departman adı, unvan, vardiya kodu | açık | izin yeterli |
| **K1 — Kimlik (kurumsal)** | genel kullanıcı kimliği, iş e-postası | açık (kiracı içi) | izin yeterli |
| **K2 — Kişisel veri** | ad-soyad, özel e-posta, telefon, adres, doğum tarihi, TCKN | **maskeli** | `*.personal_data.read` + gerekçe |
| **K3 — Özel nitelikli (hassas)** | sağlık raporu, engellilik, biyometrik, din, sendika, ceza kaydı | **gizli** | `employee.sensitive.read` + onaylı süreli erişim |
| **K4 — Evrak içeriği** | kimlik fotokopisi, sözleşme, sağlık raporu dosyası | **gizli** | evrak politikası + `document.personal_data.read` |

Maskeleme örnekleri: `Ayşe Yılmaz` → `A*** Y***`, `ayse@ornek.com` →
`a***@o***.com`, `+90 5** *** ** 12`, TCKN → `*********12`.

### 7.2 Varsayılan görünüm ve maskeleme 🔜

- Liste ve arama sonuçlarında **her zaman** genel kimlik (bölüm 8) ve maskeli
  değerler gösterilir; API yanıtlarında da maskeleme **sunucu tarafında** yapılır
  (istemciye maskesiz veri gönderilmez).
- Arama, maskesiz alan üzerinden yapılmaz; e-posta/telefon ile arama yapan
  kullanıcı yalnızca "eşleşme var/yok" bilgisini alır ❓OD8.
- Dışa aktarma (Excel/CSV) varsayılan olarak maskelidir; maskesiz dışa aktarma
  `employee.export` + gerekçe + kayıt gerektirir.

### 7.3 Kontrollü erişim akışı (talep → onay → kullanım) 🔜 (OD1/OD4 — KARAR VERİLDİ)

```
1. Talep     Kullanıcı: hangi kişi/kayıt, hangi alanlar, hangi amaç, ne kadar süre
             (izin: dataaccess.request.create) → kayıt: AccessRequest
             gerekçe zorunlu ve asgari nitelikte (PURPOSE_QUALITY): boş,
             tek kelime veya şablon/kopya metinle talep AÇILAMAZ
2. Onay      Kiracı içi taleplerde: ŞİRKET YÖNETİCİSİ veya onay yetkilisi
             (izin: dataaccess.request.approve) — temel onay mekanizması
             Platform taleplerinde: ÇİFT ONAY — birbirinden farklı iki platform
             kullanıcısı (DUAL_APPROVAL); talep sahibi onaylayamaz (SEPARATE_ACTOR)
3. Kullanım  Onay, süreli bir ERİŞİM İZNİ üretir (AccessGrant):
             kapsam = belirtilen kayıtlar + belirtilen alanlar + bitiş zamanı
             kullanımda aranan koşullar: PURPOSE_GIVEN + GRANT_VALID (+ MFA_OK)
4. Kayıt     Denetim kaydı: talep, onay, RED, erişim başlangıcı ve erişim bitişi
             ayrı ayrı yazılır (kim, kimin verisi, hangi alanlar, hangi amaç,
             hangi onay kaydı, ne zaman)
5. Bitiş     Süre dolunca erişim OTOMATİK kapanır (koşul geçersizleşir);
             iptal edilebilir; uzatma yeni onay gerektirir
```

Kurallar:

- **Varsayılan erişim yok (OD1 — kapalı):** onay yoksa, süre dolmuşsa veya kapsam
  dışıysa erişim yoktur (fail-closed). Bu kural **SUPERADMIN için de aynıdır**.
- **Amaç bağlama ve gerekçe niteliği (OD4 — kapalı):** gerekçe zorunludur; boş,
  tek kelime veya şablon/kopya metin kabul edilmez ve gerekçe kayda geçer.
- **En az ifşa:** yalnızca talep edilen alanlar açılır (tüm profil değil).
- **Onay mercii (OD4 — kapalı):** kiracı içinde şirket yöneticisi / yetkilendirdiği
  onay yetkilisi; platform tarafında **iki farklı onaylayıcı** (çift onay). Talep
  sahibi hiçbir durumda kendi talebini onaylayamaz.
- **Beş olay kayıtta:** talep, onay, red, erişim başlangıcı, erişim bitişi.
- **Görünürlük:** kiracı yöneticisi, kendi çalışanlarının verisine yapılan platform
  erişimini görür (talep, gerekçe, zaman).
- **İstisnasız kayıt:** kişisel veriye erişimin kaydedilmediği bir yol bırakılmaz.
- **Veri sahibi hakları:** çalışan kendi verisine erişimini, düzeltmeyi ve
  (saklama süresi dolduğunda) silinmesini talep edebilir (`dsr.*`).

### 7.4 Kişisel verinin teknik koruması ✅ (genişletilecek)

- Özel nitelikli alanlar (K3) veritabanında **şifreli** saklanır 🔜; anahtar
  uygulamadan ayrı yönetilir (mevcut Fernet altyapısı ✅ TOTP sırrı için bunu
  zaten yapıyor).
- Denetim kayıtları kişisel verinin **değerini** taşımaz; yalnızca alan adı ve
  "görüntülendi/değişti" bilgisini taşır ✅.
- Log ve hata mesajlarına kişisel veri yazılmaz (`docs/SECURITY.md` bölüm 8).
- Yedekler şifrelidir ve erişim kaydı tutulur 🔜.

---

## 8. Genel (public) kullanıcı kimliği modeli

### 8.1 Amaç

Sistemde bir kişiyi, gerçek ismini/e-postasını açığa çıkarmadan **işaret
edebilmek**. Destek talepleri, admin işlemleri, denetim kayıtları ve yönetim
ekranları bu kimlik üzerinden çalışır.

### 8.2 Format

```
<kiracı-kısa-adı>-<rastgele>
örnek: acme-7F3K9QW2
```

- Kiracı ön eki: kiracının kısa adı (`slug`), 2–20 karakter, yalnızca `a-z0-9-`.
- Rastgele bölüm: **Crockford Base32, 8 karakter** (`secrets` ile üretilir,
  ~40 bit entropi). Karıştırılan harfler (I, L, O, U) alfabede yoktur.
- Uzunluk/tipografisi sabit; sunucu üretir, kullanıcı değiştiremez.

**Neden sıralı sayı değil?** İstekte örnek olarak `a-sirketi-00XXXX` verilmişti;
sıralı/sayılabilir kimlikler numaralandırma (enumeration) saldırısına açıktır ve
"tahmin edilemez" şartını karşılamaz. Rastgele bölüm bu yüzden zorunludur — **OD2 kapalı**
 (kullanıcı kararı: kiracı ön eki + Crockford Base32 + 8 rastgele karakter).

### 8.3 Kurallar

| Kural | Ayrıntı |
|---|---|
| **Benzersizlik** | Tüm sistemde benzersiz (kiracı ön eki + rastgele bölüm birlikte) |
| **Değişmezlik** | Bir kez üretilir; kiracı adı değişse veya kişi başka birime geçse bile değişmez (denetim izi kopmasın) |
| **Türetilmemişlik** | İsim, e-posta, TCKN, ID veya sayaçtan türetilmez |
| **Yeniden kullanım yok** | Silinen kullanıcının kimliği başkasına verilmez |
| **Yaşam döngüsü** | Kullanıcı silinse de denetim kayıtlarında geçtiği için kaydı korunur (tombstone) |
| **Kullanım yerleri** | Tüm listeler ve formlar, admin/destek talepleri, denetim kaydı görünümü, hata raporları, entegrasyonlar, dışa aktarım (maskeli) |
| **Yasak yerler** | E-posta adreslerinde, dosya adlarında, dış URL'lerde kimliğin yanına gerçek isim yazılmaz |

### 8.4 Ters çözümleme (kimlik → gerçek kişi)

Genel kimlik **tek başına** kişisel veriye erişim sağlamaz. Eşleme yalnızca:

1. `user.public_id.resolve` izni,
2. `PURPOSE_GIVEN` (gerekçe) ve gerekiyorsa `GRANT_VALID`,
3. hız sınırı (toplu çözümleme yok; kullanıcı başına dakikada N çözümleme),
4. her çözümleme için denetim kaydı

ile mümkündür. Toplu çözümleme (`resolve` + listeleme) API'de desteklenmez;
gerekiyorsa `employee.export` üzerinden maskeli olarak yapılır.

### 8.5 Numaralandırma koruması 🔜

- Rastgele bölüm + hız sınırı + denetim; başarısız çözümleme denemeleri de sayılır.
- Aynı kiracı içinde bile başka bir çalışanın kimliğiyle arama yapıldığında
  sonuç, kullanıcının izin ve kapsamına göre filtrelenir.
- Uç nokta, "kayıt yok" ile "yetkin yok" arasında **ayırt edilemez** yanıt verir
  (varlık sızdırmama).

---

## 9. Evrak erişim modeli

### 9.1 Yükleme anında sınıflandırma 🔜

Her evrak yüklemesinde zorunlu alanlar:

| Alan | Değerler | Amaç |
|---|---|---|
| `classification` | `public`, `internal`, `confidential`, `restricted` | Genel erişim düzeyi |
| `contains_personal_data` | evet / hayır / emin değilim | Kişisel veri politikasını tetikler |
| `personal_data_categories` | sağlık, kimlik, iletişim, finans… (çoklu) | Hassas sınıf tespiti (K3) |
| `retention` | saklama süresi / olay sonrası | KVKK saklama ve silme |

"Emin değilim" seçilirse evrak **en kısıtlayıcı** politikayla başlar ve
sınıflandırma onayı bekler ❓OD9.

### 9.2 Politika matrisi

| Evrak durumu | Çalışan (kendi) | Dept. Yön. | Şirket Yetk. | Şirket Yön. | ADMIN | SUPERADMIN |
|---|---|---|---|---|---|---|
| Kişisel veri **yok**, `internal` | ● gör/indir | ◐ (birimi) | ● | ● | — | ◐ (kayıtlı) |
| Kişisel veri **var** | ○ kendi evrakı | — (yalnızca varlık bilgisi) | ◐ (yetki + gerekçe) | ● (gerekçeli) | — | ◐ (süreli onay) |
| `confidential` | — | — | ◐ | ● | — | ◐ (süreli onay) |
| `restricted` (K3: sağlık vb.) | ○ kendi evrakı | — | — | ◐ (onaylı, süreli) | — | ◐ (çift kontrol) |

Ek kurallar:

- Erişim, dosya **sunucudan sunulurken** doğrulanır; paylaşılan bağlantılar
  süreli ve tek amaçlıdır (presigned URL, kısa ömür) ✅ altyapı hazır.
- İndirme/görüntüleme denetim kaydına yazılır (kim, hangi evrak, hangi sürüm).
- Silme = mantıksal silme + saklama süresi sonunda gerçek silme; hukuki saklama
  gerektiren evraklar kullanıcı tarafından silinemez ❓OD6.
- Evrak içeriği (dosya) ile üst verisi (kime ait, tür, tarih) ayrı yetkilendirilir:
  "varlığını bilmek" ile "içeriğini görmek" farklı izinlerdir.

---

## 10. Denetim kaydı (audit log) modeli

### 10.1 Kaydedilmesi zorunlu olaylar

| Kategori | Olaylar | Ek alanlar |
|---|---|---|
| Kimlik doğrulama | giriş, çıkış, başarısız giriş, hesap kilidi, MFA kurulumu/başarısızlığı, token üretimi/iptali | IP, kullanıcı aracısı |
| Yetki değişikliği | rol oluşturma/değiştirme/silme, rol atama/kaldırma, izin değişikliği, kapsam değişikliği, kişiye özel istisna | eski/yeni değer |
| Kullanıcı yaşam döngüsü | oluşturma, davet, güncelleme, pasifleştirme, silme, parola sıfırlama | hedef genel kimlik |
| Veri görüntüleme | kişisel veri görüntüleme, listeleme, arama, dışa aktarma, yazdırma | alan listesi, gerekçe, onay kimliği |
| Veri değiştirme | iş kaydı oluşturma/güncelleme/silme | değişen alanlar (değer değil) |
| Onay süreçleri | onaylama, reddetme, devretme, geri alma, akış değişikliği | gerekçe, adım |
| Evrak işlemleri | yükleme, görüntüleme, indirme, paylaşım, silme, sınıflandırma değişikliği | evrak kimliği, sürüm |
| Platform erişimi | **talebin açılması, onay, red, erişim başlangıcı, erişim bitişi**, kiracıya destek erişimi, kiracılar arası sorgu | gerekçe (nitelik denetimli), onay kimliği (çift onay), süre, etkilenen kiracı |
| Yapılandırma | kiracı ayarları, limitler, güvenlik ayarları, izin kataloğu değişikliği | eski/yeni |

### 10.2 Kayıt alanları ✅ (genişletilecek)

Zorunlu: zaman (`occurred_at`, UTC), kiracı, yapan kullanıcı (iç kimlik + **genel
kimlik**), yapanın rolleri, işlem kodu, hedef türü + hedef **genel kimliği**,
sonuç (başarılı/başarısız), istek kimliği (`request_id`, dağıtık izleme),
IP ve kullanıcı aracısı.

Eklenecek 🔜: `purpose` (gerekçe), `grant_id` (onay kaydı), `changed_fields`
(alan adları), `access_scope`, `source` (web/api/platform), `previous_value_hash`.

**Kişisel veri kuralı:** denetim kaydı, kişisel verinin **değerini** taşımaz.
"telefon alanı 14:32'de değişti" yazılır; eski/yeni numara yazılmaz.

### 10.3 Görünürlük ve saklama

| Görünürlük | Kim |
|---|---|
| Kiracı akışı | ŞİRKET YÖNETİCİSİ (kendi kiracısı), ŞİRKET YETKİLİSİ (yetkili modüller) |
| Platform akışı | SUPERADMIN, ADMIN (maskeli) |
| Çalışan | Kendi kaydına yapılan işlemler (şeffaflık) ❓OD13 |

- Kayıtlar **yalnızca eklenebilir** (append-only) ✅; uygulama rolünün
  `UPDATE`/`DELETE` yetkisi yoktur (veritabanı seviyesinde zorlanır).
- Kiracı, kendi denetim kayıtlarını görebilir; başka kiracının kayıtlarını
  göremez (RLS) ✅.
- Saklama: varsayılan 10 yıl ❓OD13; yasal süre dolunca arşivlenir.
- **Bütünlük** 🔜: kayıtlar zincirlenir (her kayıt, öncekinin özetini taşır) →
  sonradan değiştirme tespit edilebilir. Kayıt üretimi başarısız olursa
  güvenlik açısından kritik işlem **tamamlanmaz** (fail-closed) ❓OD13.

### 10.4 Ayrım: teknik log ≠ denetim kaydı

`audit_logs` iş/uyum kaydıdır (kim, ne, hangi gerekçeyle). Uygulama/hata
logları ayrı tutulur, kişisel veri içermez ve daha kısa süre saklanır
(`docs/SECURITY.md` bölüm 6).

---

## 11. Zorlama zinciri — bir istek nasıl karara bağlanır

```
1. KİMLİK        Kim? (oturum / API token / yok)
                 └ bağlam: kiracı, kullanıcı, roller, MFA durumu
2. KİRACI        Kiracı bağlamı kuruldu mu? (oturum → alan adı → token)
                 └ uyuşmazlık = RED; yoksa RED (fail-closed)
3. İZİN          Kod ∈ etkin izinler?          → değilse 403
4. KAPSAM        Kayıt, rolün kapsamında mı?   → değilse 404 (varlık sızdırma yok)
5. KOŞUL         SELF/ASSIGNED/MFA_OK/PURPOSE/GRANT/POLICY/SEPARATE_ACTOR
                 └ sağlanmıyorsa: 403 veya "gerekçe/onay gerekli" yönlendirmesi
6. ALAN          Kişisel veri alanları maskeli mi? (çıktı filtresi)
7. VERİTABANI    RLS (kiracı) + tetikleyiciler (kiracılar arası FK yasağı)
8. DENETİM       Önemli işlem ise kayıt yazıldı mı? (yazılamazsa işlem geri alınır)
```

Notlar: (a) 4. adımda "yetkin yok" yerine "bulunamadı" dönmesi bilinçli bir
tercihtir — kayıt varlığını sızdırmaz. (b) 6. adım çıktı filtresi **sunucu**
tarafındadır; istemciye maskesiz alan gönderilmez. (c) 8. adım fail-closed'dur.

---

## 12. Test güvenceleri (negatif testler zorunlu)

Her rol ve her yeni modül için aşağıdaki iddialar test edilir:

| # | İddia | Durum |
|---|---|---|
| T1 | Kiracı A kullanıcısı, B'nin kaydını **göremez** (tüm roller için) | ✅ kısmen |
| T2 | Bağlam yoksa sorgu 0 satır döner (fail-closed) | ✅ |
| T3 | Tablo sahibi rol bile FORCE RLS'i aşamaz | ✅ |
| T4 | Kiracılar arası yabancı anahtar kurulamaz | ✅ |
| T5 | İzin verilmemiş uç nokta 403, kimlik yoksa 401 | ✅ |
| T6 | Kapsam dışı kayıt isteği 404 (varlık sızdırmaz) | 🔜 |
| T7 | Kendi kendini yükseltme (rol/izin/kapsam) engellenir | 🔜 |
| T8 | Talep sahibi kendi talebini onaylayamaz (SEPARATE_ACTOR) | ✅ **izin tarafı uygulandı (M14)**: `can_approve_step()` kendi talebini reddeder; birincil yönetici kendi talebinin onay adımı **kurulmaz** |
| T9 | Kişisel veri maskesiz **hiçbir** varsayılan yanıtta yok | 🔜 |
| T10 | Onaysız/süresi geçmiş erişimle kişisel veri açılmaz | 🔜 |
| T11 | Genel kimlik ters çözümlemesi gerekçesiz ve kayıtsız çalışmaz | 🔜 |
| T12 | Denetim kaydı yazılamazsa kritik işlem tamamlanmaz | 🔜 |
| T13 | Yetkisiz kişi evrak içeriğini (indirmeyi) alamaz | 🔜 |
| T14 | Platform erişimi süresi dolduğunda sorgu boş döner | 🔜 |
| T15 | Platform kullanıcısı, onay kaydı olmadan kiracı verisi göremez | 🔜 |
| T16 | Platform katmanı, kiracı istek hattından çağrılamaz (tek yönlü bağımlılık) | 🔜 |
| T17 | Kiracı isteği, platform şemasındaki tablolara erişemez (yetki yok) | 🔜 |
| T18 | Gerekçe boş/anlamsız ise erişim talebi oluşturulamaz; talep sahibi kendi talebini onaylayamaz | 🔜 |

---

## 13. Mevcut koddan geçiş (uygulama planı)

Kod yazılmadan önce tamamlanacak iş kalemleri (öncelik sırasıyla):

| # | İş | Etki | Not |
|---|---|---|---|
| 1 | `User.public_id` alanı + üretim + benzersizlik ✅/🔜 | Şema + tüm listeler | Bölüm 8; OD2 biçim kararı bekliyor |
| 2 | **Ayrı `platform_users` modeli + ayrı giriş yolu** | Yeni tablo + kimlik doğrulama yolu | Bölüm 3.4; OD3 **kapalı** |
| 3 | **Ayrı platform şeması + `persopyro_platform` veritabanı rolü** (kiracı RLS politikaları DEĞİŞMEZ, `BYPASSRLS` yok) | Şema + yetkiler | Bölüm 3.4; OD1/OD3 **kapalı** |
| 4 | **`AccessRequest` / `AccessGrant`: onay akışı (tek onay / çift onay), gerekçe nitelik denetimi, süre sonunda otomatik kapanma** | Yeni tablolar + servis | Bölüm 7.3; OD1/OD4 **kapalı** |
| 5 | **Platform denetim akışı (ayrı akış) + kiracı görünürlüğü kaydı** | Yeni tablo + servis | Bölüm 3.4/10; OD15 (görünürlük kapsamı) açık |
| 6 | İzin kataloğu genişletmesi + yeni migration | Veri migration'ı | Bölüm 5.2 (uygulanmış migration değiştirilmez) |
| 7 | Rol kodu eşlemesi: `manager` → `dept_manager`, `tenant_officer` eklenmesi | Veri migration'ı | Bölüm 4.2; **✅ UYGULANDI (M7 — `rbac/0005`, 2026-09-11):** yerinde adlandırma (kimlik/atama korunur), izin setleri §6.2 DY (14) ve ŞYT (19); conflict/soft-delete fail-closed |
| 8 | `user_roles.scope_org_unit_id` (kapsam) + `roles.scope_type` | Şema + guard | **✅ UYGULANDI (M8+M9 kısmi — `rbac/0006`+`0007`, 2026-09-11):** `scope_type` NOT NULL + kapalı küme `CHECK`; `scope_org_unit_id` kolonu + `NULLS NOT DISTINCT` benzersizlik + guard tetikleyicisi; **ertelenen:** `departments(id)` FK'si ve alt ağaç çözümlemesi (M10). M10 öncesi `UNIT`/`UNIT_DIRECT` atamaları izin üretmez (fail-closed) |
| 9 | Alan sınıflandırma + maskeleme katmanı (sunucu tarafı) | Servis/çıktı filtresi | Bölüm 7.2 |
| 10 | Evrak: `contains_personal_data`, `classification`, `retention` | Şema | Bölüm 9 |
| 11 | `audit_logs` yeni alanlar (`purpose`, `grant_id`, `changed_fields`) + zincirleme | Şema + servis | Bölüm 10 |
| 12 | Kapsam ve koşul kontrolü için ortak yardımcılar (`PURPOSE_QUALITY`, `DUAL_APPROVAL` dahil) | Servis katmanı | Bölüm 5.4 |
| 13 | Gerekçe nitelik denetimi (boş / tek kelime / şablon metin reddi) | Servis katmanı | Bölüm 7.3; OD4 kapalı |
| 14 | Negatif test seti T6–T18 | Test | Bölüm 12 |

**Değişmeyecek olan:** kiracı izolasyonunun üç katmanı, RLS + FORCE, kiracılar
arası referans yasağı, izin kodlama biçimi, "reddet kazanır", uygulanmış
migration'ların değişmezliği.

---

## 14. OPEN DECISION

Aşağıdaki noktalar belirsiz veya çelişkilidir; **kod yazılmadan önce karar
verilmelidir**. Her biri için seçenekler ve öneri verilmiştir.

**Durum (2026-09-11):** **OD1–OD5, OD19–OD28 KAPATILDI** (15 karar); **OD29 AÇIK**.
Kararlar aşağıda kayıtlı ve ilgili bölümlere (3.4, 5.4, 6.2, 6.3, 7.3, 8.2, 10.1, 13)
işlenmiştir. Kapalı kararlar listede kalır ama **artık karar beklemez**.
Diğer OD'ler açıktır. Veritabanı karşılığı ve migration planı: `docs/DATABASE.md`.

### OD1 — SUPERADMIN'ın kişisel veriye erişimi — **KAPALI** ✅ (2026-09-11)

- **KARAR:** Varsayılan erişim **YOK**. SUPERADMIN kişisel veriye ancak
  **gerekçeli erişim talebi + onay + süreli erişim kaydı** ile ulaşır. Erişim
  süresi dolduğunda **otomatik olarak kapanır**. Erişim ve erişim nedeni
  **denetim kaydına** yazılır.
- **İşlendiği yerler:** bölüm 3.4 (madde 5), bölüm 5.4 (`GRANT_VALID`), bölüm 6.2/A,
  bölüm 6.3, bölüm 7.3, bölüm 10.1, bölüm 13; `docs/SECURITY.md` bölüm 4.2 ve 6;
  `docs/ARCHITECTURE.md` (K19).
- **Uygulama parametresi:** varsayılan süre 60 dakika; üst sınır yapılandırılabilir.
- **Çelişki (kayıt için):** "SUPERADMIN tam yetkilidir" ↔ "kişisel veriler varsayılan olarak
  korunmalıdır".
- **Seçenekler:** (A) Tam yetki — kişisel veri dahil her şeye doğrudan erişir,
  yalnızca kayıt tutulur. (B) Sistem yönetimi tam; kişisel veri **onaylı ve
  süreli** akışla açılır (kendi talebini onaylayamaz). (C) Kişisel veri hiç
  açılmaz; yalnızca maskeli/gruplanmış görünüm.
- **Öneri:** (B). Gerekçe: "tam yetkili" ifadesini sistem yönetimi olarak
  yorumlar; kişisel veri koruması ilkesini teknik olarak anlamlı kılar ve
  denetimde "kim, ne zaman, neden" sorusunu cevaplanabilir tutar.
- **Etki:** Bölüm 6 matrisindeki SUPERADMIN kişisel veri sütunu; OD4 ile birlikte
  onay akışının tasarımı.

### OD2 — Genel kimlik formatı — **KAPALI** ✅ (2026-09-11, kullanıcı kararı)

- **KARAR:** Biçim kesinleşti: **kiracı/şirket ön eki + Crockford Base32 + 8 rastgele
  karakter** (örnek: `acme-7F3K9QW2`). Kimlik **tahmin edilemez** ve **benzersiz**
  olmalıdır. Sıralı/sayılabilir biçim (`a-sirketi-00XXXX`) **reddedildi**.
- **İşlendiği yerler:** bölüm 8.2/8.3 (biçim ve kurallar), bölüm 13 (geçiş planı 1. iş),
  `docs/DATABASE.md` bölüm B.3/M5/F1 (kolon, üretim fonksiyonu, geri doldurma).
- **Doğurduğu alt soru:** kiracı `slug`'ı değişirse mevcut kimliklerin ön eki ne olur
  → **OD25** olarak işaretlendi (öneri: ön ek sabit kalır).
- **Seçenekler (kayıt için):** (A) `acme-7F3K9QW2`. (B) Sıralı `a-sirketi-00XXXX`.
  (C) Ön eksiz global rastgele. → **(A) seçildi.**

### OD3 — Platform kullanıcılarının barındırılması — **KAPALI** ✅ (2026-09-11)

**Uygulama durumu (2026-09-11 — M1–M5):** **şema/rol tarafı UYGULANDI ✅** —
`platform.users` (kiracı tablosuyla birleşmez; `tenant_id` kolonu **yok**,
`hr.users` FK'si **yok**, `is_staff`/`is_superuser` **yok**), `platform.roles`
(kapalı küme: `superadmin`, `platform_admin`), `platform.role_permissions`
(`hr.permissions` kataloguna bağlanır, `hr.roles`'a **bağlanmaz**),
`platform.user_roles`; ayrı DB rolü `persopyro_platform`
(`NOBYPASSRLS`) ve `persopyro_app` için platform şemasında **sıfır yetki**.
Kalan: erişim talebi/onay katmanı (M17–M19) ve platform uç noktaları.

**Yetkilendirme paketi (2026-09-11 — M6/M31/M32/M33):** **UYGULANDI ✅** — platform
izin kataloğu ortak katalogda tanımlandı (6 kod, salt okunur katalog); roller açık
izin bağlarıyla seed edildi (superadmin 6/6, platform_admin 4/4, **devralma yok**);
tek merkezi çözümleyici (`apps/platform/permissions.py`) aktiflik/soft-delete/
süre kurallarını fail-closed dayatır; **self-elevation yasak** (kendine atama,
sahip olmadığı yetkiyi dağıtma, hiyerarşi ihlali reddedilir); ilk SUPERADMIN
`manage.py create_platform_superadmin` ile, parola hash'li ve açık yönetim
operasyonu olarak oluşturulur. **Login uç noktası yok** (bu paketin kapsamı dışı);
servis/model guard'ları ve testleri vardır.

- **KARAR:** SUPERADMIN ve ADMIN için **ayrı `platform_users` modeli** kullanılır.
  Bu kullanıcılar **kiracıya bağlı olmaz**; kiracı kullanıcıları ile platform
  kullanıcıları **aynı kimlik/kiracı tablosunda zorla birleştirilmez**. Kiracı
  RLS kuralları platform kullanıcıları için **gevşetilmez** (politikaya istisna
  eklenmez). **`BYPASSRLS` kullanılmaz.** Platform erişimi **ayrı ve kontrollü bir
  servis/authorization katmanı** üzerinden tasarlanır.
- **Tasarım sonucu:** platform verisi ayrı şemada; kiracı yolu (`persopyro_app`)
  ile platform yolu (`persopyro_platform`) ayrı veritabanı rolleri; kiracı
  bağlamını yalnızca platform katmanı, geçerli bir erişim onayıyla ve **tek işlem**
  için kurar (bölüm 3.4).
- **Seçenekler (kayıt için):** (A) `users.tenant_id` boş bırakılır + RLS istisnası.
  (B) Ayrı `platform_users` + ayrı giriş yolu. (C) Teknik "platform kiracısı"
  satırı. → **(B) seçildi; (A) ve (C) reddedildi.**
- **İşlendiği yerler:** bölüm 3.4, bölüm 6.2/A, bölüm 13; `docs/SECURITY.md`
  bölüm 4.2 ve 5; `docs/ARCHITECTURE.md` (K19, K21, K22).
- **Çelişki (kayıt için):** Mevcut şemada her kullanıcı bir kiracıya bağlıdır
  (`users.tenant_id` zorunlu ve RLS ile korunur). SUPERADMIN/ADMIN hiçbir
  kiracıya ait olmamalıdır.
- **Seçenekler:** (A) `users.tenant_id` boş olabilir + RLS politikasında platform
  istisnası. (B) Ayrı `platform_users` tablosu ve ayrı kimlik doğrulama yolu.
  (C) "Platform" adında teknik bir kiracı satırı (hack).
- **Öneri:** (B). Kiracı verisiyle platform verisi şemada ayrılır; RLS politikası
  kiracı tablolarında **hiç değişmez** (izolasyon garantisi bozulmaz); platform
  tarafı kendi denetim akışına sahip olur. Maliyeti: iki giriş yolu, ortak kod
  paylaşımı için dikkatli servis tasarımı.
- **Etki:** OD1/OD8 ile birlikte platform erişim mimarisini belirler. Bu karar
  verilmeden 2, 3, 4 numaralı geçiş işleri başlayamaz.

### OD4 — Kişisel veri erişim talebini kim onaylar — **KAPALI** ✅ (2026-09-11)

- **KARAR:** Kiracı içindeki kişisel veri erişimlerinde **şirket yöneticisi / onay
  yetkilisi** temel onay mekanizmasıdır. Platform tarafındaki erişimlerde **ikinci
  kontrol (çift onay)** kullanılır. **Boş veya anlamsız gerekçe ile erişim talebi
  oluşturulamaz.** Her **talep, onay, red, erişim başlangıcı ve erişim bitişi**
  denetim kaydına yazılır.
- **Koşullar:** `SEPARATE_ACTOR` (talep sahibi onaylayamaz), `PURPOSE_QUALITY`
  (gerekçe niteliği), `DUAL_APPROVAL` (platformda iki farklı onaylayıcı) — bölüm 5.4.
- **İşlendiği yerler:** bölüm 5.4, bölüm 6.2/I, bölüm 7.3, bölüm 10.1,
  `docs/SECURITY.md` bölüm 6.
- **Doğurduğu açık nokta:** tek platform yöneticisi bulunması durumunda çift onayın
  nasıl uygulanacağı → **OD17**; kiracının onay sürecindeki rolü → **OD18**.
- **Seçenekler (kayıt için):** (A) Yalnızca ŞİRKET YÖNETİCİSİ (veri sorumlusu kiracıdır).
  (B) Yalnızca SUPERADMIN. (C) Kişinin kendisi (veri sahibi onayı). (D) Kiracı
  yöneticisi + platform çift kontrolü (kullanım amacına göre).
- **Öneri:** (A) + platform taleplerinde çift kontrol. Gerekçe: KVKK'da veri
  sorumlusu kiracıdır; platform kendi çalışanının talebini tek başına
  onaylamamalıdır.
- **Etki:** Onay akışı, bildirimler, arayüz ekranları.

### OD5 — Departman yöneticisinin kapsamı — **KAPALI** ✅ (2026-09-11) — **M7–M13 ✅ UYGULANDI** (2026-09-11)

**Uygulama durumu:** `hr.roles.scope_type` (M8), `hr.user_roles.scope_org_unit_id`
(M9 → gerçek FK, M9 tamamlandı) ve organizasyon/çalışan tabloları (M10–M13)
**uygulandı**; kapsam **gerçek alt ağaçtan** hesaplanır (recursive CTE, derinlik ≤ 20).
Kapsam çözülemezse `UNIT`/`UNIT_DIRECT` ataması **izin üretmez** (fail-closed).

**KARAR (A) seçildi:** `dept_manager` rolünün organizasyon kapsamı, **atandığı
organizasyon birimi + o birimin tüm alt birimleridir** (kapsam kodu **`UNIT`**).
(B) `UNIT_DIRECT` kapsam kodu §4.4'te **korunur** ama `dept_manager` için kapalı
davranış `UNIT`'tir. (C) "birim bazında yapılandırılabilir" ihtiyacı **yeni kapsam
kodu üretmeden** karşılanır: kapsam **tipi** rol düzeyinde bildirilir
(`hr.roles.scope_type`, M8), kapsamın **hangi birim** olduğu atama düzeyinde
sabitlenir (`hr.user_roles.scope_org_unit_id`, M9).

#### 5.1 Kapsam modeli (kesin)

```
izinli(işlem, kayıt) = (kullanıcı ilgili izin koduna sahip)  ∧  (kayıt etkin organizasyon kapsamı içinde)
```

- **İki eksen ayrıdır:** RBAC *"hangi işlemi yapabilir?"*, kapsam *"hangi
  çalışan/birim üzerinde?"*. Kapsam **izin üretmez**, izni **daraltır**; izin de
  kapsam üretmez. İkisinden biri yoksa sonuç **reddedilir** (fail-closed).
- Kapsam tipi kapalı kümedir (§4.4): `SYS`, `TENANT`, `UNIT`, `UNIT_DIRECT`,
  `ASSIGNED`, `SELF` — **yeni kod eklenmedi**.
- Kapsam kaynağı **`hr.user_roles.scope_org_unit_id`**'dir (§4.4; `docs/DATABASE.md`
  M9). Bir `dept_manager` ataması **`tenant_id` + `role_id` + `scope_org_unit_id`**
  üçlüsüyle açıkça bağlıdır.
- Rolün `scope_type` alanı **azami** kapsamı bildirir; kişi bazında atama yalnızca
  **daraltabilir, genişletemez** (§4.4).
- **Fail-closed:** `UNIT`/`UNIT_DIRECT` tipli bir rol için `scope_org_unit_id IS NULL`
  ise atama **hiçbir kapsam üretmez** ve istek reddedilir. **NULL hiçbir zaman "tüm
  kiracı" anlamına gelmez.** Kiracı geneli yetki, **`TENANT` tipli ayrı bir rolle**
  verilir (`tenant_admin`, `tenant_officer`).

#### 5.2 Alt birim (subtree) davranışı

- Alt kapsam **OD24** modeliyle hesaplanır: `departments.parent_id` (adjacency list) +
  **`WITH RECURSIVE`** CTE. v1'de **closure table / materialized path / nested set
  yok**.
- Derinlik sınırı **20** (OD24 + ORGANIZATION §4); CTE yürüyüşü de 20 adımla
  sınırlıdır (bozuk ağaca karşı son savunma).
- Döngü koruması **veritabanı tetikleyicisinden** gelir (M11 / ORGANIZATION §4);
  kapsam çözümlemesi döngü yokmuş gibi davranmaz, döngü DB'de engellenir.
- Sorgu `(tenant_id, parent_id)` indeksiyle başlar; sonuç kısa TTL'li önbellekte
  tutulursa **birim taşıma veya yönetici değişiminde geçersiz kılınır**
  (ORGANIZATION §3).
- Kapsam **güncel** ağaca göre hesaplanır; **kapsam kaydı anlık görüntü (snapshot)
  değildir**.

#### 5.3 Çoklu atama (multi-scope) davranışı

- Bir kullanıcı **aynı kiracı içinde** birden fazla `dept_manager` ataması taşıyabilir
  (ör. Finans + Satın Alma).
- **Etkin organizasyon kapsamı = etkin atamaların birleşimidir (union)**; kesişim
  değildir. Birleşim, rol `scope_type`ının izin verdiği azami kapsamla sınırlıdır.
- Sınırlar (hepsi zorunlu):
  * **Kiracılar arası birleşim YOKTUR** (RLS + `tenant_id` guard'ı); kapsam daima tek
    kiracı içinde hesaplanır.
  * **Soft-delete edilmiş atama kapsam üretmez.**
  * **Süresi geçmiş atama** (`expires_at <= now()`) kapsam üretmez.
  * **Pasif/silinmiş kullanıcı** hiç kapsam üretmez (F7 ilkesi).
  * Kullanıcı **kendi atamasını/kapsamını yükseltemez** (§4.3 self-elevation yasağı).
- Bu yaklaşım **uygun bulundu ve kesinleşti**: birleşim tek kiracı içinde kaldığı
  sürece tenant izolasyonunu aşmaz; genişlik yalnızca atanmış birimlerin alt
  ağaçlarının toplamı kadardır ve her genişleme açık bir atama işlemi gerektirir.

#### 5.4 Kiracı geneli (tenant-wide) kapsam ayrımı

- Kiracı geneli yetki **`TENANT` tipli rolden** gelir: `tenant_admin` (tam),
  `tenant_officer` (izin bazlı). **`scope_org_unit_id = NULL` ile modellenmez.**
- `SYS` kapsamı yalnızca platform rollerine aittir (§4.4); kiracı rollerinde
  kullanılamaz.
- Bir kullanıcı aynı anda `TENANT` ve `UNIT` tipli rollere sahipse etkin kapsam
  **en geniş** olanıdır (TENANT); `UNIT` ataması TENANT kapsamını daraltmaz. Bu
  bilinçli bir yönetim kararıdır ve denetim kaydına yazılır. "**En dar kazanır**"
  kuralı (§4.4) rol tipleri arasında değil, **rol azami kapsamı ∩ atama kapsamı ∩
  kayıt kapsamı ∩ istek kapsamı** eksenleri arasında uygulanır.

#### 5.5 `employee_manager` ile kesin ayrım

- **`employee_manager`:** **çalışan bazlı** raporlama/onay ilişkisi
  (`primary`/`secondary`/`delegate`, tarihli) — ORGANIZATION §6.
- **`dept_manager`:** **RBAC rolünün organizasyon birimi kapsamı** (bu karar).
- **Bir çalışanın birincil yöneticisi olmak `dept_manager` izin seti KAZANDIRMAZ**;
  **`dept_manager` rolü olmak `employee_manager` satırı ÜRETMEZ.** İki kaynak
  birbirini türetmez.
- `departments.manager_employee_id` (birim sorumlusu) da **yetki üretmez**; bilgi ve
  varsayılan atama önerisi olarak kullanılır.
- Onay/izin workflow'unda iki bilgi **birlikte** kullanılabilir (`ASSIGNED` koşulu,
  `approval_steps.approver_user_id` / `approver_role_code + UNIT`), fakat
  **authorization kaynakları ayrı kalır**.

#### 5.6 Kişisel veri sınırı

- **"Çalışanı yönetebilir" ≠ "K2/K3 kişisel verisinin tamamını görebilir".**
- `employees` PII **içermez**; kişisel veri `employee_personal_data` içindedir
  (ORGANIZATION §2) ve **ayrı izinler** (`employee.sensitive.read`,
  `employee.personal_data.read`) + **koşullar** (`MFA_OK`, `PURPOSE_GIVEN`,
  gerekiyorsa `GRANT_VALID`) gerektirir (§7).
- Kapsam yalnızca *"hangi kayıt"* sorusunu yanıtlar; **maskeleme, süreli/onaylı
  erişim ve sınıflandırma kuralları aynen geçerlidir** (§7.2). Kapsam içindeki bir
  çalışanın K3 alanı yine maskelidir.
- `dept_manager` kapsamı **toplu görüntüleme/dışa aktarma** (`employee.export`)
  hakkı vermez; ayrı izin gerekir.

#### 5.7 Soft-delete / restore davranışı

- Departman soft-delete edilirse: atama satırı **fiziksel silinmez** (denetim izi
  korunur) ama **artık kapsam üretmez** → authorization fail-closed.
- Departman **restore edilse bile atama sessizce yeniden etkinleşmez** (OD21/K25:
  sessiz yeniden etkinleştirme yasak). Yetkinin geri gelmesi için atamanın **açık ve
  denetlenen** bir işlemle yeniden etkinleştirilmesi gerekir (`role.manage` +
  `*.restore` + denetim kaydı).
- "Silinmiş birim + etkin atama" kombinasyonu **tutarlılık denetiminde raporlanır**
  (M21 tarzı); otomatik onarım **yapılmaz**.

#### 5.8 Taşınan birimlerin (move) etkisi

- Kapsam, **"o birimin güncel alt ağacı"** olarak tanımlıdır: birim taşınırsa kapsam
  **yeni yapıya göre** yeniden hesaplanır (büyüyebilir veya küçülebilir).
- Bu davranış **bilinçli kabul edildi**; snapshot kapsam tutmak "yanlış ağaçta kalmış
  sessiz yetki" riski üretirdi.
- **Zorunlu karşı önlemler:** (a) önbellek geçersiz kılınır (ORGANIZATION §3),
  (b) ağaç değişikliği **yüksek etkili denetim olayı** olarak yazılır (aktör, eski/yeni
  `parent_id`, gerekçe), (c) döngü ve derinlik ihlali **veritabanında** reddedilir
  (M11).
- Yetki büyümesi yalnızca **açık bir yönetim işlemiyle** (birim taşıma) oluşur; gizli
  yükseltme yolu değildir.

#### 5.9 M7–M13 implementasyon etkisi (kesinleşti — ve M7/M8/M9 sonucu)

| İş | OD5 kaynaklı kesin gereksinim | Durum |
|---|---|---|
| **M7** | `manager → dept_manager` göçünde rol **`UNIT`** tipine geçer; `tenant_officer` **`TENANT`** tiplidir; göç sonrası **kapsamsız `dept_manager` ataması bırakılmaz** (varsa göç sırasında raporlanır/hata verir — fail-closed) | ✅ UYGULANDI (`rbac/0005`): yerinde adlandırma, izin setleri DY/ŞYT, conflict + soft-delete fail-closed. **M10 öncesi kapsamsız `dept_manager` ataması izin üretmez** (kapsam çözülemez) — yani "sessiz geniş yetki" yolu kapalı |
| **M8** | `hr.roles.scope_type` kapalı küme (`TENANT`, `UNIT`, `UNIT_DIRECT`, `ASSIGNED`, `SELF`) + dokümandaki backfill; varsayılan şablonlarda `dept_manager` = `UNIT` | ✅ UYGULANDI (`rbac/0006`): NOT NULL + DB `CHECK`; backfill eşlemesi uygulandı |
| **M9** | `hr.user_roles.scope_org_unit_id` (FK + aynı kiracı guard'ı) + **guard tetikleyicisi**: rol `scope_type ∈ {UNIT, UNIT_DIRECT}` ise `scope_org_unit_id` **zorunlu**; benzersizlik `(user_id, role_id, scope_org_unit_id)` **`NULLS NOT DISTINCT`** | ✅ **TAMAMLANDI** (`rbac/0007` + `0008_od5_scope_org_unit_fk` + `0009`/`0010`): kolon, benzersizlik, **gerçek FK (`hr.departments.id`)**, kiracı guard'ı ve "aktif birim" denetimi; devre dışı bırakma serbest |
| **M10–M13** | `departments`/`positions`/`employees`/`employee_manager`: kapsam hesabı için `(tenant_id, parent_id)` indeksi (M24) ve `employees.department_id` zorunluluğu | ✅ UYGULANDI (`organization/0001`–`0009`): tablolar + `department_id` NOT NULL + kısmi benzersizlikler + kiracı guard'ları + RLS/FORCE; alt ağaç çözümlemesi tek noktada |
| **M14** | İzin modülü: talep görünürlüğü/onayı kapsam ile daraltılır; `ASSIGNED`/`SEPARATE_ACTOR` koşullarının veri kaynağı `approval_steps` | ✅ UYGULANDI (`leave/0001`–`0004`, `rbac/0011`): `izin ∧ kapsam` kararı `apps/leave/services/authorization.py`; onay sırası (birincil yönetici → yetkili rol) atlanamaz; bakiye olay defteriyle izlenir; izin gerekçesi (K2) denetim kaydına yazılmaz |
| **Resolver** | **organization scope resolver** (`scope_org_unit_id` → alt ağaç kimlikleri, kiracı bağlamında) + **permission resolver** ile birleşik karar: `izin ∧ kapsam` | ✅ UYGULANDI (`apps/rbac/services/scope.py`): recursive CTE ile gerçek alt ağaç (≤ 20), `UNIT_DIRECT` ayrımı, çoklu atama birleşimi, fail-closed; nesne kararı `object_in_org_scope()` + çalışan hedefi için `can_access_employee()`. İki görünüm: `declared_permission_codes` (sahiplik) / `effective_permission_codes` (kullanılabilir) |
| **Testler** | T7 (kendini yükseltme), alt ağaç/kardeş birim ayrımı, multi-scope birleşim, NULL fail-closed, silinmiş birim/atama, süre bitimi, move sonrası yeniden hesaplama, PII ayrımı | ✅ UYGULANDI: M7–M9 testleri + M10–M13 testleri (`tests/test_organization_m10_m13.py`, 56 test): alt ağaç/kardeş birim, çoklu kapsam birleşimi, silinmiş birim/atama, süre bitimi, move sonrası **yeniden hesaplama**, izin ∧ kapsam (ALLOW/DENY), K2/K3 ayrımı |

#### 5.10 Güvenlik etkisi

- Fail-closed ve "reddet kazanır" korunur; kapsam **izin üretmez**.
- Kapatılan yükseltme yüzeyleri: NULL kapsam ("tüm tenant" sayılmaz), silinmiş birim,
  silinmiş/süresi geçmiş atama, pasif kullanıcı, kiracılar arası birleşim, kendine
  atama/kapsam genişletme, `manager_employee_id`'den türetilen yetki.
- **Kalan risk:** önbellek bayatlığı (taşıma sonrası eski alt ağaç) → kısa TTL + yazma
  yolunda geçersiz kılma + test (bölüm 5.8).
- PII erişimi kapsamdan **türetilmez**; maskeleme/onay kuralları bağımsız çalışır.

#### 5.11 Mevcut kod/şema ile çakışma (karar anı ölçümü — ve M7–M9 sonrası durum)

- **Karar anında çakışma yoktu.** `hr.roles.scope_type` ve `hr.user_roles.scope_org_unit_id`
  o tarihte **mevcut değildi**; mevcut izin çözümleyicisi kapsam boyutunu **hiç**
  uygulamıyordu → karar mevcut davranışı bozmadı, uygulanacak yeni katmanı tanımladı.
- **M7–M9 sonrası (2026-09-11):** her iki alan **uygulandı** (M9 kısmi — FK M10'da).
  Mevcut izin setleri korunmuştur: `tenant_admin` 29, `hr_manager` 22, `employee` 6,
  `dept_manager` 7→14; `tenant_officer` 19 ile **yeni**; atama sayısı **korundu** (ölçüm: 1).
  Tek davranış değişikliği: `UNIT` tipli bir rolün ataması kapsam çözülene kadar izin
  **üretmez** (fail-closed) — bu, istenen ve belgelenen davranıştır.
- `UNIT_DIRECT` ve `ASSIGNED` kodları §4.4'te zaten tanımlıydı; **yeni kapsam kodu
  eklenmedi**, yeni isim uydurulmadı (`SYS` tenant kümesine alınmadı).
- Tek belirsizlik `dept_manager` (eski `manager`) kapsamıydı → **`UNIT`** olarak
  sabitlendi.

- **Çelişki (kayıt için):** "yönetici tüm alt birimleri görsün" (operasyonel
  sadelik) ↔ "kapsam izin üretmesin, yetki yükseltme yolu açılmasın" (en az yetki).
  Karar ikinciyi birinciyi daraltmadan karşılar: alt ağaç yalnızca **görünürlük/işlem
  kapsamıdır**, izin seti RBAC'ten gelir.
- **Seçenekler (kayıt için):** (A) Alt ağaç. (B) Yalnızca doğrudan birim.
  (C) Birim bazında yapılandırılabilir. → **(A) seçildi**, (C) rol tipi + atama
  birimi olarak karşılandı, (B) `UNIT_DIRECT` kodu olarak korundu.
- **Etki:** kapsam çözümlemesi, vardiya/izin onayı testleri, matris satırları
  (§6.2/A ve §6.4 satırlarındaki `UNIT` kapsamı artık tanımlıdır).

### OD6 — Silme mi, arşivleme mi

- **Soru:** Hangi kayıtlar gerçekten silinir? (çalışan, evrak, departman, izin
  kaydı). Hukuki saklama süreleri nelerdir?
- **Öneri:** Varsayılan mantıksal silme + arşiv; gerçek silme yalnızca KVKK
  talebi veya saklama süresi sonunda, kayıtlı bir iş akışıyla.
- **Etki:** Evrak silme yetkileri (§9.2), `employee.delete`, denetim kayıtları.

### OD7 — Şirket yöneticisinin kendi çalışan verisini silme yetkisi

- **Çelişki:** Kiracı veri sorumlusudur ama silme işlemi geri alınamaz.
- **Öneri:** Şirket yöneticisi silme **talebi** açar; gerçek silme platform
  tarafından doğrulanır ve kayda geçer. Ara çözüm: yönetici arşivler, silme
  onay gerektirir.

### OD8 — ADMIN'in yönetim talebi kapsamı

- **Soru:** "Şirket yöneticilerinden gelen yönetim/talep işlemleri"nin sınırı ne?
  ADMIN hangi somut işlemleri yapabilir (kiracı ayarı değiştirme, kullanıcı
  pasifleştirme, veri düzeltme)?
- **Öneri:** Yalnızca **tanımlı bir işlem kataloğu** (destek ve yapılandırma
  işlemleri) üzerinden; kiracı iş verisine yazma yok. Her işlem talep kaydına
  bağlanır.
- **Etki:** Platform modülü izin listesi, arayüz, denetim.

### OD9 — ŞİRKET YETKİLİSİ'nin yetki sınırı

- **Soru:** Yöneticinin verdiği yetkiler hangi izin kodlarından oluşabilir?
  Gizli evrak, kullanıcı oluşturma, izin onayı verebilir mi?
- **Öneri:** Kiracı yöneticisi tarafından atanabilen izinleri **iki gruba**
  ayır: "devredilebilir" (evrak/çalışan operasyonu) ve "devredilemez" (rol
  yönetimi, şirket ayarları, kullanıcı silme). Devredilemez izinler hiçbir
  özel role verilemez.

### OD10 — MFA zorunluluğu hangi rollerde

- **Öneri:** SUPERADMIN, ADMIN, ŞİRKET YÖNETİCİSİ, ŞİRKET YETKİLİSİ için
  **zorunlu**; DEPARTMAN YÖNETİCİSİ için önerilir; ÇALIŞAN için isteğe bağlı.
  Kişisel veri erişimi (`MFA_OK` koşulu) MFA'yı fiilen zorunlu kılar.
- **Etki:** Giriş akışı, kullanıcı kurulumu, kurtarma süreci.

### OD11 — Bir kişi hem platform hem kiracı rolü taşıyabilir mi

- **Öneri:** Hayır (ayrılık ilkesi). Zorunlu durumda ayrı hesap kullanılır ve
  bu durum denetimde görünür olur.

### OD12 — Kişiye özel izin istisnası (override) kullanılmalı mı

- **Soru:** Mevcut altyapı kişi bazlı ekleme/çıkarma destekliyor ✅. Kiracı
  yöneticisi bunu kullanabilmeli mi?
- **Öneri:** Yalnızca **çıkarma** (kısıtlama) serbest; **ekleme** (genişletme)
  yasak — istisnalarla yetki büyümesi denetimi zorlaştırır. Ekleme gerekiyorsa
  özel rol oluşturulur (izlenebilir).

### OD13 — Denetim kaydının görünürlüğü, saklaması ve bütünlüğü

- **Sorular:** Çalışan kendi kaydına yapılan işlemleri görebilmeli mi? Saklama
  süresi kaç yıl? Zincirleme (tamper-evidence) gerekli mi? Denetim kaydı
  yazılamazsa işlem durmalı mı?
- **Öneri:** Çalışana **kendi kaydına** yapılan kritik işlemler gösterilir
  (şeffaflık); saklama 10 yıl; zincirleme uygulanır; güvenlik açısından kritik
  işlemlerde kayıt yazılamazsa işlem geri alınır.
- **Etki:** Denetim ekranları, arşivleme işi, yazma performansı.

### OD14 — Genel kimlik ile arama

- **Soru:** Kullanıcılar e-posta/telefon ile arama yapabilmeli mi?
- **Öneri:** Varsayılan olarak **hayır**; arama genel kimlik, ad (kiracı içi,
  yetkiye bağlı) ve birim üzerinden yapılır. Kişisel veriyle arama yalnızca
  gerekçeli ve kayıtlı modda açılır; dönen sonuç yalnızca eşleşme sayısıdır.

### OD15 — Kiracı, platform erişimini görsün mü

- **Öneri:** Evet. Kiracı yöneticisi, kendi verisine yapılan platform
  erişimlerinin kaydını görür ve itiraz edebilir. Gerekçe: şeffaflık, güven,
  KVKK hesap verebilirliği.
- **Etki:** Platform erişim kaydının kiracı denetim ekranında görünmesi.

### OD16 — Özel rol sınırı ve katalog evrimi

- **Soru:** Kiracı başına özel rol sayısı sınırlanmalı mı? Katalogdan bir izin
  kaldırıldığında mevcut roller ne olur?
- **Öneri:** Kiracı başına 40 özel rol sınırı (tetikleyici: bölüm 2.3); izin
  kaldırılırsa rollerden düşer ama **kayıt silinmez** (denetim izi), rol
  "güncellenmeli" uyarısı alır.

### OD17 — Platform çift onayının uygulanabilirliği (tek yönetici / acil durum) 🆕

- **Neden açık:** OD4 çift onayı zorunlu kıldı. Ancak sistemde **tek** platform
  yöneticisi varsa (veya ikincisi erişilemez durumdaysa) ikinci onayı verecek kimse
  yoktur; tüm kiracıları etkileyen bir acil durumda onay akışı kilitlenebilir.
- **Seçenekler:** (A) En az iki platform yöneticisi bulundurmak zorunlu; yoksa
  kişisel veri erişimi **hiç açılamaz** (fail-closed). (B) Tek kişilik durum için
  önceden tanımlı ikinci hesap (ayrı kimlik bilgisi, elle saklanan). (C) Acil
  durum için zaman damgalı **break-glass**: onay olmadan açılır, sonradan
  **zorunlu gözden geçirme** + kayıt + kiracıya bildirim.
- **Öneri:** (A) + (C). Varsayılan fail-closed kalır; break-glass yalnızca
  SUPERADMIN için, gerekçe ve sonradan inceleme şartıyla açılır.
- **Etki:** Platform giriş/onay akışı, kişisel veri erişim servisi, denetim
  raporu, `docs/SECURITY.md` G6. **Uygulamayı kısmen etkiler** (fail-closed
  varsayılan uygulanabilir; break-glass tasarımı bu karara bağlı).

### OD18 — Platform kişisel veri erişiminde kiracının rolü 🆕

- **Neden açık:** OD4 platform tarafında çift onayı zorunlu kıldı; ancak
  **kiracının** (veri sorumlusunun) bu erişimde **onayı mı**, yoksa yalnızca
  **bilgilendirilmesi mi** gerektiği belirtilmedi. OD15 (kiracı platform erişimini
  görsün mü) ile birlikte değerlendirilmelidir.
- **Seçenekler:** (A) Kiracı yalnızca bilgilendirilir (görür, itiraz edebilir).
  (B) Kiracı onayı da zorunludur (talep + iki platform onayı + kiracı onayı).
  (C) Kiracı onayı yalnızca K3 (özel nitelikli veri) ve toplu erişimde zorunludur.
- **Öneri:** (C). Gerekçe: KVKK'da veri sorumlusu kiracıdır; en hassas veri ve
  toplu erişimde onay alınmalı, rutin destek işleri tıkanmamalıdır.
- **Etki:** Onay akışı adım sayısı, bildirimler, kiracı denetim ekranı (OD15).

### OD19 — Django admin / `auth_*` paralel yetki yolu — **KAPALI** ✅ (2026-09-11) — **UYGULANDI** ✅ (M22–M22d)

- **KARAR (kabul edildi):**
  1. Üretimde **Django Admin kullanıcı yetkilendirmesi kullanılmaz**.
  2. **`/admin/` yolu kaldırılır** (koda bağlı değil; route + uygulama listesinden çıkarılacak).
  3. Django `auth_*` tabloları uygulamanın **gerçek RBAC kaynağı değildir**.
  4. **`is_staff` / `is_superuser` tek başına hiçbir Persopyro yetkisi vermez.**
  5. Gerçek yetkilendirme **yalnızca bu belgedeki model** üzerinden yapılır.
  6. Django'nun teknik tabloları gerekiyorsa **teknik amaçla kalabilir**; izin
     kaynağı olamaz.
  7. İleride Django Admin kullanılacaksa **ayrı güvenlik sınırı ve erişim
     politikası** yazılı olarak tanımlanmadan açılmaz (aşağıdaki koşullar).
- **Ölçüm (mevcut kod, 2026-09-11):** `config/urls.py:30` `/admin/` rotalı;
  `django.contrib.admin` uygulama listesinde; `User.is_staff` alanı var ve
  `create_superuser` onu `True` yapıyor; kurulu Django kaynağında
  `AdminSite.has_permission` = `user.is_active and user.is_staff` (satır 207) →
  **`is_staff` tek başına kapıyı açar**. Bugünkü etki sınırlı: hiçbir model
  kayıtlı değil (`admin.py` yok, `admin.site.register` çağrısı yok) → boş admin
  dizini. Ancak ileride model kaydedilirse RBAC'i atlayan bir yol doğar.
- **Neden tamamen kaldırma (internal-only değil):** Django Admin'in yetki modeli
  (`is_staff` + `has_module_perms`) kendi RBAC'imizle hizalandırılamaz; kalan her
  yol kalıcı bakım, denetim ve yanlış yapılandırma riski üretir. İç ihtiyaçlar
  `platform.*` izinleriyle **kendi katmanımızdan** karşılanır.
- **İmplementasyon kalemleri — TAMAMLANDI ✅ (M22–M22d, 2026-09-11):** `/admin/`
  route'u kaldırıldı (`GET /admin/` → **404**); `django.contrib.admin` uygulama
  listesinden çıkarıldı; `create_superuser` artık `is_staff=True` **atamaz** — açık
  hata verir (`DjangoAdminRemovedError`) ve `manage.py createsuperuser` anlaşılır
  hata basar; `auth_group`, `auth_group_permissions`, `auth_permission`,
  `django_admin_log` tablolarında uygulama rolünün **tüm yetkileri geri alındı**
  (`accounts/0004` + `scripts/02_grant_app_privileges.sql`; regresyon testi betiğin
  daraltmayı geri açmadığını doğrular); `is_superuser` alanı **eklenmedi**; üretimde
  `is_staff = true` olmaması **uygulama guard'ı + DB kısıtı + denetim komutu
  (`audit_is_staff`) + test** ile zorlanır; kaldırma sonrası **sıfırdan kurulum
  testi** yapıldı (geçici DB: `django_admin_log` hiç oluşmadı, yetkiler kapalı, tam
  test suite yeşil).
- **İleride admin kullanımı için ön koşullar:** ayrı alan adı ve ağ erişim
  sınırı; `platform_users` ile kimlik doğrulama (kiracı kullanıcısıyla değil);
  MFA zorunlu; yalnızca `platform.*` izinleriyle erişim; tüm işlemlerin platform
  denetim akışına yazılması; okuma ve yazma yetkilerinin ayrı tanımlanması.
- **İşlendiği yerler:** `docs/SECURITY.md` (bölüm 3, 5, T18, G8),
  `docs/ARCHITECTURE.md` (K23 + Django gerekçesi), `docs/DATABASE.md`
  (A.1.2, M22, D.6, H3, bölüm I).
- **Seçenekler (kayıt için):** (A) Kaldır. (B) Yalnızca internal/okuma amaçlı
  bırak. (C) Olduğu gibi bırak. → **(A) seçildi; (B) yalnızca yukarıdaki
  ön koşullarla yeniden değerlendirilebilir; (C) reddedildi.**

### OD20 — Kiracı üst verisinin sahibi (platform kaydı) — **KAPALI** ✅ (2026-09-11)

**Uygulama durumu (2026-09-11 — M4):** **UYGULANDI ✅** — `platform.tenant_registry`
(minimum alanlar; PII kopyalanmaz), tek yönlü `hr.tenants` → registry yansısı
`platform.sync_tenant_registry()` `SECURITY DEFINER` tetikleyicisiyle **aynı işlem
içinde**; yansı yazılamazsa kaynak yazımı **geri alınır** (fail-closed; regresyon
testi kasten çakıştırılmış registry satırıyla yapılır). Ters yön **yok**: platform
rolü `hr.tenants` üzerinde yazma yetkisine sahip değil; registry'nin
`plan_code`/`billing_ref`/`internal_note` alanları yansı tarafından **ezilmez**.
Kalan: M21 tutarlılık testi + tutarsızlık düzeltme akışı.

- **KARAR:** `hr.tenants` **canonical / source-of-truth** kalır. `platform.tenant_registry`
  tenant verisinin ikinci tam kopyası **değildir**; platform operasyonları için gereken
  **minimum referans indeksi**dir. **İki bağımsız mutable tenant kaynağı oluşturulmaz.**
- **Alan sahipliği (authoritative):**

  | Alan | Authoritative tablo | Registry'de var mı? |
  |---|---|---|
  | `name`, `legal_name`, `tax_number`, `email`, `phone`, `address`, `country`, `city` | `hr.tenants` | ❌ (yalnızca `name` gerekirse yansı) |
  | `slug`, `domain` | `hr.tenants` | ✅ yansı (giriş yönlendirmesi ve listeleme için) |
  | `status` (active/trial/suspended/canceled) | `hr.tenants` | ✅ yansı (platform listeleme/karar için) |
  | `max_users`, `max_storage_mb`, `settings` | `hr.tenants` | ✅ yalnızca limit alanları (raporlama için) |
  | `plan_code`, `billing_ref`, `internal_note` (ticari/operasyon) | **`platform.tenant_registry`** | ✅ tek sahibi platform |
  | `created_at` | `hr.tenants` | ✅ yansı |
  | `deleted_at` | `hr.tenants` | ✅ yansı (liste filtresi) |
- **Senkronizasyon yönü:** **tek yönlü:** `hr.tenants` → `platform.tenant_registry`,
  `SECURITY DEFINER` tetikleyiciyle (mevcut `tenant_directory` ile aynı kanıtlanmış
  desen). Ters yön **yoktur**: platform, kiracı alanlarını registry üzerinden değiştirmez;
  değiştirmesi gerekirse platform servisi, geçerli erişim onayıyla **`hr.tenants`'ı
  kiracı bağlamında** yazar (tek yazar) → tetikleyici yansıyı günceller.
- **Başarısızlık davranışı:** tetikleyici **aynı işlem içinde** çalışır; yansı yazımı
  başarısız olursa `hr.tenants` yazımı da geri alınır → **sapma oluşmaz (fail-closed)**.
  Tetikleyici asla "sessizce yut" yapmaz.
- **Gerekçe:** Kiracı verisinin sahibi kiracıdır (KVKK'da veri sorumlusu); iki mutable
  kaynak, "hangi kayıt doğru?" belirsizliği ve yetki kararlarında (askıya alınmış kiracı
  girişi gibi) tutarsızlık üretir. Tek yönlü yansı, RLS politikalarinı gevşetmeden
  (OD3) platform listelemesini mümkün kılar.
- **Şema/migration etkisi:** `platform.tenant_registry` alan kümesi yukarıdakiyle
  **sınırlandırılır**; yansı tetikleyicisi `hr.tenants` üzerine kurulur; mevcut
  `hr.tenant_directory` **korunur** (giriş öncesi okuma, uygulama rolünde salt-okunur)
  ve registry ile tutarlılığı test edilir (M4, M21, F6, F7).
- **Güvenlik etkisi:** Yansı yalnızca kiracı verisi **olmayan** alanları taşır; registry
  üzerinden kiracı iş verisine erişim doğmaz. Platform alan yazımı yalnızca platform
  rolünde; uygulama rolünün registry'de yetkisi yoktur.
- **Mevcut kodla çakışma:** Bugün `platform` şeması **yok**; `tenant_directory`
  tetikleyicisi (`sync_tenant_directory`) yansı deseninin çalıştığını kanıtlıyor →
  çakışma yok, desen yeniden kullanılıyor. `hr.tenants.slug/domain` tekil kısıtları
  OD21 ile uyumlu hale getirilir.
- **Seçenekler (kayıt için):** (A) `hr.tenants` canonical + minimum yansı. (B) Registry
  canonical. (C) Birleşik görünüm. → **(A) seçildi; (B) ve (C) reddedildi.**

### OD21 — Soft delete ve benzersizlik politikası — **KAPALI** ✅ (2026-09-11)

- **KARAR (standart):**
  1. **`deleted_at`** = silme işareti (audit/geçmiş gerektiren iş kayıtları).
     **`status`** = yaşam döngüsü (active/trial/suspended/canceled, aktif/pasif).
     **`is_active` sütunu açılmaz**; yalnızca `status` + `deleted_at`'ten türeyen
     Python özelliği olarak kalır (mevcut `User.is_active` deseni standarttır).
  2. Fiziksel `DELETE` yalnızca (a) kiracı silme/CASCADE yansıları, (b) KVKK saklama
     süresi sonu anonimleştirme/silme, (c) yeni modülün geri alınmış migration'ı
     için kullanılır. İş kayıtlarında kullanılmaz.
  3. **Sessiz yeniden etkinleştirme yasaktır:** silinmiş kaydın yeniden açılması
     açık bir `*.restore` izni + denetim kaydı gerektirir. `get_or_create` benzeri
     akışlar silinmiş kaydı **canlandıramaz** (silinmişi görmez).
  4. **Denetim kaydı asla soft delete edilmez:** `hr.audit_logs` `deleted_at`
     kolonu **almaz**; append-only + `UPDATE/DELETE` yetkisi yok kuralı korunur.
- **Tablo bazlı politika ve benzersizlik:**

  | Tablo | Silme | Benzersiz alan | Karar |
  |---|---|---|---|
  | `users` | `deleted_at` + `status` (pasifleştirme = status; silme = deleted_at) | `(tenant_id, email)`, `public_id` | e-posta **kısmi** (`WHERE deleted_at IS NULL`) → yeniden kullanılabilir; **`public_id` kısmi DEĞİL** (kimlik asla yeniden kullanılmaz — OD2/OD25) |
  | `employees` | `deleted_at` (işten çıkış = `termination_date` + status) | `(tenant_id, employee_no)` | **kısmi** |
  | `departments`, `positions` | `deleted_at` (boş birim arşivlenir) | `(tenant_id, code)` | **kısmi** |
  | `roles` | `deleted_at` (varsayılan roller silinemez, `is_assignable` ile kapatılır) | `(tenant_id, code)` | **kısmi** |
  | `user_roles` (atama) | `deleted_at` (+ `expires_at`) | `(user_id, role_id)` | **kısmi** |
  | `user_permission_overrides` | `deleted_at` | `(user_id, permission_id)` | **kısmi** |
  | `documents` | `deleted_at` + mantıksal arşiv; hukuki saklama varsa silinemez | varsa belge numarası | **kısmi** |
  | `organization` ilişkileri (`employee_manager`) | `deleted_at` (+ `effective_to`) | aktif çift | **kısmi** (`WHERE deleted_at IS NULL AND effective_to IS NULL`) |
  | `tenants` | `deleted_at` (iptal/arşiv) | `slug`, `domain` | **istisna: kısmi DEĞİL** → şirket slug'ı **kalıcı olarak rezerve** (kimlik karışıklığı, destek/denetim belirsizliği ve `public_id` ön ek tutarlılığı için — OD25) |
  | `audit_logs` | **silinemez** | — | `deleted_at` eklenmez |
  | `permissions` (global katalog) | **silinemez** | `code` (tam) | Katalog salt okunur |
- **Gerekçe:** kısmi indeks, soft-delete ile benzersizlik çatışmasını PostgreSQL
  seviyesinde çözer; veri kaybı olmadan yeniden kayıt açılabilir. Rezerve edilen
  alanlar (tenant slug'ı, `public_id`, katalog kodları) kimlik/denetim tutarlılığını
  korur.
- **Şema/migration etkisi:** Kısmi benzersiz indeksler **yeni migration** ile eklenir
  (uygulanmış migration'lar değiştirilmez; mevcut tam indeksler düşürülüp kısmi
  karşılıkları kurulur). Mevcut tam indeksler: `users_tenant_email_uniq`,
  `roles_tenant_code_uniq`, `user_roles_uniq`, `user_permission_overrides_uniq`
  (ölçüldü). M8, M9, E, F1–F2.
- **Güvenlik etkisi:** **Kritik (bulgu F7, aşağıda).** Soft-delete bugün yetki
  çözümlemesinde **hiç dikkate alınmıyor**; silinmiş rol/atama/istisna yetki vermeye
  devam ediyor. Bu kararla birlikte: (a) `objects` yöneticisi silinmiş kayıtları
  **varsayılan olarak gizler** (varsayılan reddet), (b) `all_objects` yalnızca
  denetim/geri alma yollarında kullanılır, (c) yetki çözümlemesi `deleted_at IS NULL`
  koşulunu **açıkça** uygular, (d) silme ve geri alma işlemleri denetim kaydına yazılır.
- **Mevcut kodla çakışma:** `apps/common/models.py:SoftDeleteModel` var ama **filtreleyen
  yönetici yok**; `apps/rbac/services/permissions.py` `deleted_at` filtresi kullanmıyor;
  `apps/rbac/services/roles.py` `get_or_create` ile silinmiş rolü "var" sayıp atlar.
  Ayrıca `SoftDeleteModel` alan sağlar ama `User`/`Tenant` `deleted_at`'i kendi
  tanımlar (ortak temel tutarlı değil). → Bulgu **F7** + uygulama kalemleri M25/M26.
- **Seçenekler (kayıt için):** (A) Kısmi indeks + `deleted_at` standardı. (B) Silinen
  kaydın benzersiz değerini anonimleştir. (C) Değişiklik yok. → **(A) seçildi; (B)
  yalnızca e-posta gibi doğal anahtarlarda yardımcı olarak kullanılabilir; (C)
  reddedildi.**

### OD22 — Platform API token'larının yeri ve kuralları — **KAPALI** ✅ (2026-09-11)

**Uygulama durumu (2026-09-11 — M3):** **şema/guard tarafı UYGULANDI ✅** —
`platform.tokens`: ham token **yok** (SHA-256 özeti, `UNIQUE`); `platform_user_id`
(owner) ve `purpose` (≥10 karakter) zorunlu; `scope` NOT NULL ve **boş olamaz**;
joker `*` yasak; `token_kind` kapalı küme; `expires_at` NOT NULL **ve** TTL tavanı
DB `CHECK`'iyle zorlanır (`interactive` ≤ 24 saat, `service` ≤ 90 gün — süresiz
token imkânsız); iptal **tek yönlü** ve kayıt silinmez; kapsam/purpose/süre/tür/
hash/owner **değişmez**; **service token superadmin izin kümesiyle üretilemez**
(tetikleyici). Servis katmanı: `apps/platform/services.py`
(`validate_purpose`/`validate_scope`/`validate_ttl`/`issue_platform_token`/
`revoke_platform_token`/`token_is_usable`). Kalan: platform oturum/açma-kapama
uç noktaları ve `last_used_at` güncelleme yolu (bu paketin kapsamı dışında).

- **KARAR:**
  1. Platform API/service token'ları **yalnızca `platform.tokens`** tablosunda tutulur.
     `hr.api_tokens`, tenant kullanıcı tabloları veya Django `auth_*`/permission
     yapılarıyla **karıştırılmaz** (OD19: `auth_*` izin kaynağı değildir).
  2. **Ham token DB'de saklanmaz**; yalnızca SHA-256 özeti (`token_hash`, benzersiz).
     Üretim değeri yalnızca üretim anında bir kez gösterilir.
  3. Zorunlu alanlar: `owner` (`platform_user_id`), `purpose` (zorunlu, anlamlı metin),
     `scope` (**zorunlu**, izin kodu listesi), `token_kind` (`interactive`/`service`),
     `created_at`, `expires_at` (**NOT NULL**), `revoked_at`, `last_used_at`, `label`.
     Ham token alanı **yoktur**.
  4. Platform token'ı **hiçbir koşulda tenant RLS sınırını aşmaz**: platform DB rolü
     `NOSUPERUSER`/`NOBYPASSRLS`'tir (OD3) ve kiracı tablolarına erişim yalnızca geçerli
     `AccessGrant` ile, tek işlem için, denetim kaydıyla açılır (OD1/OD4).
  5. Kiracı verisi gerektiğinde token **doğrudan bağlam kuramaz**; mevcut platform
     authorization + erişim onayı mekanizmasından geçer.
  6. **Uzun ömürlü sınırsız SUPERADMIN token yasaktır.** Somut sınırlar:
     `interactive` token TTL ≤ **24 saat**; `service` token TTL ≤ **90 gün** ve
     **superadmin izin kümesiyle üretilemez** (yetki yükseltme yasağı). Boş `scope`
     ile token üretimi **reddedilir** (varsayılan reddet).
  7. İptal olayları: `revoked_at` yazılır (kayıt silinmez — denetim izi); rol değişimi,
     kullanıcı pasifleştirme, MFA değişimi ve parola değişiminde ilgili token'lar
     **otomatik iptal** edilir.
- **Gerekçe:** Platform token'ları uygulamanın en ayrıcalıklı kimlik bilgileridir; ayrı
  şemada tutulmaları (a) RLS varsayımlarını bozmaz, (b) `tenant_id` zorunluluğunu
  delmeyi gerektirmez, (c) kapsam/süre sınırlarıyla "kalıcı arka kapı" riskini (T12)
  kapatır.
- **Şema/migration etkisi:** `platform.tokens` (M3) + iptal indeksi
  (`(revoked_at, expires_at)` kısmi) + `scope` için `CHECK (array_length(scope,1) > 0)`.
  Tenant token modeli (`hr.api_tokens`) **değişmez** (aşağıdaki asimetri notu).
- **Güvenlik etkisi:** Kapsam zorunluluğu, en az yetkiyi token seviyesine indirir;
  `expires_at` zorunluluğu kalıcı token'ı imkânsız kılar; `revoked_at` ile iptal
  **kanıtlanabilir** olur. Platform token'ı hiçbir yolda RLS'i atlatamaz.
- **Mevcut kodla çakışma / asimetri (bulgu F8):** Mevcut `hr.api_tokens` (tenant)
  **kapsamsız**, **`revoked_at`'siz** (iptal = satır silme) ve sabit 30 gün TTL'lidir
  (`API_TOKEN_TTL_HOURS=720`). Bu, tenant token'ının kullanıcının **tüm** yetkilerini
  taşıdığı ve iptalin denetim izinin kaybolduğu anlamına gelir. Platform tarafında bu
  **kabul edilmez** (yukarıdaki 6. madde). Tenant token modelinin hizalanması ayrı bir
  karar gerektirdiği için **OD26** olarak açıldı; bu turda tenant modeli değiştirilmedi.
- **Seçenekler (kayıt için):** (A) Ayrı `platform.tokens`. (B) `hr.api_tokens`'ta
  `tenant_id` nullable + `actor_kind`. (C) Platform için token yok (yalnızca oturum).
  → **(A) seçildi; (B) mevcut RLS/FK varsayımlarını zayıflattığı için reddedildi;
  (C) servis entegrasyonları için yetersiz.**

### OD23 — Denetim kaydında platform aktörünün temsili — **KAPALI** ✅ (2026-09-11)

- **KARAR:**
  1. `hr.audit_logs.actor_kind` **zorunlu** ve **kapalı küme**: `tenant_user`,
     `platform_user`, `service`, `system` (`NOT NULL DEFAULT 'tenant_user'` + DB `CHECK`).
  2. `user_id` (mevcut `user` FK) **yalnızca `tenant_user`** için kullanılır; diğer
     aktör türlerinde **NULL** kalır.
  3. Platform aktörü için **tenant tablosuna FK kurulmaz**: `platform_actor_id uuid`
     (FK'sız) + `actor_label` (snapshot: genel kimlik + rol kodu) tutulur. Böylece
     tenant yazma yolu platform şemasına **sıkı bağlanmaz** ve aktör silinse/yeniden
     adlandırılsa bile kayıt anlamlı kalır.
  4. Platform erişimli her olayda zorunlu alanlar: `actor_kind='platform_user'`,
     `platform_actor_id`, `actor_label`, **`access_grant_ref`** (onay kaydı
     referansı, FK'sız), **`purpose`** (gerekçe), `tenant_id` (**hedef kiracı**),
     `action`, `created_at`. DB kısıtı:
     `CHECK (actor_kind <> 'platform_user' OR (purpose IS NOT NULL AND access_grant_ref IS NOT NULL))`
     → onaysız/gerekçesiz platform erişimi **kayıt dışı kalamaz**.
  5. **Sadeleştirme:** ayrı `source` ve `changed_fields` kolonları **eklenmez**;
     aktör türü `actor_kind` içinde, alan düzeyi bilgi mevcut `changes` (jsonb,
     maskeli) içinde yaşar.
  6. **Append-only korunur:** `AuditLog.user` ilişkisi `SET_NULL` yerine **`PROTECT`**
     olur (denetim satırı hiçbir durumda güncellenmez; kullanıcı fiziksel silinmez — OD21).
  7. **Kiracısız olaylar bu tabloya yazılmaz**; onların evi `platform.audit_logs`'tur
     (K22). `service`/`system` aktörleri, **kiracı verisini değiştiren otomasyon**
     için tenant akışında kullanılır (ör. kuyruk işi).
- **Gerekçe:** Tek tabloda hem kiracı kullanıcısını hem kontrollü platform erişimini
  izlemek gerekir; ancak platform şemasına FK, tenant yazma yolunu kırılgan yapar ve
  aktör silindiğinde kaydı anlamsızlaştırır. FK'sız referans + snapshot etiket +
  DB `CHECK`, kaydı hem **bağımsız** hem **kanıtlanabilir** kılar.
- **Şema/migration etkisi:** `hr.audit_logs`'a `actor_kind` (NOT NULL, default),
  `platform_actor_id`, `actor_label`, `access_grant_ref`, `purpose` kolonları +
  1 `CHECK` kısıtı; `user_id` FK davranışı `SET_NULL` → `PROTECT` (yeni migration;
  uygulanmış migration'lar değiştirilmez). M20 güncellenir; F8 geri doldurma:
  mevcut satırlar `actor_kind='tenant_user'` (user_id doluysa) / `'system'`.
- **Güvenlik etkisi:** Platform erişiminin **gerekçe + onay referansı olmadan**
  kaydedilmesi veritabanı seviyesinde imkânsızlaşır (T12/T15 savunması); denetim
  satırının UPDATE edilmesi yolunu kapatan `PROTECT` kararı **F9** bulgusunu kapatır.
- **Mevcut kodla çakışma:** `apps/audit/models.py` yalnızca `tenant/user/action/...`
  taşır; `platform_actor_id`/`purpose`/`grant` alanları **yok**; `user` ilişkisi
  `SET_NULL` (append-only ile çelişki → bulgu **F9**). `log_action(...)` imzası yeni
  alanları alacak şekilde genişletilecek (kod yazılmadı).
- **Seçenekler (kayıt için):** (A) `user_id` NULL + `actor_kind` + FK'sız platform
  referansı. (B) Ayrı `hr.platform_actor` tablosu + FK. (C) Yalnızca platform
  akışına yaz. → **(A) seçildi** (kullanıcının önerisiyle uyumlu; (B) tenant↔platform
  sıkı bağı kurar, (C) şeffaflık şartını karşılamaz).

### OD24 — Organizasyon ağacı DB modeli — **KAPALI** ✅ (2026-09-11)

- **KARAR:**
  1. **Temel kaynak (v1):** `hr.departments` (birim ağacı, `parent_id` ile
     **adjacency list**), `hr.positions`, `hr.employees`, `hr.employee_manager`
     (kişi bazlı yönetici/raporlama ilişkisi).
  2. **Hiyerarşi çözümü:** yalnızca **özyinelemeli CTE** (PostgreSQL `WITH RECURSIVE`).
     **Materialized path, nested set ve closure table v1'de EKLENMEZ.**
  3. **Kapsam kullanımı:** birim ağacı `UNIT` kapsamını (birim + alt birimler)
     hesaplar; `employee_manager` kişi bazlı zinciri (kendi ekibi, birincil yönetici)
     verir. **Ağaç yetki mirası üretmez** — RBAC ayrı kalır; ağaç yalnızca
     **yönetim/scope hesaplaması** için kullanılır.
  4. **Birincil yönetici:** tek kaynak `hr.employee_manager`; `relation_type`
     (`primary` | `secondary` | `delegate`) + kısmi benzersiz indeks
     (`WHERE relation_type='primary' AND effective_to IS NULL AND deleted_at IS NULL`)
     → çalışanın **tek birincil** yöneticisi olur, ikincil/tarihsel ilişkiler açıktır.
     `hr.departments.manager_employee_id` ise **birim sorumlusudur** (onay
     yönlendirmesi); ikisi farklı anlam taşır, çift kayıt oluşturmaz.
  5. **Döngü yasağı (DB garantisi):** `CHECK (parent_id IS DISTINCT FROM id)` +
     `BEFORE INSERT OR UPDATE` tetikleyicisi, aynı kiracı içinde ata zincirini
     yürüyerek (derinlik sınırı 20) döngüyü **hata ile reddeder**; `employee_manager`
     için `CHECK (employee_id <> manager_employee_id)` + aynı tetikleyici deseni.
     Çözümleyici CTE'ler de **derinlik sınırlı** yazılır (bozuk veriye karşı son savunma).
  6. **Kiracı sınırı:** `parent_id`, `manager_employee_id`, `department_id` vb. her
     kiracı-içi FK mevcut `ensure_same_tenant_ref` guard'ına bağlanır (tenant dışı
     ilişki yasak).
  7. **Derinlik sınırı:** varsayılan en fazla **20 seviye** (tetikleyici + CTE
     sınırı); bu sınıra yaklaşan ağaçlar raporlanır.
  8. **Performans:** sorgular `(tenant_id, parent_id)` indeksiyle çalışır; kapsam
     sonucu DB cache'te (K17) **kısa TTL** ile tutulur ve organizasyon değişiminde
     (birim taşıma, yönetici değişimi) **geçersiz kılınır**.
- **Gerekçe (ölçek/bakım değerlendirmesi):** Kiracı başına birim sayısı KOBİ–orta
  ölçekte binlerle, derinlik tek haneli/20 ile sınırlıdır; bu ölçekte özyinelemeli CTE
  milisaniye düzeyinde kalır. Adjacency list **en sade ve en az bozulan** modeldir
  (taşıma = tek satır `parent_id` güncellemesi; path/closure bakımı ve tutarsızlık
  riski yoktur). Erken optimizasyon yerine ölçüm: **geri dönüş tetikleyicileri**
  aşağıdadır.
- **Fikri değiştirme tetikleyicileri:** (a) derinlik > 20 veya kiracı başına birim
  > 5.000, (b) kapsam sorgusu p95 > 50 ms (ölçüm), (c) kapsam çözümü istek başına
  birden çok kez ve toplu (N+1) yapılıyor. Bunlardan biri gerçekleşirse closure
  tablosu (C) değerlendirilir.
- **Şema/migration etkisi:** `departments`/`employee_manager` tabloları + RLS + guard
  + kısmi benzersizlik + **döngü önleme tetikleyicileri**; `path`/closure kolonu
  **eklenmez**. M10/M11 güncellenir.
- **Güvenlik etkisi:** Döngü, kapsam çözümlemesini sonsuz döngüye/Kaynak tüketimine
  sokabilir ve **yetki kapsamını bozabilir**; bu yüzden döngü engeli yalnızca
  uygulama katmanında değil **veritabanında** zorunludur (fail-closed). Ağacın yetki
  mirası üretmemesi, "yönetici oldum → üst birimin yetkilerini aldım" yanılgısını
  baştan engeller (RBAC ayrı).
- **Mevcut kodla çıkışma:** `departments`/`employees`/`employee_manager` modelleri
  **henüz yok** (hedef şema, `docs/DATABASE.md` B.4) → çakışma yok. `docs/ORGANIZATION.md`
  referans tasarımı (companies, employment_history, nested set vb.) bu kararla
  hizalanacak şekilde güncellendi.
- **Seçenekler (kayıt için):** (A) `parent_id` + özyinelemeli CTE. (B) Materialized
  path. (C) Closure tablosu. → **(A) seçildi** (kullanıcının önerisiyle uyumlu);
  (B)/(C) v1 için gereksiz karmaşıklık, tetikleyicilerle ertelendi.

### OD25 — Tenant slug ve public user ID prefix ilişkisi — **KAPALI** ✅ (2026-09-11)

**Uygulama durumu (2026-09-11 — M5):** **UYGULANDI ✅** — `hr.tenants.public_id_prefix`
(nullable ekle → geri doldur → `NOT NULL` + **tam UNIQUE** + format/rezerve `CHECK`
+ immutability tetikleyicisi) ve `hr.users.public_id` (`<prefix>-<8×Crockford>`,
**global** UNIQUE indeks, format `CHECK`, immutability tetikleyicisi). Üretim
fonksiyonları: `hr.generate_public_id_prefix(slug)`, `hr.generate_public_id(tenant_id)`,
`hr.crockford_suffix(n)` (Crockford alfabesi: `I/L/O/U` yok), `hr.is_reserved_name()`;
rezerve adlar (`www, api, app, admin, portal, mail, support, hr, platform, plt,
system`) slug/prefix olarak **reddedilir**; kiracı bağlamı yoksa public ID
**üretilmez** (fail-closed). Ölçüm: `demo-3A8EAQ9B`, `askida-1CY29715`.

- **KARAR:**
  1. Public ID ön eki **canlı `slug` değerine bağlı değildir**: `hr.tenants`'a
     **immutable `public_id_prefix`** kolonu eklenir ve kiracı oluşturulurken bir kez
     üretilip sabitlenir.
  2. Biçim: `public_id_prefix` ∈ `[a-z0-9]`, 3–20 karakter (slug'tan türetilir,
     uygunsa kısaltılır) + çakışma halinde kısa rastgele sonek. **Tenant başına
     benzersiz** ve **tekrar kullanılamaz** (tam `UNIQUE`, kısmi değil).
  3. `users.public_id` = `<public_id_prefix>-<8×Crockford Base32>` ve **immutable**;
     slug sonradan değişse bile mevcut public ID'ler **değişmez** (yeni kullanıcılar
     yeni slug'ı değil, yine **sabit prefix**i kullanır).
  4. **Soft-deleted tenant'ın prefix'i yeniden tahsis edilmez** (tam UNIQUE; OD21'in
     "kalıcı rezerve" istisnasıyla aynı çizgi).
  5. **Kullanıcı tarafından keyfi değiştirilemez:** prefix'i değiştiren bir uç nokta
     yoktur; `BEFORE UPDATE` tetikleyicisi prefix değişimini **hata ile reddeder**
     (değişmezlik DB'de zorlanır, yalnızca uygulama disiplinine bırakılmaz).
  6. **Public ID bir güvenlik sınırı değildir:** authorization/erişim kararı için
     **kullanılmaz**; yalnızca kişisel veriyi gizleyen bir **referanstır**. Kimlikle
     kayıt arama yapıldığında **kiracı izolasyonu + RLS** aynen geçerlidir (bir
     kiracı, başka kiracının prefix'iyle bile satır göremez).
- **Gerekçe:** Slug, işletme gereği değişebilen bir alandır; public ID'yi slug'a
  bağlamak, kimliğin (ve denetim izinin) slug her değiştiğinde kayması anlamına
  gelir. Sabit prefix + immutable public ID, dış referansları (destek talepleri,
  entegrasyonlar, denetim kayıtları) kırar ve "eski ID hangi kiracıya aitti?"
  sorusunu yanıtsız bırakır.
- **Şema/migration etkisi:** `hr.tenants.public_id_prefix varchar(20) NOT NULL UNIQUE`
  (nullable eklenir → geri doldurulur → NOT NULL) + değişmezlik tetikleyicisi;
  `users.public_id` üretimi **registry/slug'dan değil**, kiracının
  `public_id_prefix`'inden beslenir. M5/M27 ve F1 güncellenir; mevcut tek kiracı
  (`demo`) için geri doldurma değeri `demo`.
- **Güvenlik etkisi:** Kimlik, kimlik doğrulama ya da yetkilendirme girdisi
  **değildir**; yalnızca gösterim/referans katmanıdır → numaralandırma saldırısı
  yetki kazandırmaz. Immutability, denetim kaydı ile kullanıcı kaydı arasındaki
  bağın kopmamasını sağlar (F7/OD13 ile aynı çizgi).
- **Mevcut kodla çakışma:** `Tenant.slug` `SlugField(unique=True)` olarak var;
  `public_id_prefix` **yok**; `users.public_id` **yok** → çakışma yok, ekleme
  gerekiyor. `apps/tenancy/models.py` içinde prefix üretimi/değişmezliği **kod
  yazılmadı**; karar M5/M27'ye işlendi.
- **Seçenekler (kayıt için):** (A) Sabit, immutable prefix kolonu. (B) Ön eki canlı
  slug'dan türet. (C) Ön ek yerine kiracı kimliğinin kısa özeti. → **(A) seçildi**;
  (B) kimlik kayması nedeniyle reddedildi; (C) insan tarafından okunabilirliği
  kaybettirdiği için reddedildi.

### OD26 — Tenant API token modelinin hizalanması — **KAPALI** ✅ (2026-09-11) — **UYGULANDI** ✅ (kod + migration + test)

**1) Durum:** **KAPALI ✅ ve UYGULANDI (2026-09-11).** Bulgu **F8**'in çözüm tasarımı
kesinleşti; madde 7'deki uygulama işlerinin **tamamı kodlandı** (`accounts/0003_apitoken_od26`
migration'ı, `apps/accounts/services.py`, `apps/rbac/services/permissions.py`,
`apps/tenancy/access.py`; ölçüm ve test sonuçları için `docs/CHANGELOG.md` 0.16.0).

**2) Kesin tenant token modeli** — `hr.api_tokens` genişletilir; **yeni/ikinci bir
token sistemi kurulmaz** (OD22 ile aynı alan anlamları):

Korunur: `id`, `tenant_id`, `user_id`, `token_hash` (UNIQUE, SHA-256), `label`,
`expires_at` NOT NULL, `last_used_at`, `created_at`.

- **`token_kind varchar(16) NOT NULL DEFAULT 'interactive'`** —
  `CHECK IN ('interactive','service')`.
- **`purpose text NOT NULL`** — `CHECK (length(btrim(purpose)) >= 10)`;
  boş/anlamsız gerekçe yok (OD4 ile aynı ilke).
- **`scopes text[] NOT NULL DEFAULT '{}'`** — `CHECK (cardinality(scopes) >= 1) OR
  (revoked_at IS NOT NULL)`; wildcard yasak (`NOT ('*' = ANY(scopes))`); **her değer
  `hr.permissions.code` içinde bulunmalı** ve **`platform.*` ad alanı yasak**
  (tetikleyici ile); tekrar eden değer yok.
- **`revoked_at timestamptz NULL`** + **`revoked_reason varchar(32) NULL`** (kapalı
  küme: `logout`, `owner_disabled`, `tenant_disabled`, `admin_revoke`,
  `security_incident`, `policy_migration`); `CHECK ((revoked_at IS NULL) =
  (revoked_reason IS NULL))`.
- **Değişmezlik:** `scopes`, `purpose`, `expires_at`, `token_kind`, `token_hash`,
  `tenant_id`, `user_id` oluşturulduktan sonra **değişmez** (tetikleyici); yalnızca
  `last_used_at` ve `revoked_at`/`revoked_reason` yazılabilir → değişiklik isteği
  **iptal + yeniden üretim** ile yapılır (sessiz yetki kayması olmaz).
- **İptal tek yönlüdür:** `revoked_at` tekrar NULL yapılamaz (tetikleyici).
- **Raw token saklanmaz** (mevcut davranış korunur); özet **loglanmaz**.
- **Tenant izolasyonu:** tablo RLS dışıdır (tasarım gereği, bkz. A.5), ancak token
  **yalnızca kendi `tenant_id`'si** için bağlam kurar ve **kiracı sınırını aşamaz**;
  token ile başka kiracı bağlamı açılamaz.

**3) Scope / effective permission kuralı:**

`effective_permissions = current_user_permissions ∩ token_scopes` → **fail-closed**.

- Tek kilit nokta: `apps/rbac/permissions.py` zinciri
  (`effective_permission_codes(request)`): istek **token** ile doğrulandıysa
  **kesişim**, **oturum** ile doğrulandıysa kullanıcının izinleri.
- Üretim anında: `scopes ⊆ effective_permission_codes(owner)`; **boş liste**,
  **wildcard**, **katalogda olmayan kod** ve **`platform.*`** → `400`. Böylece token,
  üreticinin sahip olmadığı bir yetkiyi **taşıyamaz**.
- Kesişim iki kuralı tek kuralla sağlar: (a) token sonradan verilen izni **kazanmaz**;
  (b) kullanıcıdan izin geri alınırsa token o izni **kullanamaz** → rol değişiminde
  ayrıca token iptali gerekmez.
- Kapsam RBAC'i **yalnızca daraltır, genişletemez**; token kapsamı bir **yetki
  kaynağı değildir**.

**4) TTL / `token_kind` kararı:**

- `interactive` (insan; parola + MFA ile üretilir): varsayılan **30 gün**,
  **üst sınır 30 gün (720 sa)**.
- `service` (entegrasyon/makine kullanıcısı): **üst sınır 90 gün (2160 sa)**.
- **Süresiz token yok**; `expires_at` NOT NULL kalır. İstemci tavanın üzerinde TTL
  isterse **açık hata** (`400`) döner — sessiz kırpma yapılmaz.
- `service` token v1'de **yalnızca sahibi adına** üretilebilir (başkası adına üretim
  yeni bir yetki devri kavramı gerektirir → v1 dışı).
- `API_TOKEN_TTL_HOURS=720` **varsayılan** olarak korunur; tavanlar ayrıca sunucu
  tarafında dayatılır. **Ayrı token sistemi kurulmaz**: aynı tablo, aynı doğrulama,
  yalnızca tavan ve denetim etiketi farklı.

**5) Revocation kuralları:**

- **Açık iptal:** `POST /api/v1/auth/token/logout` → **silme değil iptal**
  (`revoked_at=now(), revoked_reason='logout'`) — mevcut fiziksel silme kaldırılır.
- **Yeni uç:** `POST /api/v1/auth/token/revoke-all` → sahibinin **tüm** token'ları
  (self-servis "her cihazdan çık").
- **Süre bitimi:** `expires_at` geçmişse reddedilir (iptal kaydı gerekmez).
- **Sahip pasif/silinmiş/askıda:** `status != active` veya `deleted_at IS NOT NULL`
  → reddedilir (mevcut `is_active` kontrolü; `INACTIVE`/`ARCHIVED` bu durumları
  karşılar → ayrı `suspended` durumu **eklenmez**).
- **Kiracı askıda/iptal/silinmiş:** reddedilir → **yeni kural, kodda yok**
  (bkz. **SECURITY FINDING F11**).
- **Toplu iptal:** servis fonksiyonları
  (`revoke_tokens(user=…|tenant=…|kind=…, reason='security_incident')`) — kimlik
  bilgisi sızıntısı/güvenlik olayı senaryosu.
- **İptal edilen token yeniden etkinleştirilemez**; yerine yeni token üretilir.
- Her iptal `revoked_at` + `revoked_reason` ve denetim kaydı (`token.revoke` /
  `token.revoke_all`) ile izlenir → **iptalin denetim izi kalır** (F8'in ikinci yarısı).

**6) Eski token migration stratejisi (fail-closed):**

- Kapsamsız mevcut token'lar **"tüm izinler" sayılmaz**; **hiçbiri çalışmaya devam
  etmez**.
- Sıra: (1) tüm mevcut satırlar iptal edilir
  (`revoked_at=now(), revoked_reason='policy_migration'`); (2) zorunlu alanlar
  iptal edilmiş eski satırlara **sentinel** ile yazılır (`scopes='{}'`,
  `purpose='policy_migration (kapsamsız dönem)'` — boş kapsam yalnızca iptal edilmiş
  satırda geçerlidir); (3) `NOT NULL` + CHECK + tetikleyiciler kurulur; (4) kullanıcı
  yeniden giriş yapıp **kapsamlı** token üretir.
- Kullanıcı verisi etkilenmez; yeniden üretim parola + MFA gerektirir.
- Bugünkü ölçüm: `hr.api_tokens` **0 satır** → geçiş bugün risksizdir; plan yine de
  üretim için yazılmıştır.

**7) F8'in kapanması için gereken işler** (implementasyon — **TAMAMLANDI ✅**):

1. **Şema:** **M29** (kolonlar + CHECK'ler + kapsam doğrulama tetikleyicisi +
   değişmezlik/tek yönlü iptal tetikleyicisi), **M30** (yetki turu: `api_tokens`
   UPDATE yalnızca `last_used_at`/`revoked_at`/`revoked_reason` ile sınırlı kalır),
   **F10** veri göçü (mevcut token'ları iptal et).
2. **Model:** `ApiToken` alanları + `revoke(reason)` + `is_usable`
   (`revoked_at IS NULL AND expires_at > now`); `delete()` yolu kaldırılır.
3. **Doğrulama:** `authenticate_credentials` → `revoked_at IS NULL` kontrolü +
   **kiracı durumu** kontrolü (F11) + `request.auth.scopes` erişilebilirliği.
4. **Yetki:** `effective_permission_codes(request)` kesişimi (tek kilit nokta).
5. **Üretim servisi:** gövde `purpose`, `scopes`, `kind`, `ttl_hours?`; sunucu
   tarafı doğrulama (alt küme + katalog + wildcard + `platform.*` + tavan); denetim
   kaydı (`token.issue`).
6. **Uçlar:** `token/logout` → iptal; yeni `token/revoke-all`.
7. **Testler:** boş kapsam reddi · wildcard reddi · katalogda olmayan kod reddi ·
   sahip olunmayan kapsam reddi · `platform.*` reddi · kesişim (izin geri alınınca
   token o izni kaybeder) · sonradan verilen iznin token'a yansımaması · iptal sonrası
   `401` · tek yönlü iptal · süre bitimi `401` · sahip pasif → `401` · kiracı askıda →
   `401` · düz metin saklanmaması · eski (`policy_migration`) token'ın reddi.
8. **Denetim:** `token.issue`, `token.revoke`, `token.revoke_all` eylemleri; kapsam
   kümesi + `purpose` + `kind` kaydedilir (**token'ın kendisi asla**).

**8) Uygulama öncesi kodla çakışmalar (ölçüm) — TAMAMI GİDERİLDİ ✅:**

- `apps/accounts/views.py` → `TokenLogoutView` **fiziksel silme** yapıyor
  (`ApiToken.objects.filter(pk=…).delete()`) → iptal izi kaybolur; **değişecek**.
- `apps/accounts/authentication.py` → **`revoked_at` kontrolü yok**, **kiracı durumu
  kontrolü yok**.
- `ApiToken.issue(user, label)` → `purpose`/`scopes`/`kind`/TTL parametresi yok;
  **imza değişecek**.
- `apps/rbac/permissions.py` → yalnızca kullanıcı izinlerine bakıyor; kesişim
  eklenmezse **kapsam dekoratif kalır** (asıl F8 boşluğu buradadır).
- `tests/test_api_auth.py` → `/auth/token` çağrıları yalnızca `email`+`password`
  gönderiyor → **bu testler güncellenmeden yeşil kalmaz** (beklenen davranış
  değişikliği; kapsam zorunlu hale geliyor).
- `settings.API_TOKEN_TTL_HOURS=720` korunur (varsayılan); tavanlar yeni ayarlarla.
- `docs/API.md` §2 **`Authorization: Bearer`** ve `/api/v1/auth/refresh` yazıyordu;
  kod `Token` anahtar sözcüğü kullanır ve refresh ucu **yoktur** → doküman hatası bu
  turda düzeltildi (kod değil).
- Önerilen örnek kapsam adları (`employees.read`, `leave.approve` vb.) **katalogda
  yoktur**; gerçek ad alanı `employee.read`, `leave.request.approve`,
  `org.department.read`, `shift.assign`… (**29 kod**, `apps/rbac/permission_catalog.py`)
  → kapsam değerleri **katalogdan** doğrulanır.

**Gerekçe:** Aynı güvenlik sınıfındaki iki token türünün (platform: OD22, tenant:
OD26) farklı kurallarla yaşaması bakım ve denetim yükü üretir; kapsamsız token
**en az yetki ilkesini** kimlik bilgisi seviyesinde boşaltır ve iptalin izi
kalmadığı için olay müdahalesi kanıtsız kalır.

**Güvenlik etkisi:** F8 kapanır (kapsam + iptal izi); token ile yetki yükseltme yolu
kapanır; sızıntıda etki alanı kapsamla sınırlanır ve toplu iptal kanıtlanabilir olur.
Kiracı durumu kuralı da uygulandı (**F11 kapandı** ✅): giriş, istek hattı/oturum, token
doğrulaması ve istek düzeyinde yetki kararı olmak üzere dört yol `active`/`trial`
dışındaki kiracıyı fail-closed reddeder.

**Şema/migration etkisi:** Yeni sistem yok; `hr.api_tokens` genişletilir (5 yeni
kolon, 2 tetikleyici, CHECK'ler) + veri göçü F10. Kiracı RLS politikaları ve mevcut
tablolar **değişmez**; uygulanmış migration dosyaları **değiştirilmez**.

**9) Yeni OPEN DECISION:** **Yok.** Kalan tüm noktalar bu kararla kapatıldı.

### OD27 — Kiracı içinde birden fazla tüzel şirket (`companies`) — **KAPALI** ✅ (2026-09-11)

- **KARAR (v1):** **`1 tenant = 1 tüzel şirket / işveren organizasyonu`.** Ayrı
  `companies` veya `legal_entities` tablosu **EKLEMEZ**.
  - `departments`, `positions`, `employees` doğrudan **`tenant_id`**'ye bağlı kalır.
  - Organizasyon ağacı **tenant altında** çözülür; izin politikaları, vardiyalar ve
    evrak **tenant kapsamında** kalır.
  - **RBAC scope'a `company_id` katmanı eklenmez**; `tenant_id` yanında gereksiz
    `company_id` kolonları **oluşturulmaz**.
  - Tüzel kişilik bilgisi zaten `hr.tenants` satırındadır (`legal_name`,
    `tax_number`, `email`, `phone`, `country`, `city`, `address`) — v1 için ek tablo
    gerekmez (doğrulandı).
- **Geleceğe açıklık (bağlayıcı ilkeler):** Multi-company desteği **eklenebilir**
  olmalıdır; ancak:
  1. `legal_entities` benzeri ayrı bir **katman** olarak eklenir (tenant'ın yerine
     geçmez), `departments`/`employees` uygun şekilde ona bağlanır.
  2. **`tenant` güvenlik sınırı olmaya devam eder**; legal entity **hiçbir zaman**
     tenant izolasyon sınırının yerine geçmez (RLS `tenant_id` üzerinde kalır).
  3. Geçiş şekli: `legal_entity_id` **nullable** eklenir → tek tüzel kişilik için
     geri doldurulur → `NOT NULL` (geri dönüşü kolay; uygulanmış migration değişmez).
  4. Kapsam katmanı eklenecekse **yeni bir kapsam kodu** (ör. `COMPANY`) tanımlanır ve
     izin matrisi güncellenir; mevcut kapsam kodları bozulmaz.
- **Ölçülebilir yeniden değerlendirme tetikleyicileri** (herhangi biri):
  (a) aktif kiracıların **≥ %10'u** veya **≥ 3 kiracı** aynı hesap altında ikinci bir
  işveren şirket talep ediyorsa; (b) bordro/izin politikalarının **tüzel kişilik
  bazında** ayrılması sözleşmesel zorunluluk haline gelirse (**≥ 1 imzalı sözleşme**);
  (c) enterprise satışlarda bu nedenle **ardışık ≥ 2 anlaşma** bloke olursa; (d) tek
  kiracıda **≥ 2 ayrı işveren sicil numarası** ile çalışma ihtiyacı doğarsa.
- **Gerekçe:** Hedef kitle KOBİ. İkinci bir kapsam katmanı, RLS'i ve her izin
  kontrolünü iki boyutlu hale getirir (tenant + company) ve yanlış yapılandırma
  yüzeyini büyütür. Tenant satırı zaten tüzel kimlik alanlarını taşıdığı için v1'de
  kayıp yoktur; ihtiyaç ölçümle doğrulandığında ekleme maliyeti düşüktür.
- **Şema/migration etkisi:** **Yok** — M10–M13 aynen kalır (aşağıdaki ek kontrole
  bakınız); `company_id` hiçbir tabloda yer almaz. B.4 ve organizasyon şeması
  değişmez.
- **Güvenlik etkisi:** Tek kapsam boyutu (tenant) korunur → RLS politikası, guard
  tetikleyicileri ve izin çözümlemesi sade kalır; "iki kapsam boyutundan birini
  atlama" sınıfı hatalar oluşmaz.
- **Mevcut kodla çakışma:** Yok — `departments`/`positions`/`employees` modelleri
  henüz yazılmadı ve hiçbir yerde `company_id` planlanmadı (ölçüm: `docs/` içinde
  yalnızca bu karar maddesi ve referans tasarım atıfları geçiyor).
- **Seçenekler (kayıt için):** (A) v1 tek tüzel kişilik. (B) `companies` v1'de. (C)
  opsiyonel nullable `company_id`. → **(A) seçildi**; (C) "kullanılmayan ama şemada
  duran kolon" yarattığı için reddedildi.

### OD28 — `public_id_prefix` yeniden markalama istisnası — **KAPALI** ✅ (2026-09-11)

**Uygulama durumu (2026-09-11 — M5):** **UYGULANDI ✅ (v1: istisna yok)** — prefix
**immutable** (DB tetikleyicisi `tenants_public_id_prefix_guard`); **değiştirme uç
noktası yok**; slug/rebrand değişimi prefix'i ve mevcut `public_id`'leri değiştirmez
(regresyon testi: slug değişir, prefix ve eski kullanıcı kimlikleri sabit kalır, yeni
kullanıcı **eski** prefix'le üretilir); prefix **tam UNIQUE** olduğu için soft-delete
edilen kiracının prefix'i **kalıcı rezerve**dir (yeniden tahsis edilmez). İstisna
mekanizması (platform operasyonu + denetim + eski prefix rezerve) **kodlanmadı** —
karar gereği v1'de yok.

- **KARAR (v1):** `public_id_prefix` **immutable**dır.
  - Rebranding / tenant adı / `slug` değişmesi **mevcut prefix'i değiştirmez**,
    **mevcut `public_id`'leri değiştirmez** ve **yeni kullanıcılar da aynı sabit
    prefix** ile üretilir.
  - **Normal API/UI üzerinden prefix değiştirme uç noktası YOKTUR.** Değişmezlik
    veritabanı tetikleyicisiyle zorlanır (OD25).
  - **V1'de istisna mekanizması kodlanmaz** (ne uç nokta ne servis).
- **İleride istisna gerekirse (yalnızca tasarım; v1'de yapılmaz) — bağlayıcı koşullar:**
  1. **`public_id`'ler geriye dönük değiştirilmez** (kimlik değişmezliği korunur).
  2. Bir **authorization mekanizması değildir**; erişim kararı vermez, yalnızca
     görünen referansı değiştirir.
  3. **Denetlenir**: talep, gerekçe, eski/yeni prefix, onay ve etkilenen kullanıcı
     sayısı denetim kaydına yazılır.
  4. **Yalnızca platform operasyonu** olarak yürütülür (kiracı kendi kendine
     değiştiremez; kontrollü tek seferlik prosedür, ayrı migration/CLI).
  5. Yeni prefix **çakışmaz** ve **daha önce kullanılmış/rezerve edilmiş olamaz**
     (eski prefix kalıcı olarak rezerve kalır).
  6. İşlem sonrası **iki prefix de anlamlı kalır** (eski public ID'ler eski prefix'i
     taşımaya devam eder) → destek ve denetim aramasında her iki biçim de çalışır.
- **Yeniden değerlendirme tetikleyicileri:** (a) yasal unvan/birleşme değişikliği
  sözleşmeyle prefix değişimini zorunlu kılıyorsa; (b) müşteri prefix'i marka olarak
  kullanıyorsa ve değişim talebi **≥ 3 kez** tekrarlanmışsa; (c) prefix kaynaklı
  destek karışıklığı ölçülürse (yanlış kiracıya yönlendirilen **≥ 5 talep**).
- **Gerekçe:** Kimlik değişmezliği, denetim izinin ve dış referansların (destek
  talepleri, entegrasyonlar, raporlar) bütünlüğünün ön koşuludur. İstisna mekanizmasını
  erken kodlamak, "prefix değiştirilebilir" beklentisi yaratır ve kimlik kaymasını
  normalleştirir.
- **Şema/migration etkisi:** **Yok** — OD25/M5/M27/F1 planı değişmez; istisna bir iş
  kalemi olarak **eklenmez** (v1 kapsamı dışı). Migration planındaki eksik görülen
  küçük işler (üretim fonksiyonları, format `CHECK`, rezerve adlar, platform
  kullanıcısı prefix'i) `docs/DATABASE.md` M2/M5'e **kural** olarak eklendi.
- **Güvenlik etkisi:** Prefix değişimi bir yetki yükseltme yolu (privilege escalation)
  değildir; istisna prosedürü platform-only, denetimli ve `public_id` sabit tutulduğu
  için kimlik üzerinden erişim kazanılamaz (OD25 madde 6).
- **Mevcut kodla çakışma:** Yok — prefix bugün hiç yok; uygulanacak karar M5/M27/F1'de.
- **Seçenekler (kayıt için):** (A) v1'de istisna yok. (B) Denetimli platform "prefix
  göçü". (C) Yeniden üretim (tüm public ID'ler değişir). → **(A) seçildi**; (B)
  yalnızca tetikleyici gerçekleşirse ayrı kararla; (C) **reddedildi** (denetim izi ve
  dış referanslar kırılır).

---

## 15. Özet

- Model: **RBAC + kapsam + kapalı koşul kümesi** (hibrit). Tam ABAC reddedildi;
  geri gelme tetikleyicileri ölçülebilir şekilde tanımlandı.
- 6 rol: 2 platform (`superadmin`, `platform_admin`), 4 kiracı (`tenant_admin`,
  `tenant_officer`, `dept_manager`, `employee`).
- Kiracı izolasyonu üç katmanda ve **istisnasız**; RLS politikaları hiçbir platform
  istisnası için gevşetilmez. Platform erişimi ayrı model, ayrı şema ve ayrı
  veritabanı rolüyle yürür; varsayılan **yok**, süreli ve kayıtlıdır (OD1/OD3 — kapalı).
- Kişisel veri: varsayılan maskeli; açılması izin + gerekçe + (gerekiyorsa) onay
  + süre + kayıt gerektirir. **SUPERADMIN için de varsayılan erişim yoktur**
  (OD1 — kapalı); gerekçe boş/anlamsız olamaz (OD4 — kapalı).
- Genel kimlik: `kiracı-XXXXXXXX` (rastgele, değişmez, türetilmemiş); ters
  çözümleme izinli, gerekçeli, kayıtlı ve hız sınırlı.
- Evrak: yükleme anında "kişisel veri içeriyor mu?" işareti + sınıflandırma;
  erişim politikası buna göre uygulanır; "varlığı bilmek" ≠ "içeriği görmek".
- Denetim kaydı: append-only, kişisel veri değeri taşımaz, platform erişimleri
  dahil; zincirleme ve saklama kararları OD13'te.
- **Karar durumu:** **OD1–OD5, OD19–OD28 KAPATILDI** (2026-09-11).
  Açık: **15 karar** (OD6–OD18 + **OD29**, **OD30** — `leave.balance.manage` rol ataması). **Engelleyici açık karar yoktur**; **OD5
  (departman yöneticisi kapsamı) 2026-09-11'de KAPATILDI** → organizasyon modülü
  (M7–M13) önündeki kapsam belirsizliği kalktı: `dept_manager` kapsamı **`UNIT`**
  (atandığı birim + alt ağaç), kapsam kaynağı `user_roles.scope_org_unit_id`,
  NULL **fail-closed**.
  Açık **SECURITY FINDING**'ler: F8 (tenant token kapsamı — OD26 kararı verildi, kod
  bekliyor), F10 (prefix/slug benzerliği — typosquatting, Düşük) ve **F11 (kiracı
  askıya alınınca erişim kesilmiyor, Yüksek)**. **F7 (soft-delete yetkiyi
  kaldırmıyor) ve F9 (denetim satırı `SET_NULL`) KAPATILDI (2026-09-11).**


### OD29 — `leave.balance.manage` iznini hangi rol taşıyacak? — **AÇIK** ⏳ (2026-09-11)

**Bağlam (bulgu — test arayüzü turu):** M14 ile katalogda `leave.balance.manage`
("İzin bakiyesi düzeltme") kodu tanımlandı ve `adjust_leave_balance` servisi bu izni
zorunlu tutuyor; ancak `permission_catalog.DEFAULT_ROLE_TEMPLATES` içinde **hiçbir rol**
bu izni almıyor. Sonuç: kiracıda **servis üzerinden manuel bakiye düzeltmesi
yapılamıyor** (izin hiçbir kullanıcıda bulunmuyor). Bu turda `apps/testui` bunu
değiştirmedi; demo/bootstrap betiği sahte verisinde elle test için bakiye tamamlamasını
**doğrudan** yazar (gerçek düzeltme yolu değildir).

**Etki:** Bakiye yalnızca hakediş komutuyla (`accrue_leave_entitlements`, idempotent)
üretilebiliyor; hakedişin elle düzeltilmesi gerektiğinde kullanılabilir bir yol yok.
**Güvenlik açığı değildir** — davranış fail-closed'dur (izin yoksa işlem reddedilir).

**Seçenekler:**
  * **(A)** `tenant_admin` + `hr_manager` rolleri bu izni alsın (kapsam `TENANT`;
    denetim kaydı zaten yazılıyor).
  * **(B)** Yalnızca `tenant_admin` alsın.
  * **(C)** Yeni bir "İK operasyon / bordro" rolü tanımlanıp izin ona verilsin.
  * **(D)** v1'de yalnızca hakediş komutu kalsın (izin katalogda rezerve kalır).

### OD30 — Kişisel veri **yazma** için ayrı izin kodu gerekli mi? — **AÇIK** ⏳ (2026-09-11)

**Bağlam:** Katalogda kişisel veri için yalnızca `employee.sensitive.read` var; **yazma**
(yeni kayıt/güncelleme) için ayrı kod yok. Test arayüzü kişisel veriyi hem **okumak** hem
**yazmak** için bu tek kodu kullanıyor (izin + organizasyon kapsamı).

**Neden açık:** Yetki kataloğu değişikliği rol izin sayılarını (ve
`docs/AUTHORIZATION.md` matrisi ile `verify_fresh_install.sh` beklentilerini) etkiler;
bu tur kapsamında katalog **bilinçli olarak sabit tutuldu**. Güvenlik açığı değildir:
izinsiz kullanıcı hem okuma hem yazmada `403` alır (fail-closed).

**Seçenekler:**
  * **(A)** Mevcut tek kod (`employee.sensitive.read`) okuma + yazma için yeterli olsun
    (bugünkü davranış; dokümante edilmiş kabul).
  * **(B)** `employee.sensitive.write` kodu eklenip kapsam `TENANT` ile `hr_manager` +
    `tenant_admin` rollerine verilsin (okuma izni olmadan yazma mümkün olmasın diye
    servis "yazma ⇒ okuma" kuralını uygular).
  * **(C)** Yazma yalnızca `tenant_admin` ile sınırlansın (İK yöneticisi giremesin).
  * **(D)** Kişisel veri yazımı bu arayüzden hiç yapılmasın; yalnız okunur kalsın.

**Karar sahibi:** ürün sahibi.

**Karar sahibi:** ürün sahibi. **Not:** seçilen seçenek rol izin sayılarını değiştirir;
`docs/AUTHORIZATION.md` §6.2 matrisi ve `scripts/verify_fresh_install.sh` beklentileri
**birlikte** güncellenmelidir — bu yüzden bu turda kod değiştirilmedi.
