"""
Platform katmanı (OD3/OD20/OD22/OD25 — kapalı kararlar).

Bu uygulama **ayrı güvenlik sınırıdır**: platform kullanıcıları, rolleri ve
token'ları kiracı (`hr`) tablolarıyla birleştirilmez; kiracı RLS politikaları
platform için gevşetilmez ve `BYPASSRLS` kullanılmaz.

NEDEN DJANGO MODELİ YOK: hedeflenen nesneler `platform` PostgreSQL şemasında
yaşar. Django'nun `db_table` alanı şema niteleyicisi taşımaz (`"platform.users"`
tek tanımlayıcı olarak tırnaklanır), bu yüzden ORM modelleri kurmak şema sınırını
"hack"ler ve güvenlik sınırını bulanıklaştırırdı. Bu pakette tablolar migration
içinde ham SQL ile kurulur; erişim `apps/platform/services.py` içindeki dar SQL
arayüzünden yapılır (endpoint bu paketin kapsamı dışıdır — OD22/M1–M5).

Yetki modeli (docs/DATABASE.md B.1, D.4):

  * `persopyro_app` (kiracı runtime yolu): `platform` şemasında **hiç yetki yok**
    → kiracı isteğinden platform verisine yol yoktur.
  * `persopyro_platform`: `platform` şemasında tam DML; `hr` şemasında bu pakette
    **hiç yetki verilmez** (RLS'e tabi kiracı erişimi, erişim-onay katmanıyla
    birlikte ayrı iş kalemidir — şema varlığı erişim yetkisi değildir).
  * Rol NOSUPERUSER + NOBYPASSRLS kalır.
"""
