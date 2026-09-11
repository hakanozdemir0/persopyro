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

**Mevcut kodla fark:** katalogda `tenant_admin`, `hr_manager`, `manager`,
`employee` var ✅. Yeni tasarım: `manager` → `dept_manager` (kapsamı daraltılır),
`hr_manager` → **şablon rol** olarak korunur (zorunlu değil), `tenant_officer`
eklenir, `superadmin`/`platform_admin` yeni. Geçiş: bölüm 13.

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

### 4.4 Kapsam (scope) kodları

| Kod | Anlamı |
|---|---|
| `SYS` | Tüm sistem, kiracılar arası (yalnızca platform rolleri) |
| `TENANT` | Yalnızca kullanıcının kiracısı |
| `UNIT` | Kullanıcının bağlı olduğu organizasyon birimi **ve alt birimleri** ❓OD5 |
| `UNIT_DIRECT` | Yalnızca doğrudan bağlı birim (alt birimler hariç) |
| `ASSIGNED` | Yalnızca kullanıcıya açıkça atanmış kayıtlar (onay adımı, vaka) |
| `SELF` | Yalnızca kullanıcının kendi kaydı |

Etkin kapsam = **en dar** olan: rolün izin verdiği kapsam ∩ kaydın kapsamı ∩
isteğin kapsamı. Örneğin `UNIT` kapsamlı bir onay izni, başka bir birimin
kaydında işe yaramaz.

Kapsam kaynağı: `user_roles.scope_org_unit_id` (kiracı rolleri için) 🔜. Rolün
kapsamı kişi bazında daraltılabilir; **genişletilemez**.

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
| Platform | `platform.tenant.read`, `platform.tenant.manage`, `platform.settings.manage`, `platform.audit.read`, `platform.support.manage`, `platform.role.assign` |
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

#### F. Vardiya ve devam

| İzin kodu | SA | PA | ŞİRKET YÖN. | ŞİRKET YETK. | DEPT. YÖN. | ÇALIŞAN |
|---|---|---|---|---|---|---|
| `shift.read` | — | — | ● (TENANT) | ● (TENANT) | ● (UNIT) | ○ (SELF) |
| `shift.assign` | — | — | ● (TENANT) | ◐ | ● (UNIT) | — |
| `shift.schedule.manage` | — | — | ● (TENANT) | ◐ | ◐ (UNIT) | — |
| `shift.publish` | — | — | ● (TENANT) | — | ◐ (UNIT) ❓OD5 | — |
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
| T8 | Talep sahibi kendi talebini onaylayamaz (SEPARATE_ACTOR) | 🔜 |
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
| 7 | Rol kodu eşlemesi: `manager` → `dept_manager`, `tenant_officer` eklenmesi | Veri migration'ı | Bölüm 4.2; OD5 kararı sonrası |
| 8 | `user_roles.scope_org_unit_id` (kapsam) | Şema | Organizasyon modülüyle birlikte |
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

**Durum (2026-09-11):** **OD1, OD2, OD3, OD4, OD19, OD20, OD21 ve OD22 KAPATILDI.**
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

### OD5 — Departman yöneticisinin kapsamı

- **Seçenekler:** (A) Kendi birimi ve **tüm alt birimler** (alt ağaç).
  (B) Yalnızca doğrudan bağlı birim (alt birimler hariç). (C) Birim bazında
  yapılandırılabilir.
- **Öneri:** (A) varsayılan, (C) ileride. Gerekçe: pratikte alt birimler de
  yöneticiye bağlıdır; (B) çok kısıtlayıcıdır.
- **Etki:** Kapsam çözümlemesi, vardiya/izin onayı testleri, matris satırları.

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

### OD19 — Django admin / `auth_*` paralel yetki yolu — **KAPALI** ✅ (2026-09-11)

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
- **İmplementasyon kalemleri (bu turda kod yazılmadı):** `/admin/` route'u
  kaldırılır; `django.contrib.admin` uygulama listesinden çıkarılır;
  `create_superuser` artık `is_staff=True` atamaz; `auth_group`,
  `auth_group_permissions`, `auth_permission`, `django_admin_log` tablolarında
  uygulama rolünün **tüm yetkileri geri alınır**; `is_superuser` alanı
  **eklenmez**; üretimde `is_staff = true` olan aktif kullanıcı bulunmaması
  **denetim sorgusu + test** ile zorlanır; kaldırma sonrası **sıfırdan kurulum
  testi** tekrarlanır.
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

### OD23 — Denetim kaydında platform aktörünün temsili 🆕

- **Neden açık:** `hr.audit_logs.user_id → users.id` (kiracı kullanıcısı). Platform
  kullanıcısı bu tabloda yoktur; ama platform erişiminin kiracı akışına da
  yazılması gerekiyor (şeffaflık, K22).
