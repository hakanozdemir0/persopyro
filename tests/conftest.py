"""
pytest ortak kurulumu.

Test veritabanı, ana veritabanının şema ve yetki düzenini taklit etmelidir;
aksi halde rol/RLS testleri yanlış sonuç verir.

NOT (geçiş dönemi): Django'nun yerleşik tabloları şu an migration ile
oluşturuluyor ve `hr` şeması henüz bir migration tarafından yaratılmıyor. Bu
yüzden `hr` şeması burada, test veritabanı için elle oluşturulup yetkilendirilir.
Faz 2'de ilk migration `CREATE SCHEMA IF NOT EXISTS hr` çalıştıracak; o zaman bu
kurulum sadeleşecek (bkz. docs/ARCHITECTURE.md bölüm 22).
"""

from pathlib import Path

import pytest
from django.conf import settings
from django.db import connection

YETKI_SQL = (Path(settings.BASE_DIR) / "scripts" / "02_grant_app_privileges.sql").read_text(
    encoding="utf-8"
)


@pytest.fixture(scope="session", autouse=True)
def veritabani_yetkileri(django_db_setup, django_db_blocker) -> None:  # noqa: ANN001, PT004
    """Test veritabanında hr şemasını ve uygulama rolü yetkilerini uygular."""
    with django_db_blocker.unblock():
        with connection.cursor() as cur:
            cur.execute(YETKI_SQL)
    yield
