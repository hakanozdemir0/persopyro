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

### 3.4 Platform erişimi: izolasyonun tek ve sınırlı istisnası 🔜

SUPERADMIN ve sistem ADMIN'in kiracılar arası işlem yapabilmesi gerekir; ancak bu,
"tek satır kod B şirketinin verisini görebilir" kuralını boşaltmamalıdır.

**Tasarım (öneri):** RLS politikasına **koşullu ve kayıtlı** bir bayrak eklenir:

```
politika: tenant_id = current_tenant_id()
          OR (app.platform_scope  = 'support'
              AND app.platform_tenant = tenant_id::text
              AND app.platform_grant  ≠ ''        -- geçerli, süreli onay kimliği)
```

Bayrağı yalnızca platform servis katmanı, **onaylı bir erişim kaydı (AccessGrant)**
vardıysa kurar. Kurallar:

- Erişim **süreli** (varsayılan maksimum 60 dakika) ve **tek kiracı** kapsamlıdır.
- Her kullanım `audit_logs`'a **ikinci bir kayıt** olarak yazılır (kim, hangi
  kiracı, hangi gerekçe, hangi kayıtlar).
- Süresi geçen onay hiçbir sorguda işe yaramaz; bayrak kurulsa bile politika
  reddeder.
- Varsayılan platform erişiminde **kişisel veri alanları maskelidir**; maskesiz
  erişim ayrıca bölüm 7'deki onay akışına tabidir.
- Kiracı, kendi verisine yapılan platform erişimlerini **kendi denetim
  ekranında görür** (şeffaflık).

Alternatif (bölüm 14, OD1): `BYPASSRLS` yetkili ayrı bir veritabanı rolü.
Önerilen seçenek bu değildir: RLS'i tümden devre dışı bırakır ve kayıt zorlaması
uygulama disiplinine kalır.

### 3.5 İzolasyonun test ile güvencesi ✅

Mevcut testler: kiracı A kullanıcısı ile B'nin kayıtlarının görünmediği,
bağlamsız sorgunun 0 satır döndüğü, tablo sahibinin bile FORCE RLS'i aşamadığı,
kiracılar arası FK kurulamadığı. Yeni roller eklendikçe bu negatif testler
genişletilir (bölüm 12).

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
| `GRANT_VALID` | Onaylı, süresi geçmemiş erişim kaydı var | Platform/destek erişimi, maskesiz görüntüleme |
| `POLICY_OK` | Evrakın erişim politikası izin veriyor | Kısıtlı/karanlık evrak |
| `SEPARATE_ACTOR` | İşlemi yapan, talebi/onayı oluşturandan farklı | Onay adımları |

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

Kurallar: ADMIN, kiracı iş verisine **doğrudan** yazamaz; yalnızca kendisine
atanan yönetim talebini yürütür ve her adım kayda geçer ❓OD8.

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
| `employee.personal_data.read` (maskesiz) | ◐ (GRANT_VALID + PURPOSE_GIVEN) | — | ◐ (PURPOSE_GIVEN) ❓OD4 | ◐ (GRANT_VALID) | ○ (UNIT + PURPOSE_GIVEN, süreç alanları) | ○ (SELF) |
| `employee.sensitive.read` (özel nitelikli) | ◐ (GRANT_VALID, çift kontrol) | — | ◐ (GRANT_VALID) ❓OD4 | — | — | ○ (SELF) |
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
| `dataaccess.request.approve` | ◐ (yalnızca çift kontrol; **kendi talebini onaylayamaz**) | — | ● (kendi şirketi) ❓OD4 | — | — | — |
| `dataaccess.grant.use` | ◐ (SYS + süre) | ◐ (SYS + süre) | ◐ (TENANT) | ◐ | ◐ | ○ |
| `dsr.read` / `dsr.manage` (KVKK başvurusu) | ◐ (kayıtlı) | ◐ | ● (TENANT) | ◐ | — | ○ (SELF, kendi başvurusu) |

### 6.3 Matrisin yorumu

- **SUPERADMIN** teknik olarak en geniş yetkiye sahiptir ama **kişisel veri**
  sütununda `GRANT_VALID` görür: bu, "tam yetkili" ile "varsayılan korunmuş
  kişisel veri" kuralını uzlaştıran bilinçli bir sınırdır ❓OD1.
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

### 7.3 Kontrollü erişim akışı (talep → onay → kullanım) 🔜

```
1. Talep     Kullanıcı: hangi kişi/kayıt, hangi alanlar, hangi amaç, ne kadar süre
             (izin: dataaccess.request.create) → kayıt: AccessRequest
2. Onay      Yetkili onayı: kiracı içi taleplerde ŞİRKET YÖNETİCİSİ;
             platform taleplerinde başka bir platform yöneticisi (çift kontrol)
             kural: talebi açan onaylayamaz (SEPARATE_ACTOR)
3. Kullanım  Onay süreli bir ERİŞİM İZNİ üretir (AccessGrant):
             kapsam = belirtilen kayıtlar + belirtilen alanlar + bitiş zamanı
             kullanımda: PURPOSE_GIVEN + GRANT_VALID koşulları aranır
4. Kayıt     Her kullanım audit_logs'a yazılır: kim, kimin verisi, hangi alanlar,
             hangi amaç, hangi onay kaydı, ne zaman
5. Bitiş     Süre dolunca erişim otomatik kapanır; iptal edilebilir
```

Kurallar:

- **Amaç bağlama:** gerekçe zorunludur ve kayda geçer; boş gerekçeyle erişim
  teknik olarak mümkün değildir.
