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

## Manuel test arayüzü (`/test-ui/`)

Amaç: organizasyon + izin akışını **tarayıcıdan gerçek kullanıcı gibi** uçtan uca
denemek. Üretim kullanıcı arayüzü değildir; yeni frontend zinciri (React/Vite/
Tailwind/npm) kurulmaz — Django şablonu + vanilla JS/CSS kullanılır.

Kapı: `TEST_UI_ENABLED` (`PERSOPYRO_TEST_UI`, varsayılan `DJANGO_DEBUG`). Yani
`DJANGO_DEBUG=1` iken arayüz açıktır; üretimde (`DJANGO_DEBUG=0`, `PERSOPYRO_TEST_UI`
verilmemiş) bütün `/test-ui/*` yolları **404** döner ve hiçbir veri açılmaz.
Güvenlik kararı arayüzde verilmez: tüm işlemler servis katmanına gider
(`izin ∧ kapsam`), RLS yürürlükte kalır, CSRF/oturum mevcut mekanizmadır.

### Çalıştırma (Ubuntu — birkaç komut)

```bash
cd ~/persopyro
set -a && . ./.env && set +a

# 1) Demo verisi + demo kullanıcılar (yazma: sahip rol; parola SADECE bu komutta geçer,
#    depoya yazılmaz, migration'lara konmaz)
POSTGRES_USER="$POSTGRES_OWNER_USER" POSTGRES_PASSWORD="$POSTGRES_OWNER_PASSWORD" \
DEMO_PASSWORD="Demo-Parola-1234" .venv/bin/python manage.py shell < scripts/_demo_data.py

# 2) Sunucu (okuma/yazma: uygulama rolü — .env'deki değerler)
.venv/bin/python manage.py runserver 127.0.0.1:8000
```

Avantaj: şema hazırsa (`bash scripts/migrate.sh`) ek kurulum gerekmez. Demo verisi
idempotenttir; betik tekrar çalıştırılabilir.

**DİKKAT — şablon önbelleği:** Django şablon yükleyicisi bu sürümde **her zaman**
`cached.Loader` ile sarmalanır; `runserver` otomatik yeniden yükleme yapmadığında
(`--noreload`) şablon (`*.html`) değişiklikleri **sunucu yeniden başlatılmadan
görünmez**. Belirti: düzeltilmiş şablon yerine eski sürüm servis edilir (ör. panel
boş kalır). Kural: her `.html` değişikliğinden sonra sunucuyu durdurup yeniden
başlatın. Gömülü betiği denetlemek için: sayfayı alıp `<script>` içeriğini çıkarın
ve `node --check <dosya>.js` çalıştırın (`verify_fresh_install.sh` adım **6c-7**
bunu otomatik yapar; `node` yoksa atlanır).


### URL

`http://127.0.0.1:8000/test-ui/login/` (panel: `/test-ui/`)

### Demo kullanıcılar (parola: 1. adımda verilen `DEMO_PASSWORD`)

| E-posta | Rol | Kapsam | Ne için |
|---|---|---|---|
| `demo@persopyro.test` | `tenant_admin` | kiracı geneli | kapsam içi işlemler; normal talep onayı yalnız atanmış adım sahibi, izin override'ı ayrı izinle |
| `finans.mudur@persopyro.test` | `dept_manager` | **Finans** (+ alt ağaç) | kapsam/onay testi |
| `personel@persopyro.test` | `employee` | yalnız kendisi | Muhasebe çalışanı; kendi talebi |
| `ik.calisan@persopyro.test` | `employee` | yalnız kendisi | **kapsam dışı** (İnsan Kaynakları) |

Kiracı `slug`: `demo`.

### Elle test adımları (önerilen sıra)

1. `demo@persopyro.test` ile gir → **Panel**: kullanıcı, kiracı, roller, kapsam
   ("Kiracı geneli (TENANT)"), çalışan kaydı, izin bakiyesi, onay kuyruğu.
