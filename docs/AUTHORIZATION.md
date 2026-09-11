# Yetkilendirme — Persopyro

Kimlik doğrulama ("sen kimsin") ve yetkilendirme ("ne yapabilirsin") modeli.
Mevcut veritabanı tasarımındaki RBAC tabloları esas alınır.

## 1. Kimlik doğrulama (authentication)

- Kullanıcı e-posta + parola ile giriş yapar; parola Argon2id ile doğrulanır
  (uygulama katmanında; `apps/accounts/backends.py`).
- **E-posta kiracı başına tekildir** (aynı e-posta farklı kiracılarda olabilir).
  Bu nedenle Django'nun `ModelBackend`'i kullanılmaz; kendi backend'imiz kullanılır
  ve `auth.E003`/`auth.W004` denetimleri bilinçli olarak susturulur.
- **Giriş, kiracı bağlamı kurulmadan yapılamaz.** Kiracı belirlenemezse giriş
  reddedilir (fail-closed). Kiracı; oturum/alan adı üzerinden çözümlenir ve
  backend'e açıkça verilir — istemci gövdesinden okunmaz.
- **Önkoşul (istek hattı) — UYGULANDI:** kiracı bağlamı, Django'nun
  `AuthenticationMiddleware` adımından ÖNCE kurulur
  (`RequestIdMiddleware` → `TenantContextMiddleware` → `ApiTokenTenantMiddleware`).
  Kiracı sırası: oturum → alan adı → token. Oturum ile alan adı uyuşmazsa istek
  reddedilir.
- Oturumlar kısa ömürlüdür (varsayılan 12 saat) ve veritabanında tutulur.
- API token'ları kendi `ApiToken` modelimizle yönetilir: SHA-256 özeti saklanır,
  süresi zorunludur (varsayılan 30 gün) ve çıkış yalnızca o cihazın token'ını
  iptal eder. Üçüncü parti token paketi kullanılmaz (K15).
- MFA (TOTP) **uygulanmıştır**: hem oturum hem token girişinde zorunludur; TOTP
  sırrı veritabanında şifreli saklanır. Anahtar okunamazsa giriş reddedilir.
- Parola sıfırlama: `password_reset_tokens` üzerinden tek kullanımlık, süreli token.
- Her giriş/çıkış/başarısız deneme `audit_logs` tablosuna yazılır.
- Kullanıcı numaralandırmasına karşı: kullanıcı bulunamasa da parola doğrulaması
  maliyeti ödenir (zamanlama eşitleme).

## 2. Yetki modeli (RBAC)

Veritabanı nesneleri:

| Tablo | Rolü |
|---|---|
| `users` | Kiracıya bağlı kullanıcılar |
| `roles` | Roller; `tenant_id` NULL ise sistem geneli rol |
| `permissions` | Yetki tanımları (kod bazlı) |
| `user_roles` | Kullanıcı–rol eşleşmesi |
| `role_permissions` | Rol–yetki eşleşmesi |
| `user_permission_overrides` | Kullanıcı bazlı ekleme/çıkarma istisnası |

`hr.role_type` enum'u: `system`, `custom`, `default`.

Çözümleme kuralı:

```
etkin_yetkiler(kullanıcı) =
    (rollerinin izinleri)
    + (kullanıcıya eklenen override'lar)
    - (kullanıcıdan çıkarılan override'lar)
```

Mevcut tasarımda `hr.user_permissions(p_user_id)` fonksiyonu bu birleşimi üretir.

## 3. Değişmez yetkilendirme kuralları

1. **Varsayılan reddet**: bir işlem için açık izin yoksa işlem reddedilir.
2. **Kiracı sınırı aşılamaz**: sistem geneli rolü olan kullanıcı bile, bir
   kiracı bağlamında yalnızca o kiracının verisini görür.
3. **Yetki kontrolü servis katmanında yapılır**; arayüzde buton gizlemek yetki
   kontrolü sayılmaz.
4. **Her uç nokta gerektirdiği izni belgeler** (`API.md` içine yazılır).
5. **Rol değişikliği audit log'a yazılır.**
6. **Kendi kendini yükseltme yasak**: kullanıcı kendi rolünü/izinlerini
   değiştiremez; bu işlem ayrı bir izin gerektirir.
7. **Son yönetici korunur**: bir kiracıda son `tenant_admin` kaldırılamaz/silinemez.

## 4. Yetki kodlama biçimi

İzin kodları `<kaynak>.<işlem>` biçiminde yazılır:

```
employee.read          employee.create        employee.update        employee.delete
leave.request.read     leave.request.approve  leave.type.manage
shift.read             shift.assign           shift.schedule.manage
document.read          document.upload        document.delete        document.confidential.read
org.department.manage  org.position.manage
role.manage            user.manage            audit.read             billing.manage
```

Kurallar:

- Yeni izin eklerken mevcut kodlama biçimi kullanılır; yeni bir kalıp icat edilmez.
- İzin kodları veritabanında tutulur, koda gömülmez.
- İzinler rol aracılığıyla verilir; doğrudan kullanıcıya izin vermek istisnadır ve
  gerekçesi audit kaydında görünür.

## 5. Rollerin önerilen başlangıç seti

| Rol | Kapsam | Örnek izinler |
|---|---|---|
| `super_admin` | Platform (sistem geneli) | Kiracı yönetimi, destek amaçlı erişim (kayıtlı) |
| `tenant_admin` | Kiracı | Tüm İK modülleri, rol/kullanıcı yönetimi, faturalama |
| `hr_manager` | Kiracı | Çalışan, izin, vardiya, evrak, performans |
| `manager` | Departman | Ekibinin izin/onay ve devam kayıtları |
| `employee` | Kendisi | Kendi profil, izin talebi, kendi evrakları |

Kural: `super_admin` dahil hiçbir erişim, audit kaydı olmadan kiracı verisine
dokunamaz.

## 6. Nesne seviyesi yetki (IDOR koruması)

Her kayıt erişiminde iki soru sorulur:

1. Bu kayıt, oturumun kiracısına mı ait? (`tenant_id` eşleşmesi)
2. Bu kullanıcının bu kaydı bu işlem için görme/değiştirme izni var mı?

Örnek: `manager` rolündeki kullanıcı yalnızca kendi ekibindeki çalışanların izin
talebini onaylayabilir; başka departmanın kaydı erişim denemesinde 403 değil,
kaydın kapsam dışında olduğu bilgisini sızdırmamak için 404 döndürülmesi tercih
edilir.

## 7. Yetki kontrolünün uygulandığı yerler

| Katman | Ne yapar |
|---|---|
| API katmanı | Token geçerliliği, uç nokta için gereken iznin varlığı |
| Servis katmanı | Gerçek iş kuralı kontrolü, nesne seviyesi kapsam kontrolü |
| Veri katmanı | Tenant filtresi + RLS (son savunma hattı) |

Üç katmandan birinin atlanması yasaktır.
