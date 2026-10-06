# DEVAM NOKTASI — 2026-10-06

- Kullanıcı tercihi: PersoPyro'da her görev/işlem sonrasında commit atılmayacak;
  commit ve push yalnız kullanıcı açıkça isterse yapılacak. Bu kural
  `docs/AI_WORKING_RULES.md` içine işlendi.
- Önceki oturumun hatırlatma isteği Codex geçmişinden bulundu: vardiya raporu
  yalnız ayrı yetkisi olanlara açılacak; yeni izin/fazla mesai talepleri önce
  yalnız doğrudan yöneticiye gidecek; yönetici onay anında isterse İK ek onayı
  isteyecek.
- Bu değişiklikler mevcut çalışma ağacında commit edilmemiş olarak duruyor;
  temizlenmeyecek veya commit edilmeyecek.
- 2026-10-06'da iki gereksinim kodlandı. `rbac.0026_shift_report_permission`
  yerel veritabanına uygulandı. `git diff --check` başarılı; otomatik testler
  çalıştırılmadı. Commit/push yok.
