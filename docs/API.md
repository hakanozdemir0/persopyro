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
