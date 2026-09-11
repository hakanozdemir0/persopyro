# Güvenlik — Persopyro

Güvenlik bu projede birinci önceliktir (`DEVELOPMENT_RULES.md`). Bu doküman
güvenlik ilkelerini, tehdit modelini ve uygulanacak kontrolleri tanımlar.

## 1. Değişmez ilkeler

1. Güvenlik, teslim hızından önce gelir.
2. Kiracı izolasyonu hem uygulama hem veritabanı katmanında uygulanır.
3. Kişisel veriler varsayılan olarak korunur ve minimum düzeyde gösterilir.
4. Her önemli işlem denetim kaydına (audit log) yazılır.
5. Secret ve API anahtarları kaynak kodda yer almaz.
6. Güvenlik kararı, "şimdilik hızlı çözelim" gerekçesiyle gevşetilmez.

## 2. Tehdit modeli

| # | Tehdit | Etki | Temel kontrol |
|---|---|---|---|
| T1 | Bir kiracının başka bir kiracının verisine erişmesi (tenant escape) | Felaket düzeyinde | Zorunlu tenant bağlamı + RLS |
| T2 | Yetkisiz kullanıcının yetkisi dışındaki işlemi yapması | Yüksek | RBAC + servis katmanı yetki kontrolü |
| T3 | Kimlik bilgisi sızması (parola, token) | Yüksek | bcrypt hash, kısa ömürlü token, MFA desteği |
| T4 | Evrak/belge dosyalarına yetkisiz erişim | Yüksek | İmzalı/kısa ömürlü URL, erişim kaydı, confidential işaretleme |
| T5 | Audit kaydının manipüle edilmesi veya eksik kalması | Orta-Yüksek | Otomatik tetikleyici ile yazım, yazma yetkisinin kısıtlanması |
| T6 | Dosya yükleme üzerinden zararlı içerik | Orta | MIME/boyut/checksum doğrulaması |
| T7 | Yatay/dikey yetki yükseltme (IDOR) | Yüksek | Her kayıt erişiminde tenant + yetki kontrolü |
| T8 | Kişisel verinin gereğinden fazla gösterilmesi / dışa sızması | Orta | Varsayılan maskeleme, en az veri ilkesi |
| T9 | Secret'ın kod deposuna veya loglara düşmesi | Yüksek | Ortam değişkeni/secret store, log temizleme |
| T10 | Brute force / parola deneme saldırısı | Orta | Deneme sınırı + hesap kilitleme (tasarımda mevcut alanlar) |

## 3. Kimlik ve parola güvenliği

- **Kimlik doğrulama uygulama katmanında yapılır.** Parola, veritabanı
  fonksiyonuna gönderilmez (`hr.authenticate_user` üretimde kullanılmaz); bu
  sayede parola veritabanı loglarına/sorgu kayıtlarına düşemez ve politika,
  kilitleme ve MFA mantığı tek yerde toplanır (bkz. `ARCHITECTURE.md` bölüm 7).
- Parolalar asla düz metin saklanmaz. **Argon2id** (Django birinci sınıf
  destekli) kullanılır; mevcut bcrypt hash'leri için uyumluluk hasher'ı açık
  tutulur, yeni parolalar Argon2id ile yazılır.
- `users` tablosu şu güvenlik alanlarını içerir: `failed_login_count`,
  `lockout_until`, `password_changed_at`, `email_verified_at`,
  `multi_factor_enabled`. Bu alanlar kullanılmalı, boş bırakılmamalıdır.
- Çok faktörlü doğrulama (TOTP) `tenant_admin` ve `super_admin` için zorunludur.
- Parola sıfırlama `password_reset_tokens` tablosu üzerinden, tek kullanımlık,
  süreli ve hash'li token ile yapılır.
- Giriş denemeleri `login_audit` tablosuna yazılır.

## 4. Çok kiracılı izolasyon (kritik kontrol)

- Her iş tablosunda `tenant_id` bulunur; tüm yabancı anahtarlar aynı kiracı
  içinde kalacak şekilde doğrulanır (kiracılar arası referans yasaktır).
- Sorgular uygulama katmanında tenant bağlamı ile filtrelenir; ayrıca PostgreSQL
  RLS politikaları ile veritabanı seviyesinde dayatılır
  (`hr.enable_tenant_rls`, `hr.current_tenant_id`).
