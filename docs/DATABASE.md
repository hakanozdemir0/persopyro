# Veritabanı — Persopyro

Veri katmanının mevcut tasarımı `~/hrsaas_db/` altındadır (PostgreSQL 14+, önerilen
15+). Bu dosya o tasarımın envanterini, kurallarını ve henüz doğrulanmamış
noktalarını kaydeder. **Bu aşamada SQL dosyaları değiştirilmemiştir.**

## 1. Mevcut dosyalar

| Dosya | İçerik |
|---|---|
| `01_extensions_schema.sql` | `pgcrypto`, `uuid-ossp`; `hr` şeması; `tenants`, `tenant_settings` |
| `02_users_rbac.sql` | `users`, `roles`, `permissions`, `user_roles`, `role_permissions`, `user_permission_overrides`, oturum/parola tabloları |
| `03_org_structure.sql` | `companies`, `departments`, `positions`, `employees`, `employment_history`, `emergency_contacts`, `work_schedules`, `projects` |
| `04_attendance_timesheet.sql` | `attendance_records`, `attendance_events`, `timesheets`, `timesheet_entries` |
| `05_leave_management.sql` | `leave_types`, `leave_balances`, `leave_requests`, `leave_approvals` |
| `06_payroll.sql` | `salary_components`, `employee_compensations`, `payroll_runs`, `payroll_lines`, `payroll_component_lines` |
| `07_performance.sql` | `review_cycles`, `review_form_templates`, `performance_reviews`, `review_goals`, `review_scores` |
| `08_hiring_candidates.sql` | `job_postings`, `candidates`, `job_applications`, `application_stages`, `interviews`, `interview_panelists`, `offers` |
| `09_documents.sql` | `documents`, `document_categories`, `document_versions`, `document_access` |
| `10_subscription_billing.sql` | `plans`, `subscriptions`, `subscription_seats`, `invoices`, `payments` |
| `11_functions_triggers.sql` | `audit_logs` + fonksiyonlar ve tetikleyiciler |
| `12_seed_indexes.sql` | Örnek veri ve ileri seviye indeksler |

Tasarım yaklaşık 90 tablo ve 16 enum tipi içerir. Kaynak not:
`~/hrsaas_db/README_DATABASE.md`.

## 2. Ortak tasarım kuralları

- **UUID birincil anahtar**: `gen_random_uuid()`.
- **Tenant kolonu**: tüm iş tablolarında `tenant_id UUID NOT NULL REFERENCES hr.tenants(id)`.
- **Zaman damgaları**: her tabloda `created_at` / `updated_at` (`TIMESTAMPTZ`);
  `updated_at` `hr.set_updated_at()` tetikleyicisi ile güncellenir.
- **Soft delete**: `deleted_at` alanı; `hr.soft_delete_row()` ile.
- **Durum alanları**: `record_status` enum'u (`active`, `inactive`, `archived`).
- **Şema**: `hr`; `search_path TO hr, public`.
- **Parola**: uygulama katmanında **Argon2id** ile hash'lenir (mevcut bcrypt
  hash'leri için uyumluluk hasher'ı açık tutulur). Veritabanı seviyesindeki
  `crypt()` tabanlı kimlik doğrulama üretimde kullanılmaz — gerekçe:
  `ARCHITECTURE.md` bölüm 5.4 madde 3.

## 3. Veritabanı seviyesindeki güvenlik ve otomasyon

Fonksiyonlar (`11_functions_triggers.sql`):

| Fonksiyon | Görev |
|---|---|
| `hr.set_updated_at()` | `updated_at` güncelleme tetikleyicisi |
| `hr.soft_delete_row()` | Yumuşak silme yardımcısı |
| `hr.hash_password()` / `hr.verify_password()` | bcrypt parola işlemleri |
| `hr.authenticate_user(email, password, tenant_id)` | Kimlik doğrulama |
| `hr.user_permissions(user_id)` | Etkin izin çözümlemesi |
| `hr.audit_trigger_fn()` | Değişiklikleri `audit_logs`'a yazma |
| `hr.current_tenant_id()` | RLS için tenant bağlamı okuma |
| `hr.enable_tenant_rls(table)` | Tabloya `tenant_isolation` RLS politikası ekleme |
| `hr.validate_new_user()` | Yeni kullanıcı doğrulaması |
| `hr.sync_leave_balance_on_approval()` | İzin onayında bakiye senkronizasyonu |