2. **Departmanlar** sekmesi: ağaç görünümü (`Genel Müdürlük → Finans → Muhasebe`,
   `→ İnsan Kaynakları`); yetkiniz varsa birim oluştur / taşı / soft delete.
3. **Çalışanlar**: kapsamdaki çalışanlar; birim + unvan seçerek yeni çalışan
   oluştur; "Bakiye" ile dönem hakediş/kullanılan/bekleyen/kalan değerleri gör.
4. **İzin Talebi**: bir çalışan seç, tür + tarih ver, oluştur → sonuçta
   **sunucunun hesapladığı** gün sayısı ve durum (`pending`) görünür.
5. Çıkış yap, `finans.mudur@persopyro.test` ile gir → **Onaylar**: yalnızca
   *Muhasebe* talebi (`IZIN_TESTI_MUHASEBE`) görünür; *İnsan Kaynakları* talebi
   (`IZIN_TESTI_IK`) görünmez. **Onayla** → durum ilerler (nihai onay için 1. adım:
   `demo@persopyro.test`); **Reddet** gerekçe ister.
6. `personel@persopyro.test` ile gir → **Panel**: "Yıllık izin bakiyem" kartında
   hak/kullanılan/bekleyen/kalan değerlerini gör; **İzin Talebi → Taleplerim**
   yalnızca kendi kayıtlarını gösterir. `ik.calisan@persopyro.test` ile girip aynı
   onay denenirse **403** ("Bu işlem için yetkiniz veya organizasyon kapsamınız
   yok.") alınır.
7. **Çıkış** düğmesine bas → giriş ekranına dönersin; panel adresini yenile
   (`/test-ui/`) → girişe yönlenir; geri tuşu paneli açmaz. (Çıkış sunucuda
   oturumu kapatır; korunan uçlar **403 `not_authenticated`** döner.)
8. `finans.mudur@persopyro.test` ile gir → **Çalışanlar**: her satırda birim,
   **unvan**, **rol** ve **yıllık izin** özeti görünür (İnsan Kaynakları çalışanı
   listede yoktur). **Detay** → unvan/birim/yönetici ve yıllık izin; rol/yetki
   bölümü bu kullanıcıda kapalıdır (yetki yok).
9. `demo@persopyro.test` ile gir → **Çalışanlar → Detay (personel)**: "Rol ve
   Yetkiler" bölümünde mevcut rolleri ve kapsamlarını gör; **Rol ata** ile ör.
   *İK Yöneticisi* rolünü ata (UNIT rol seçersen birim kapsamı zorunludur),
   sonra **Rolü kaldır** ile kaldır ve istersen **Geri al** ile geri getir
   (OD21: kaldırılan atama sessizce canlanmaz). Kullanıcı **kendi** rolünü
   atayamaz (self-elevation yasağı).
10. **Çalışanlar → Yeni çalışan**: "Bağlanacak kullanıcı" listesinden hesap seç
    (boş bırakılırsa hesap bağlanmaz); yetkiniz varsa **Yeni unvan ekle** ile
    pozisyon tanımlayıp aynı formda seçebilirsin.
11. **İzin Talebi → İzin türleri**: tüm türleri (pasifler dahil) bayraklarıyla gör;
    şirket yöneticisi **Yeni izin türü** ekleyebilir (sistem kodları rezerve) ve
    mevcut türü **Devre dışı bırak** ile pasifleştirebilir. Eklenen tür anında talep
    formundaki listede görünür.
12. **Panel → Karara bağlanan izinler**: onay bekleyen/onaylanan/reddedilen/iptal
    sayıları ve son karara bağlanan talepler (çalışan adı, birim, tür, tarih, gün,
    durum) — kapsamınız dışındaki çalışanlar listede görünmez.
13. **Departmanlar → Düzenle**: birimin adını/kodunu değiştir (denetim kaydı yazılır);
    **Taşı** ayrı düğmedir.
14. **Yetki Yönetimi** (yalnız şirket yöneticisi; diğer roller/görüntüleme yetkisi
    olmayanlar listeyi dahi görmez): rol tablosundan bir rolü seç →
    **Yetkileri düzenle** →
    modül gruplarından izinleri işaretle ("Tümünü seç" üstte) → gerekçe yaz →
    izin ara / **Tümünü seç** / **Hiçbirini seçme** / **Değişiklikleri geri al** ile
    düzenle → **Kaydet**. Kaydetmeden önce "N izin eklenecek, M izin kaldırılacak"
    özetini görürsünüz. Değişiklik anında geçerlidir; kendi rolünüz ve platform
    izinleri düzenlenemez. Rol atamak için: **Çalışanlar → Detay → Rol ve Yetkiler**.
    Yetkisi olmayan kullanıcıda ekran **salt okunur** görünür (kutular kapalı).
15. **Departmanlar → Taşı**: açılan pencerede hedef üst birimi ağaçtan seç
    ("En üst seviyeye taşı (kök)" seçeneği de vardır); onay sorusundan sonra ağaç
    yenilenir ve Finans müdürünün gördüğü çalışanlar yeni ağaca göre değişir.
    Taşınan birimin kendisi ve alt birimleri listede **çıkmaz** (sunucu üretir).

### Otomatik doğrulama

`bash scripts/run_tests.sh tests/test_testui.py` (arayüz + kapsam + yetki + çıkış +
bakiye + rol yönetimi + taşıma + kan grubu/K3) ve `bash scripts/verify_fresh_install.sh`
(**6c-1…6c-10** adımları: giriş, kapsam görünürlüğü, çıkışın oturumu kapatması,
bakiye/unvan/rol görünürlüğü, taşıma hedefleri, rol atama-kaldırma, yetkisiz 403,
kişisel veri kapısı, rol oluşturma, gömülü arayüz JS sözdizimi (`node --check`),
kayıt formu + kan grubunun şifreli saklanması, üretim varsayılanında 404).

### Elle denenecek yeni akışlar (bildirim 5. tur)

1. **Bilgilerim:** hangi rolle girilirse girilsin panelin ilk kartında ad soyad, unvan,
   birim, kayıtlı e-posta, şirket ve yıllık izin satırları görünür.
2. **Hesap açma:** Çalışanlar → "Yeni çalışan" → *2) Giriş hesabı*; "Parola üret" ile
   parola oluştur, kaydı aç, çıkış yapıp o e-posta/parolayla giriş yapılabildiğini gör.
