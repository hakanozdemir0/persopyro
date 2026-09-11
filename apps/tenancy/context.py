"""
Kiracı bağlamı (tenant context).

KURAL (docs/ARCHITECTURE.md bölüm 2.3): veritabanı erişimi kiracı bağlamı
olmadan yapılmaz. Bağlam, PostgreSQL oturum değişkeni `app.tenant_id` üzerinden
verilir ve RLS politikaları bunu okur.

İki katmanlı güvence:
  1. Uygulama katmanı: `require_tenant_context()` → bağlam yoksa açık hata.
  2. Veritabanı katmanı (son savunma): RLS politikaları → bağlam yoksa 0 satır
     (fail-closed; sızma değil, veri görünmez).

`set_config(..., is_local=True)` kullanılır: ayar İŞLEM (transaction) ömrüne
bağlıdır ve işlem bitince kaybolur. Bu, bağlantı havuzu kullanıldığında bağlamın
başka bir isteğe sızmasını engeller.

UYARI (ölçülmüş davranış): PostgreSQL, SET LOCAL ile tanışan özel bir parametreyi
işlem sonunda NULL'a değil BOŞ STRING'e döndürür. Bu yüzden okuma tarafında
`NULLIF(current_setting('app.tenant_id', TRUE), '')` şarttır
(kanıt: docs/CHANGELOG.md 0.4.0, iddia A8).
"""

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from django.db import connection, transaction

_TENANT_ID: ContextVar[uuid.UUID | None] = ContextVar("persopyro_tenant_id", default=None)

_SET_TENANT_SQL = "SELECT set_config('app.tenant_id', %s, true)"
_READ_TENANT_SQL = "SELECT NULLIF(current_setting('app.tenant_id', TRUE), '')::uuid"


class TenantContextMissingError(RuntimeError):
    """Kiracı bağlamı gerekliyken ayarlanmadığında yükseltilir."""


def current_tenant_id() -> uuid.UUID | None:
    """Uygulama tarafındaki aktif kiracı (yoksa None)."""
    return _TENANT_ID.get()


def require_tenant_id() -> uuid.UUID:
    """Aktif kiracıyı döndürür; yoksa hata yükseltir."""
    tenant_id = current_tenant_id()
    if tenant_id is None:
        raise TenantContextMissingError(
            "Kiracı bağlamı ayarlanmadan kiracıya bağlı veriye erişilemez. "
            "Servis katmanında `tenant_context(...)` kullanın."
        )
    return tenant_id


def database_tenant_id() -> uuid.UUID | None:
    """Veritabanı oturumundaki kiracı bağlamını okur (RLS ile aynı kaynak)."""
    with connection.cursor() as cur:
        cur.execute(_READ_TENANT_SQL)
        return cur.fetchone()[0]


def tenant_id_by_slug(slug: str) -> uuid.UUID | None:
    """
    Slug → kiracı kimliği.

    Giriş öncesi (kiracı bağlamı yokken) çağrılır. Kiracı tablosu RLS + FORCE ile
    korunduğu ve FORCE tablo sahibini de bağladığı için doğrudan sorgulanamaz;
    bu yüzden yalnızca eşleme tutan `hr.tenant_directory` tablosu kullanılır
    (RLS yok, uygulama rolü yalnızca okur).
    """
    return _lookup_tenant("SELECT tenant_id FROM hr.tenant_directory WHERE slug = %s", slug)


def tenant_id_by_domain(domain: str) -> uuid.UUID | None:
    """Alan adı → kiracı kimliği (aynı gerekçe; istek hattında kullanılır)."""
    return _lookup_tenant(
        "SELECT tenant_id FROM hr.tenant_directory WHERE domain = %s", domain
    )


def _lookup_tenant(sql: str, value: str) -> uuid.UUID | None:
    """
    Eşleme sorgusu; kayıt yoksa None döner.

    DİKKAT: Eşleme bulunamaması NORMAL bir durumdur (ör. localhost'un bir
    kiracıya bağlı olmaması). Bu yüzden boş sonuç hata değildir.
    """
    if not value:
        return None
    with connection.cursor() as cur:
        cur.execute(sql, [value.strip().lower()])
        satir = cur.fetchone()
        return satir[0] if satir else None


@contextmanager
def tenant_context(tenant_id: uuid.UUID | str | None) -> Iterator[None]:
    """
    Kiracı bağlamını kurar: uygulama tarafı + veritabanı tarafı.

    Bir işlem (transaction) içinde çalışır; işlem bitince veritabanı ayarı
    kendiliğinden kaybolur. Çıkışta, önceki değer geri yüklenir; böylece iç içe
    kullanımda dış bağlam sızmaz/bozulmaz.

    `tenant_id=None` verilirse bağlam temizlenir (kiracı dışı işlemler: örneğin
    yalnızca kiracı kaydı oluşturma).
    """
    token = _TENANT_ID.set(uuid.UUID(str(tenant_id)) if tenant_id else None)
    try:
        with transaction.atomic():
            with connection.cursor() as cur:
                cur.execute(_READ_TENANT_SQL)
                previous = cur.fetchone()[0]
        try:
            with transaction.atomic():
                with connection.cursor() as cur:
                    cur.execute(_SET_TENANT_SQL, [str(tenant_id) if tenant_id else ""])
            yield
        finally:
            with transaction.atomic():
                with connection.cursor() as cur:
                    cur.execute(
                        _SET_TENANT_SQL, [str(previous) if previous else ""]
                    )
    finally:
        _TENANT_ID.reset(token)