RLS modeli:

```sql
CREATE POLICY tenant_isolation ON hr.<tablo>
    USING (tenant_id = hr.current_tenant_id())
    WITH CHECK (tenant_id = hr.current_tenant_id());
```

Tenant bağlamı: `SELECT set_config('app.tenant_id', '<uuid>', FALSE);`

## 4. Kurallar (bağlayıcı)

1. Yeni tablo eklenirse `tenant_id` zorunludur (platform geneli tablolar hariç,
   bunlar açıkça gerekçelendirilir).
2. Yabancı anahtarlar kiracılar arası referans üretemez.
3. Kişisel veri kolonları açıkça işaretlenir ve maskelenebilir olmalıdır.
4. Hassas/şifreli alanlar için `is_encrypted` benzeri işaretleme ve `checksum`
   yaklaşımı korunur.
5. Şema değişikliği geri alınabilir (ileri/geri migration) olarak yazılır.
6. Var olan kolon adları ve tipleri gerekçesiz değiştirilmez.
7. İndeks ihtiyacı, gerçek sorgu desenlerine göre eklenir; her kolona indeks
   eklenmez.

## 5. Kişisel veri içeren kolonlar (maskeleme hedefi)

`employees`, `users`, `emergency_contacts`, `candidates`, `documents` tabloları
başta olmak üzere: kimlik numarası, doğum tarihi, adres, telefon, e-posta, IBAN,
sağlık/mazeret gerekçesi, ücret bilgisi.

- Varsayılan görünümde maskelenir (`85******123` benzeri).
- Açık görüntüleme ayrı izin gerektirir ve audit log'a yazılır.
- Dışa aktarma çıktılarında mümkünse toplulaştırılmış veri tercih edilir.

## 6. Doğrulanmamış / açık noktalar

Bunlar henüz test edilmediği için "doğrulanmış" sayılmaz:

- SQL dosyalarının gerçek bir PostgreSQL örneğinde hatasız çalıştığı
  doğrulanmamıştır (bu ortamda `psql` kurulu değil).
- RLS etkinleştirmeleri örnek olarak yorumlanmıştır; hangi tablolarda uygulanacağı
  kararı verilmemiştir. Karar `ARCHITECTURE.md` bölüm 5.3'teki rol ayrımı ile
  birlikte, faz 1 spike'ında verilecektir.
- Şema yönetimi kararı verilmiştir: **şemanın sahibi Django migration'larıdır.**
  Mevcut SQL dosyaları salt-okunur referans olarak korunur; tetikleyici, fonksiyon
  ve RLS politikaları migration içinde ham SQL (`RunSQL`) ile yazılır.
  (Önceki sürümde "migration aracı seçilmedi" notu vardı; bu kararla kapatıldı.)
- PostgreSQL `ENUM` tipleri ile Django `TextChoices` yaklaşımı arasındaki fark
  henüz test edilmemiştir; bkz. `ARCHITECTURE.md` bölüm 5.4 madde 2.
- Uygulama rolü ve `GRANT` planı yazılmamıştır (RLS öncesi zorunlu adım).
- Örnek veri (`12_seed_indexes.sql`) üretimde kullanılmayacaktır; ayrı bir seed
  stratejisi gereklidir.

## 7. Yedeklilik ve saklama

- Günlük otomatik yedek + nokta-zamanalı geri yükleme hedeflenir.
- Silme yerine arşivleme önceliklidir; saklama süresi kiracı sözleşmesi ve
  mevzuata göre tanımlanır.
- Yedekler de kiracı verisini karıştırmayacak şekilde geri yüklenebilmelidir.