3. **Kişisel veriler:** yönetici çalışan detayında "Kişisel veriler" bölümünü görür ve
   güncelleyebilir; departman yöneticisi aynı bölümü **görmez** (uç `403` döner).
   Vatandaşlık TR dışı seçilirse oturma/çalışma izni türü zorunludur.
4. **İzin türü ekleme:** hazır şablon seç → "Uygula"; alanlar ve özellikler dolar.
5. **Yetki Yönetimi:** yalnız şirket yöneticisinde görünür; "Yeni rol" ile rol açılır,
   "Düzenle" ile ad/kapsam güncellenir (etkin ataması olan rolde kapsam değiştirilemez).
6. **Yetki açıp kapatma:** rol izinlerini değiştir → çıkış yapıp o rolle giriş yap →
   etkinin anında görüldüğünü doğrula.

### Elle denenecek yeni akışlar (bildirim 6. tur)

1. **Görünürlük:** yetkisiz rolle gir (ör. `personel@persopyro.test`) → Departmanlar,
   Çalışanlar, İzin Talebi sekmelerinde oluşturma formları **hiç görünmemeli**
   ("yetkiniz yok" yazısı da olmamalı).
2. **Kendi kaydı:** aynı kullanıcıda çalışan listesinde yalnız kendi kaydı görünür.
3. **İzin durumu noktası:** bugün izinde olan çalışanda yeşil, izni ≤7 gün kalanında
   sarı nokta (ör. DEMO-001).
