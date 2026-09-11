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

### URL

`http://127.0.0.1:8000/test-ui/login/` (panel: `/test-ui/`)

### Demo kullanıcılar (parola: 1. adımda verilen `DEMO_PASSWORD`)

| E-posta | Rol | Kapsam | Ne için |
|---|---|---|---|
| `demo@persopyro.test` | `tenant_admin` | kiracı geneli | her şeyi görmek, nihai onay |
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
14. **Yetki Yönetimi**: rol tablosundan bir rolü seç → **Yetkileri düzenle** →
    modül gruplarından izinleri işaretle ("Tümünü seç" üstte) → gerekçe yaz →
    **Yetkileri kaydet**. Değişiklik anında geçerlidir; kendi rolünüz ve platform
    izinleri düzenlenemez. Rol atamak için: **Çalışanlar → Detay → Rol ve Yetkiler**.
15. **Departmanlar → Taşı**: açılan pencerede hedef üst birimi ağaçtan seç
    ("En üst seviyeye taşı (kök)" seçeneği de vardır); onay sorusundan sonra ağaç
    yenilenir ve Finans müdürünün gördüğü çalışanlar yeni ağaca göre değişir.
    Taşınan birimin kendisi ve alt birimleri listede **çıkmaz** (sunucu üretir).

### Otomatik doğrulama

`bash scripts/run_tests.sh tests/test_testui.py` (arayüz + kapsam + yetki + çıkış +
bakiye + rol yönetimi + taşıma) ve `bash scripts/verify_fresh_install.sh`
(**6c-1…6c-4** adımları: giriş, kapsam görünürlüğü, çıkışın oturumu kapatması,
bakiye/unvan/rol görünürlüğü, taşıma hedefleri, rol atama-kaldırma, yetkisiz 403,
üretim varsayılanında 404).

### Kapsam dışı

Impersonation **yok** (kullanıcı değiştirmek için çıkış + giriş). Üretim UI'ı,
tema/tasarım sistemi, JS çerçevesi, arayüz üzerinden yetki yönetimi bu kapsamda
değildir.