- **Seçenekler:** (A) `user_id` NULL + `actor_kind` (`tenant_user`/`platform_user`/
  `system`) + `actor_public_id`. (B) Ayrı `hr.platform_actor` tablosu ve FK.
  (C) Platform erişimini yalnızca platform akışına yaz.
- **Öneri:** (A). (C) şeffaflık şartını karşılamaz; (B) gereksiz karmaşıklık.
- **Etki:** `docs/DATABASE.md` B.3/M20/F8, `docs/SECURITY.md` bölüm 10.

### OD24 — Organizasyon ağacı mekanizması 🆕

- **Neden açık:** `UNIT` kapsamı (departman ve alt birimleri) her istekte
  çözülecek; mekanizma seçimi hem güvenliği (kapsam doğruluğu) hem performansı
  belirler. OD5 (kapsamın alt ağaç mı, doğrudan birim mi olduğu) hâlâ açık.
- **Seçenekler:** (A) `parent_id` + özyinelemeli CTE. (B) Materialized path
  (`path` kolonu, `text_pattern_ops` indeks). (C) Closure tablosu.
- **Öneri:** (A) ile başla; ölçülen yavaşlama veya ağaç derinliği > 6 olduğunda
  (C)'ye geç. Gerekçe: erken optimizasyon yerine ölçüm.
- **Etki:** `docs/DATABASE.md` M11/E/H9; kapsam çözümleyicisi.

### OD25 — Kiracı `slug` değişince genel kimlik ön eki 🆕

- **Neden açık:** OD2 biçimi kiracı slug'ını ön ek olarak kullanır; slug tekil ve
  güncellenebilir bir alandır.
- **Seçenekler:** (A) Ön ek sabit kalır (kimlik değişmez; eski ön ek görülebilir).
  (B) Slug değişince tüm kimlikler yeniden üretilir. (C) Ön ek yerine kiracı
  kimliğinin kısa özeti kullanılır.
- **Öneri:** (A). Gerekçe: kimlik **değişmez** olmalıdır (denetim izi ve dış
  referanslar kopmasın); (B) denetim bütünlüğünü bozar.
- **Etki:** `docs/DATABASE.md` B.3/F1; kiracı yeniden adlandırma akışı.

### OD26 — Tenant API token modelinin hizalanması (kapsam ve iptal izi) 🆕

- **Neden açık (ölçüm — bulgu F8):** Mevcut `hr.api_tokens` **kapsamsız**dır (token,
  kullanıcının tüm yetkilerini taşır), **`revoked_at` yoktur** (iptal = satır silme →
  iptal izi kaybolur) ve TTL sabittir (`API_TOKEN_TTL_HOURS=720` = 30 gün). Platform
  token'ları için OD22 bu üç noktayı **kapatmıştır**; tenant tarafı hâlâ açık.
- **Seçenekler:** (A) Tenant token'a da `scope` (izin kodu listesi), `revoked_at` ve
  `purpose` ekle; iptal artık silme değil işaretleme olsun. (B) Yalnızca `revoked_at`
  ekle (kapsam olmadan; en az yetki kazanımı yok). (C) Değişiklik yok (kabul edilen
  risk olarak kaydet).
- **Öneri:** (A). Gerekçe: aynı güvenlik sınıfındaki iki token türünün farklı
  kurallarla yaşaması bakım ve denetim yükü üretir; kapsamsız token, en az yetki
  ilkesini (T16) kimlik bilgisi seviyesinde boşaltır.
- **Etki:** `docs/DATABASE.md` (B.3, M3/M27), `docs/SECURITY.md` bölüm 3/6; mevcut
  token üretim/doğrulama servisi ve testleri. **Bu turda kod değiştirilmedi.**
- **Karar beklenmeden yapılacak:** yalnızca `revoked_at` (B alt kümesi) eklenmesi
  geriye dönük uyumludur ve denetim izini geri kazandırır; **kapsam (A)** karar
  gerektirir.

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
- **Karar durumu:** **OD1–OD4, OD19, OD20, OD21, OD22 KAPATILDI** (2026-09-11).
  Açık: **18 karar** (OD5–OD18, OD23–OD26). **Engelleyici açık karar yoktur**;
  OD17 (çift onayın uygulanabilirliği), **OD26** (tenant token kapsamı) ve OD23
  (denetimde platform aktörü) uygulama sırasında öncelikli netleştirilmelidir.
  Açık **SECURITY FINDING**: F7 (soft-delete yetkiyi kaldırmıyor) — OD21 kararı
  uygulanarak kapatılır.