4. **Eksi bakiye:** İzin Talebi → İzin politikası → "bakiye yetersizse talep açılabilir"
   açıkken, kalan günden uzun talep oluşturulabilir; kapatıp deneyince `409` gelir.
5. **Kendi talebi:** Onaylar sekmesinde kendi talebinde Onayla/Reddet **yok**;
   Düzenle/İptal var. Düzenle → tarih değiştir → kaydet: eski talep iptal, yenisi onaya
   sunulur (mesaj bunu söyler).
6. **Onaylayan bakiyesi:** yönetici hesabıyla Onaylar sekmesinde satırda talep sahibinin
   bakiyesi görünür.
7. **Tarih biçimi:** tüm tarihler `gg.aa.yyyy`.
8. **Yönetici seçimi:** Yeni çalışan formunda "Bağlı olduğu yönetici" seçilir; detayda
   yönetici görünür.
9. **JS sözdizimi:** şablon içi betik değiştiyse `bash scripts/verify_fresh_install.sh`
   6c-7 adımı `node --check` ile denetler (yerelde de: gömülü betiği çıkarıp
   `node --check`).

### Kapsam dışı

Impersonation **yok** (kullanıcı değiştirmek için çıkış + giriş). Üretim UI'ı,
tema/tasarım sistemi ve JS çerçevesi bu kapsamda değildir. Rol izinlerinin
düzenlenmesi **vardır** ama kararı arayüz vermez: yetki kararı servis katmanında
(`apps/rbac/services/roles.py`) verilir, arayüz yalnızca bunu yansıtır.

### Elle denenecek yeni akışlar (bildirim 7. tur — görsel iyileştirme)

1. **Genel görünüm:** üst bar kaydırmada sabit ve yarı saydam; seçili sekme dolu
   mavi; kart başlıkları sayı rozetiyle (`Çalışanlar · 3 kayıt`).
2. **Form alanları:** etiketler koyu ve yarı kalın, odaklanan alan mavi çerçeve +
   halka; alanlar ekran genişliğine göre 2-4 sütuna yayılır.
3. **Tablolar:** başlık satırı açık gri ve büyük harf; satır üzerine gelince hafif
   vurgu; geniş tablolar kart içinde **yatay kayar** (sayfa taşmaz).
4. **Durumlar:** `pill` etiketleri (onaylandı/bekliyor/reddedildi) renkli; işlem
   sonucu mesajı sol kenarlı ve renkli; modal açılışta hafif animasyonlu.
5. **Çalışanlar → Çalışan listesi:** kayıt yoksa kesikli çerçeveli boş durum
   metni; kapsamı SELF olan kullanıcıda açıklama "yalnız kendi kaydınız" der
   (önceki sürümde eski alan adı okunduğu için yanlış metin görünüyordu).
6. **Çalışan detayı:** kişisel veriler tablosu etiket/değer düzeninde; IBAN satırı
   **bir kez** görünür (önceki sürümde iki kez basılıyordu).

### Elle denenecek yeni akışlar (bildirim 8. tur — kayıt formu bütün bilgileri ister)

1. **Çalışanlar → Yeni çalışan:** kişisel veri bölümünde **kimlik no** ve **kan
   grubu** alanları vardır; kan grubu açılır liste kapalı kümeden gelir
   (A Rh+ … 0 Rh-; boş = bilinmiyor). Zorunlu alanlar yalnız ad/soyad olarak
   işaretlidir.
2. **Tek adımda kayıt:** ad/soyad + kimlik no + kan grubu + iletişim + adres +
   yakın kişi + vatandaşlık/izin + banka/IBAN doldurup **Çalışanı oluştur** →
   kayıt oluşur; aynı sayfadaki **Detay** ile bütün alanlar dolu görünür.