- RLS'i devre dışı bırakmak, ayrıcalıklı bir hesapla çalışmak veya politikayı
  atlatmak yasaktır. Geçici bir ihtiyaç doğarsa bu, yazılı gerekçe ve kayıtla
  yapılır.
- Kiracı kimliği istemciden gelen veriye dayanarak belirlenmez.

## 5. Yetkilendirme

Detaylar `AUTHORIZATION.md` içindedir. Özet:

- Rol tabanlı erişim (RBAC) + kullanıcı bazlı istisna (override) modeli.
- Her uç nokta, hangi izni gerektirdiğini açıkça belirtir.
- Varsayılan davranış reddetmektir (deny by default).

## 6. Denetim kaydı (audit log)

- `hr.audit_logs` tablosu: `tenant_id`, `user_id`, `action`, `table_name`,
  `record_id`, `changes`.
- Yazım `hr.audit_trigger_fn()` tetikleyicisi ile otomatik yapılır; uygulamanın
  "log yazmayı unutması" riskine bırakılmaz.
- Denetim kaydına yazılması zorunlu işlemler: kimlik doğrulama, rol/yetki
  değişiklikleri, çalışan kaydı oluşturma/güncelleme/silme, izin onay-red, vardiya
  değişiklikleri, evrak yükleme/indirme/silme, abonelik ve faturalama işlemleri.
- Audit kaydı uygulama tarafından silinemez veya değiştirilemez.

## 7. Evrak ve dosya güvenliği

- Dosyalar nesne depolamada (`storage_key`) tutulur; veritabanı yalnızca üst veri
  saklar.
- `checksum_sha256` ile bütünlük doğrulanır.
- `is_confidential` işaretli belgeler için erişim ayrıca yetki gerektirir ve
  erişim `document_access` üzerinden kayıtlanır.
- Dosyalara doğrudan genel URL verilmez; kısa ömürlü imzalı bağlantı üretilir.
- Yüklemede boyut, MIME türü ve uzantı doğrulanır.

## 8. Kişisel veri (KVKK/GDPR yaklaşımı)

- Veri toplama amacı ve saklama süresi tanımlıdır; amaç dışı işleme yapılmaz.
- Varsayılan görünümde TC kimlik no, adres, telefon, doğum tarihi, sağlık/izin
  gerekçesi gibi alanlar maskelenir; görüntüleme yetki + amaç gerektirir.
- Liste ve dışa aktarma uçları, tam kişisel veri döndürmez.
- Silme talepleri soft-delete ile arşivleme + kişisel alanların anonimleştirilmesi
  şeklinde ele alınır; hangi alanların anonimleştirileceği `DATABASE.md` içinde
  kayıtlıdır.
- Loglara kişisel veri yazılmaz.

## 9. Secret yönetimi

- Secret'lar ortam değişkeni veya secret store'dan okunur; koda, `.env` örneğine
  veya dokümana gerçek değer yazılmaz.
- Depoda yalnızca örnek anahtar isimleri (`.env.example`) bulunur, gerçek değer
  bulunmaz.
- Bir secret sızdığından şüphelenilirse önce döndürülür (rotate), sonra olay
  kaydı açılır.

## 10. Altyapı ve üretim güvenliği

- Tüm trafik TLS üzerinden taşınır.
- Uygulama veritabanına, tablo sahibi (owner) veya superuser hesabıyla değil,
  sınırlı yetkili `persopyro_app` rolüyle bağlanır. Migration ayrı bir sahip
  rolüyle (`persopyro_owner`) çalışır — bu, RLS'in atlanmaması için zorunludur
  (`ARCHITECTURE.md` bölüm 5.3).
- Girdi doğrulama her uç noktada zorunludur; sorgular parametreli çalıştırılır.
- Bağımlılıklar güncel ve denetlenmiş tutulur; güvenlik açığı bildirimi işlenir.
- Hata mesajları iç durum, sorgu veya yığın izi sızdırmaz.

## 11. Güvenlik kontrol listesi (her değişiklikte)

- [ ] Bu değişiklik tenant izolasyonunu etkiliyor mu?
- [ ] Yeni uç nokta/tablo eklendiyse tenant filtresi ve RLS var mı?
- [ ] Yetki kontrolü tanımlandı mı, varsayılan reddetme korunuyor mu?
- [ ] Kişisel veri dönüşü minimum düzeyde mi?
- [ ] Kritik işlem audit log'a yazılıyor mu?
- [ ] Secret veya gerçek anahtar koda sızdı mı?
- [ ] Test edildi mi?
