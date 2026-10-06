# Persopyro AI Low-Token Working Rules

## 1. Tek görev

Bir promptta yalnız kullanıcının istediği işi yap.

İstenen sorunu çözdükten sonra:
- başka modüle geçme
- "fırsat bulmuşken" refactor yapma
- yeni özellik ekleme
- kapsamı genişletme

## 2. Önce git diff

Her görev başlangıcında yalnız:

```
git status --short
git diff --stat
git log -1 --oneline
```

ile mevcut durumu ölç.

Çalışma ağacı kirliyse mevcut işi koru.

## 3. Repo'yu baştan tarama

Her promptta bütün repository'yi yeniden okuma.

Yalnız görevle doğrudan ilişkili:
- dosyaları
- testleri
- modelleri
- servisleri

incele.

Önce grep/ripgrep ile hedef dosyayı bul.

Bütün docs/* veya apps/* taraması ancak görev gerçekten gerektiriyorsa yapılabilir.

## 4. Dokümanları tekrar tekrar okuma

HR_ROADMAP.md, DATABASE.md, AUTHORIZATION.md vb. büyük dosyaların tamamını her görevde okuma.

Önce ilgili başlığı/OD kodunu ara.

Yalnız ilgili bölümü oku.

## 5. Alt ajanları varsayılan olarak kullanma

Sub-agent / inventory agent / parallel agent başlatma.

Yalnız kullanıcı açıkça isterse veya tek modelle çözülemeyen açık bir ihtiyaç varsa kullan.

Bir sorun için birden fazla ajanla aynı repository taramasını tekrar etme.

## 6. Önce targeted test

Kod değişikliğinden sonra önce sadece ilgili test dosyasını çalıştır.

Örnek:

```
pytest tests/test_x.py -q
```

Targeted test kırmızıysa full suite ÇALIŞTIRMA.

Önce targeted testi düzelt.

## 7. Full suite sadece kapanışta

`bash scripts/run_tests.sh`

yalnız:
- targeted testler yeşil
- implementasyon tamamlanmış

olduğunda bir kez çalıştır.

Aynı full suite'i sebep olmadan tekrar çalıştırma.

Kullanıcı ayrıca doğrulama isterse tekrar çalıştırılabilir.

## 8. Fresh install sadece paket sonunda

`verify_fresh_install.sh`

her küçük değişiklikten sonra çalıştırma.

Sadece migration/DB/RLS/permission değişikliği olan paketin kapanışında çalıştır.

## 9. Browser smoke sadece kapanışta

Her kod değişikliğinden sonra browser/runserver testi yapma.

Targeted testler ve full suite yeşil olduktan sonra gerekiyorsa tek smoke akışı yap.

## 10. Maksimum iki düzeltme iterasyonu

Aynı hata için:
1. ölç
2. minimal düzelt
3. testi çalıştır

Aynı problem iki kez çözülemezse dur ve kullanıcıya:

- hata
- ölçülen neden
- denenmiş çözümler

raporla.

Kendi kendine sınırsız iterasyona girme.

## 11. Minimal değişiklik

Bir bug için mümkün olan en küçük doğru değişikliği yap.

İlgisiz:
- refactor
- rename
- formatting
- mimari değişiklik

yapma.

## 12. Mevcut sistemi source-of-truth kabul et

Dokümanda kapanmış OD varsa yeniden tartışma.

Yeni OPEN DECISION ancak mevcut kararlarla gerçekten çözülemeyen blocker varsa aç.

## 13. Dosya okumayı sınırla

Bir dosyanın yalnız gerekli kısmını oku.

1000 satırlık dosyada sorun 50 satırlık fonksiyondaysa tüm dosyayı tekrar tekrar modele taşıma.

## 14. Büyük çıktıları modele geri besleme

Full pytest çıktısının tamamını tekrar analiz etme.

Başarısız testlerde yalnız:
- failure name
- traceback'in ilgili bölümü
- assertion

üzerinden çalış.

Başarılı testlerin tek tek listesini modele taşıma.

## 15. Logları filtrele

Büyük loglarda:

```
grep
tail
head
pytest -x
pytest --tb=short
```

gibi araçları kullan.

Binlerce satır logu context'e yükleme.

## 16. Test failure durumunda

`pytest -x`

ile önce ilk hatayı çöz.

20 hatayı aynı anda analiz etmeye çalışma.

İlk hata çözülünce sonraki hataya geç.

## 17. Docs en son

Kod tamamlanmadan dokümantasyon güncelleme.

Sıra:

implementasyon
→ targeted tests
→ full suite
→ quality/fresh install
→ docs
→ commit

## 18. Commit yalnız açık istekle

İşlem veya mikro görev tamamlandı diye otomatik commit oluşturma.
Commit ve push yalnızca kullanıcı açıkça istediğinde yapılır.
Birden çok değişikliği çalışma ağacında birlikte tut; mevcut dirty değişiklikleri
koru ve kullanıcı istemedikçe commit'e hazırlama.

## 19. Token disiplini

Uzun iç açıklamalar üretme.

Terminal işlemleri ve kendi ara raporları kısa olsun.

Kullanıcıya yalnız gerekli sonucu ver.

Tekrar tekrar mevcut mimariyi özetleme.

## 20. Stop condition

İstenen görev tamamlandığında DUR.

Bir sonraki roadmap maddesine otomatik geçme.

Son rapor maksimum:

- ne değişti
- test sonucu
- blocker/security finding
- commit/hash gerekiyorsa

olsun.