3. **Kan grubu şifreli:** Detay → *Kişisel veriler* bölümünde kan grubu **etiketle**
   görünür (`0 Rh+`). Veritabanında düz metin **değildir**:
   `sql_demo "SELECT blood_type FROM hr.employee_personal_data WHERE employee_id='<id>'"`
   → `gAAAAA…` ile başlar.
4. **Yetki sınırı:** `finans.mudur@persopyro.test` (departman yöneticisi) aynı
   çalışanın kişisel verisini görmez ve yazamaz (`403`); `personel@persopyro.test`
   yalnız kendi kaydını görür.
5. **Geçersiz değer:** kan grubu alanına elle geçersiz bir değer gönderilirse
   (`{"blood_type":"XY"}`) sunucu **400** + "kapalı küme" mesajı verir (fail-closed).
6. **Boşaltma:** kan grubu "(bilinmiyor)" seçilip kaydedilirse alan temizlenir
   (`null`); diğer alanlar korunur.


### Elle denenecek yeni akışlar (bildirim 9. tur — UX/fonksiyon düzeltmeleri)

1. **Genel yerleşim:** masaüstünde içerik ~1400 px'e kadar genişler; üst sırada
   **yan yana 3 kart**, altında geniş tablo/detay alanı. %100 zoom'da normal
   kullanımda **yatay kaydırma çubuğu oluşmaz** (ölçüm: sayfa
   `scrollWidth - innerWidth <= 0`, kart içi tablolarda `.tablo-kaydir` içi kayma).
2. **Çalışanlar:** tabloda *Çalışan* (ad + e-posta iki satır), *Çalışan no*,
   *Unvan*, *Birim*, *Yönetici*, *Rol (yetki)*, *Kalan izin*, *Durum*, *İşlem*
   kolonları vardır; teknik UUID gösterilmez. Üstteki **filtre** ad/no/birim/unvan/
   e-postaya göre satırları süzer, `n / m kayıt` yazar.
3. **Detay çekmecesi:** `Detay` → **sağdan** panel açılır; liste yerleşimi
   kaymaz. Aynı çalışanın `Detay`ına tekrar basılır → **kapanır**; başka çalışanın
   `Detay`ı → önceki kapanır (aynı anda tek detay). Buton metni
   `Detay` ⇄ `Detayı kapat`; Escape ve çekmece başlığındaki **Kapat** düğmesi de
   kapatır.
4. **Otomatik çalışan numarası:** Yeni çalışan formunda **numara alanı
   salt-okunurdur** ("Otomatik (EMP-000001 biçimi)"); kaydettikten sonra mesaj
   *"verilen numara: EMP-0000NN"* der. Numara **birim değişince değişmez**
   (OD21 taşıma akışı sonrası listede aynı numara kalır).
5. **Rol ve Yetkiler (çalışan detayı):** rol adı + teknik kod, kapsam, **kapsam
   birimi**, verilme tarihi görünür; yeni atamada `UNIT/UNIT_DIRECT` rol
   seçilirse **kapsam zorunlu**, `TENANT` rolünde kapsam seçicisi **devre dışı**.
   Unvan (ör. *Finans Uzmanı*) ile yetki rolü (*Departman Yöneticisi*) ayrı
   gösterilir.
6. **Yetki Yönetimi:** solda rol kartları (teknik kod küçük yazı), sağda izin
   **grupları**; her grup `n / m seçili` sayacını taşır, izinler okunabilir ad +
   küçük teknik kod olarak grid'de listelenir. `Tümünü seç`, `Hiçbirini seçme`,
   `Değişiklikleri geri al` çalışır.
7. **İzin türleri:** özellikler **2×2 kart** düzenindedir (onay kutusu başlıkla
   aynı hizada, açıklama başlığın altında, kartlar eşit yükseklikte); tablo
   başlıkları *Kod · Ad · Kategori · Ücretli · Bakiye · Belge · Onay · Durum ·
   İşlem*.
