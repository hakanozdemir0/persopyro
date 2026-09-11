"""
pytest ortak kurulumu ve kiracı verisi üreten yardımcılar.

KRİTİK: Kiracıya bağlı veri oluşturmak da kiracı bağlamı gerektirir. Bir kiracı
kaydını oluşturmak bile `tenant_context(<o kiracının id'si>)` içinde yapılır —
RLS politikası `id = current_tenant_id()` biçimindedir. Bu bilinçlidir: kiracı
kimliğini bilmeyen bir kod onu üretemez.
"""

import uuid
from pathlib import Path

import pytest
from django.conf import settings
from django.db import connection

from apps.accounts.models import User
from apps.tenancy.context import tenant_context
from apps.tenancy.models import Tenant

YETKI_SQL = (Path(settings.BASE_DIR) / "scripts" / "02_grant_app_privileges.sql").read_text(
    encoding="utf-8"
)


@pytest.fixture(scope="session", autouse=True)
def veritabani_yetkileri(django_db_setup, django_db_blocker) -> None:  # noqa: ANN001, PT004
    """Test veritabanında uygulama rolü yetkilerini uygular (RLS denetimi dahil)."""
    with django_db_blocker.unblock():
        with connection.cursor() as cur:
            cur.execute(YETKI_SQL)
    yield


@pytest.fixture
def tenant_a(db):  # noqa: ANN001, ANN201
    tid = uuid.uuid4()
    with tenant_context(tid):
        return Tenant.objects.create(id=tid, name="Kiracı A", status="active")


@pytest.fixture
def tenant_b(db):  # noqa: ANN001, ANN201
    tid = uuid.uuid4()
    with tenant_context(tid):
        return Tenant.objects.create(id=tid, name="Kiracı B", status="active")


@pytest.fixture
def user_a(tenant_a):  # noqa: ANN001, ANN201
    with tenant_context(tenant_a.id):
        return User.objects.create_user(
            email="a@ornek.test",
            password="Guclu-Parola-123",
            tenant=tenant_a,
            first_name="Ayşe",
            last_name="Kaya",
        )


@pytest.fixture
def user_b(tenant_b):  # noqa: ANN001, ANN201
    with tenant_context(tenant_b.id):
        return User.objects.create_user(
            email="b@ornek.test",
            password="Guclu-Parola-456",
            tenant=tenant_b,
            first_name="Burak",
            last_name="Demir",
        )
