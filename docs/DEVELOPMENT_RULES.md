# Geliştirme Kuralları — Persopyro

Bu kurallar zorunludur. Kural ile pratik çözüm çatışırsa kural kazanır; kural
ancak açık gerekçe ve kayıtla değiştirilir.

## Kurallar

- Güvenlik birinci önceliktir.
- Multi-tenant veri izolasyonu zorunludur.
- Bir şirket başka bir şirketin verisine hiçbir şekilde erişememelidir.
- Kişisel veriler varsayılan olarak korunmalıdır.
- Gereksiz kişisel veri gösterilmemelidir.
- Her önemli işlem audit log'a yazılmalıdır.
- Secret/API key gibi bilgiler kaynak koduna yazılmamalıdır.
- Mevcut çalışan kod gereksiz yere değiştirilmemelidir.
- Yeni özellik eklenmeden önce mevcut mimari kontrol edilmelidir.
- Her değişiklik mümkün olduğunca test edilmelidir.
- Production güvenliği göz önünde bulundurulmalıdır.

## Kuralların uygulanışı

1. **Güvenlik birinci önceliktir.** Hız, teslim tarihi veya kolaylık gerekçesiyle
   güvenlik kontrolü atlanmaz. Bir görev güvenliği zayıflatmayı gerektiriyorsa,
   iş durdurulur ve alternatif aranır.

2. **Multi-tenant veri izolasyonu zorunludur.** Her yeni tablo `tenant_id` taşır.
   Her sorgu tenant bağlamı ile çalışır. RLS politikaları veritabanı seviyesinde
   korunur ve devre dışı bırakılmaz.

3. **Bir şirket başka bir şirketin verisine hiçbir şekilde erişememelidir.**
   İzolasyon yalnızca ana liste sorgularında değil; tekil kayıt erişimi, arama,
   dışa aktarma, dosya indirme, rapor ve arka plan işlerinde de geçerlidir.
   Erişim testi olmadan tenant ile ilgili bir değişiklik "tamam" sayılmaz.

4. **Kişisel veriler varsayılan olarak korunmalıdır.** Kimlik numarası, adres,
   telefon, e-posta, ücret, sağlık/mazeret gerekçesi gibi alanlar varsayılan
   görünümde maskelenir. Tam görüntüleme ayrı yetki gerektirir ve kayıtlanır.

5. **Gereksiz kişisel veri gösterilmemelidir.** Bir ekranda/yanıtta iş için
   gerekenden fazla alan döndürülmez. Liste ve dışa aktarmalar en az veriyi
   taşır.

6. **Her önemli işlem audit log'a yazılmalıdır.** Kimlik doğrulama, yetki/rol
   değişikliği, çalışan kaydı değişikliği, izin onayı, vardiya değişikliği,
   evrak erişimi, abonelik işlemleri `audit_logs`'a yazılır. Mümkün olduğunda
   yazım tetikleyici ile otomatik yapılır.

7. **Secret/API key gibi bilgiler kaynak koduna yazılmamalıdır.** Anahtarlar
   ortam değişkeni veya secret store'dan okunur. Depoda yalnızca anahtar
   isimlerini gösteren örnek dosya bulunur. Loglara secret yazılmaz.

8. **Mevcut çalışan kod gereksiz yere değiştirilmemelidir.** Değişiklik en dar
   kapsamda yapılır. İlgisiz biçimlendirme, yeniden adlandırma, taşıma veya
   "iyileştirme" aynı değişiklikte yapılmaz. Mevcut dosya ve veritabanı nesneleri
   gerekçesiz silinmez.

9. **Yeni özellik eklenmeden önce mevcut mimari kontrol edilmelidir.**
   `ARCHITECTURE.md`, `SECURITY.md`, `AUTHORIZATION.md`, `DATABASE.md` ve
   `FEATURES.md` okunur. Aynı ihtiyacı karşılayan mevcut yapı varsa yenisi
   yazılmaz. Tasarım kararı verilirse önce doküman güncellenir, sonra kod yazılır.

10. **Her değişiklik mümkün olduğunca test edilmelidir.** Yeni davranış için test
    yazılır. Özellikle şu testler zorunludur: tenant izolasyonu, yetki kontrolü,
    audit kaydı üretimi, kişisel veri maskeleme. Test çalıştırmadan bir iş
    bitmiş sayılmaz.

11. **Production güvenliği göz önünde bulundurulmalıdır.** Geliştirme kolaylığı
    için üretimde tehlikeli varsayılan değerler (ör. açık RLS, zayıf parola
    politikası, ayrıntılı hata mesajı, debug modu) bırakılmaz. Ayrıcalıklı
    hesaplar yalnızca gerektiğinde ve kayıtla kullanılır.

## Değişiklik kabul listesi

- [ ] Mevcut mimari ve ilgili doküman okundu.
- [ ] Tenant izolasyonu korunuyor; yeni tablo/sorgu tenant bağlamı içeriyor.
- [ ] Yetki kontrolü tanımlı ve varsayılan reddet korunuyor.
- [ ] Kişisel veri minimum düzeyde.
- [ ] Önemli işlemler audit log'a yazılıyor.
- [ ] Secret veya gerçek anahtar koda/loga yazılmadı.
- [ ] Kapsam dışı dosya değiştirilmedi.
- [ ] Test yazıldı ve çalıştırıldı.
- [ ] Üretim etkisi değerlendirildi.
- [ ] `CHANGELOG.md` güncellendi.

## Çalışma sırası

1. İsteği oku ve kapsamı netleştir.
2. İlgili dokümanları ve mevcut kodu incele.
3. Gerekirse tasarımı dokümana yaz.
4. En dar kapsamda değişikliği uygula.
5. Test yaz ve çalıştır.
6. Kontrol listesini işaretle, `CHANGELOG.md`'yi güncelle.
7. Ne değişti, ne doğrulandı, ne kaldı — kısa rapor ver.