8. **İzin bakiyesi:** Panel'de **kapsam** bilançosu üretilir (hak / kullanılan /
   bekleyen / kalan); çalışan kaydı olmayan kullanıcıda üstte "Bu kullanıcının
   çalışan kaydı yok" bilgisi yer alır ama **kapsamındaki** çalışanların
   bakiyeleri listelenir. Çalışan (SELF) yalnız kendi bakiyesini görür. Değerler
   backend bakiye kaydından gelir; arayüz hesap yapmaz.
9. **Departman taşıma:** sol ağaçtan birim seç → sağdaki kartta **Taşı** →
   modal; hedef liste **organizasyon ağacından** (kendisi ve alt birimleri hariç)
   gelir, kullanıcı UUID girmez; onay metni *"… birimini … altına taşımak
   istediğinize emin misiniz?"*; taşıma sonrası ağaç ve kapsam yenilenir.
10. **Çıkış:** sağ üst `Çıkış` sunucu tarafında oturumu kapatır; çıkıştan sonra
    `/test-ui/api/me` **403**, panel adresi **login'e yönlenir**, tarayıcı geri
    tuşu/refresh paneli geri getirmez.

### Doğrulama betiği tuzağı (6c-9 sonrası not)

`verify_fresh_install.sh` içinde arka planda bir `runserver` çalışırken **çıplak
`wait`** kullanmak betiği kilitler (bash, o daemon'un bitmesini bekler). Paralel
işler için PID'ler toplanıp `wait $PID_LISTESI` yazılmalı; bu düzeltme 6c-9
adımında uygulandı (iki eşzamanlı `curl` süreci yalnız kendileri beklenir).

### Elle denenecek yeni akışlar (bildirim 11. tur — izin hakları, hakediş, bakiyeler)

1. **Çalışan detayı:** bölümler *İş Bilgileri → İzin Hakları → Rol ve Yetkiler →
   Kişisel Bilgiler*; **ham yetki kodu listesi yok**.
2. **İzin Hakları:** tabloda yıllık hak/kullanılan/bekleyen/kalan ve üstte
   *Sonraki yıllık hakediş: gg.aa.yyyy* (işe giriş yıl dönümü). Yetkili kullanıcıda
   *Politikadan hakediş tanımla* ve satır başına *Düzelt* düğmesi görünür.
3. **Bakiye düzeltme:** girilen değer doğrudan yazılmaz; gerekçesiz kayıt **reddedilir**
   ("Manuel düzeltme için anlamlı bir gerekçe zorunludur"), gerekçeli kayıt kalan günü
   günceller (defter + `leave.balance.adjustment` denetim kaydı).
4. **İzin Bakiyeleri sekmesi:** kapsam süzgeçli tablo (çalışan/izin türü/hakediş/kullanılan/
   bekleyen/kalan/sonraki hakediş); filtreler sunucuda çalışır, "Düşük (≤ 3 gün)" ve
   "Tükendi" seçenekleri vardır. Çalışan (SELF) yalnız kendi satırını görür.
5. **Yeni Çalışan:** Çalışanlar ekranındaki alt sekmede **yalnız** kayıt formu vardır;
   ad/soyad/kurumsal e-posta/parola/birim/işe giriş **zorunludur** (eksikse tarayıcıda
   uyarı, sunucuda 400). Çalışan numarası alanı salt-okunurdur.
6. **Onaylar:** *Onay beklenen* kolonunda sıradaki onaylayıcı adı + unvanı; iki adımlı
   akışta sıralı liste görünür.
7. **Panel:** üstte 4 özet kartı; yaklaşan izinlerde renk **ve** metin (İzinde / N gün
   sonra); altta "Bakiyesi kritik çalışanlar".
8. **Yetki Yönetimi:** sizde olmayan izinler **verilemez** ama rolde seçiliyse
   **kaldırılabilir** (onay kutusu kilitli değildir; kilit nedeni yazılıdır).