- **En az ifşa:** yalnızca talep edilen alanlar açılır (tüm profil değil).
- **Çift kontrol:** platform tarafında erişim talebini başka bir yönetici onaylar;
  SUPERADMIN kendi talebini onaylayamaz.
- **Görünürlük:** kiracı yöneticisi, kendi çalışanlarının verisine yapılan
  platform erişimlerini görür (talep, gerekçe, zaman).
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
"tahmin edilemez" şartını karşılamaz. Rastgele bölüm bu yüzden zorunludur ❓OD2.

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
| Platform erişimi | kiracıya destek erişimi, kiracılar arası sorgu, erişim izni kullanımı | gerekçe, onay kimliği, süre |
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

---

## 13. Mevcut koddan geçiş (uygulama planı)

Kod yazılmadan önce tamamlanacak iş kalemleri (öncelik sırasıyla):

| # | İş | Etki | Not |
|---|---|---|---|
| 1 | `User.public_id` alanı + üretim + benzersizlik ✅/🔜 | Şema + tüm listeler | Bölüm 8 |
| 2 | Platform rolleri için kullanıcı barındırma kararı | Şema | ❓OD3 engelleyici |
| 3 | RLS politikasına koşullu platform bayrağı | Migration + politika | Bölüm 3.4; OD1 kararı sonrası |
| 4 | `AccessRequest` / `AccessGrant` tabloları | Yeni tablolar | Bölüm 7.3 |
| 5 | İzin kataloğu genişletmesi + yeni migration | Veri migration'ı | Bölüm 5.2 (uygulanmış migration değiştirilmez) |
| 6 | Rol kodu eşlemesi: `manager` → `dept_manager`, `tenant_officer` eklenmesi | Veri migration'ı | Bölüm 4.2; OD5 kararı sonrası |
| 7 | `user_roles.scope_org_unit_id` (kapsam) | Şema | Organizasyon modülüyle birlikte |
| 8 | Alan sınıflandırma + maskeleme katmanı (sunucu tarafı) | Servis/çıktı filtresi | Bölüm 7.2 |
| 9 | Evrak: `contains_personal_data`, `classification`, `retention` | Şema | Bölüm 9 |
| 10 | `audit_logs` yeni alanlar + zincirleme | Şema + servis | Bölüm 10 |
| 11 | Kapsam koşulları için ortak kontrol yardımcıları | Servis katmanı | Bölüm 5.4 |
| 12 | Negatif test seti T6–T14 | Test | Bölüm 12 |

**Değişmeyecek olan:** kiracı izolasyonunun üç katmanı, RLS + FORCE, kiracılar
arası referans yasağı, izin kodlama biçimi, "reddet kazanır", uygulanmış
migration'ların değişmezliği.

---

## 14. OPEN DECISION

Aşağıdaki noktalar belirsiz veya çelişkilidir; **kod yazılmadan önce karar
verilmelidir**. Her biri için seçenekler ve öneri verilmiştir.

### OD1 — SUPERADMIN'ın kişisel veriye erişimi

- **Çelişki:** "SUPERADMIN tam yetkilidir" ↔ "kişisel veriler varsayılan olarak
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

### OD2 — Genel kimlik formatı

- **Seçenekler:** (A) `acme-7F3K9QW2` (kiracı ön eki + rastgele 8 karakter).
  (B) İstekteki gibi sıralı `a-sirketi-00XXXX`. (C) Kiracı ön eki olmadan
  global rastgele kimlik.
- **Öneri:** (A). Sıralı kimlik numaralandırmaya açıktır ve "tahmin edilemez"
  şartını karşılamaz; kiracı ön eki destek ekibine bağlam verirken kişisel veri
  sızdırmaz.
- **Etki:** Uzunluk sabitlenir; kullanıcı arayüzü ve entegrasyonlar bu biçime
  göre tasarlanır. Kiracı adı değişirse ön ek değişmez (karar gerekli).

### OD3 — Platform kullanıcılarının barındırılması (engelleyici)

- **Çelişki:** Mevcut şemada her kullanıcı bir kiracıya bağlıdır
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

### OD4 — Kişisel veri erişim talebini kim onaylar

- **Seçenekler:** (A) Yalnızca ŞİRKET YÖNETİCİSİ (veri sorumlusu kiracıdır).
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

---

## 15. Özet

- Model: **RBAC + kapsam + kapalı koşul kümesi** (hibrit). Tam ABAC reddedildi;
  geri gelme tetikleyicileri ölçülebilir şekilde tanımlandı.
- 6 rol: 2 platform (`superadmin`, `platform_admin`), 4 kiracı (`tenant_admin`,
  `tenant_officer`, `dept_manager`, `employee`).
- Kiracı izolasyonu üç katmanda ve **istisnasız**; tek istisna süreli, onaylı,
  kayıtlı **platform erişimi**dir.
- Kişisel veri: varsayılan maskeli; açılması izin + gerekçe + (gerekiyorsa) onay
  + süre + kayıt gerektirir.
- Genel kimlik: `kiracı-XXXXXXXX` (rastgele, değişmez, türetilmemiş); ters
  çözümleme izinli, gerekçeli, kayıtlı ve hız sınırlı.
- Evrak: yükleme anında "kişisel veri içeriyor mu?" işareti + sınıflandırma;
  erişim politikası buna göre uygulanır; "varlığı bilmek" ≠ "içeriği görmek".
- Denetim kaydı: append-only, kişisel veri değeri taşımaz, platform erişimleri
  dahil; zincirleme ve saklama kararları OD13'te.
- **16 OPEN DECISION** karar bekliyor; uygulamayı engelleyenler: **OD3** (platform
  kullanıcı barındırma), **OD1** (SUPERADMIN kişisel veri erişimi), **OD4**
  (kişisel veri erişimini kim onaylar).
