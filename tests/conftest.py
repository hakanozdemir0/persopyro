"""
pytest ortak kurulumu ve kiracı verisi üreten yardımcılar.

KRİTİK: Kiracıya bağlı veri oluşturmak da kiracı bağlamı gerektirir. Bir kiracı
kaydını oluşturmak bile `tenant_context(<o kiracının id'si>)` içinde yapılır —
RLS politikası `id = current_tenant_id()` biçimindedir. Bu bilinçlidir: kiracı
kimliğini bilmeyen bir kod onu üretemez.
"""

import base64
import uuid
from pathlib import Path

import pytest
from django.conf import settings
from django.db import connection
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.tenancy.context import tenant_context
from apps.tenancy.models import Tenant

YETKI_SQL = (Path(settings.BASE_DIR) / "scripts" / "02_grant_app_privileges.sql").read_text(
    encoding="utf-8"
)

# Testler için sabit (gizli olmayan) alan şifreleme anahtarı.
# Fernet tam olarak 32 baytlık base64 anahtar ister.
TEST_ENCRYPTION_KEY = base64.urlsafe_b64encode(b"\x00" * 32).decode()


@pytest.fixture(scope="session", autouse=True)
def veritabani_yetkileri(django_db_setup, django_db_blocker) -> None:  # noqa: ANN001, PT004
    """
    Test veritabanında uygulama rolü yetkilerini uygular ve cache tablosunu hazırlar.

    Cache tablosu migration'larla gelmez (`createcachetable` ile oluşur); test
    veritabanında yoksa hız sınırlama ve cache kullanımı hata verir.
    """
    from django.core.management import call_command

    with django_db_blocker.unblock():
        with connection.cursor() as cur:
            cur.execute(YETKI_SQL)
        call_command("createcachetable", verbosity=0)
    yield


@pytest.fixture(autouse=True)
def alan_sifreleme_anahtari(monkeypatch) -> None:  # noqa: ANN001
    """Şifreleme anahtarı testlerde ortamdan bağımsız olsun."""
    monkeypatch.setenv("FIELD_ENCRYPTION_KEY", TEST_ENCRYPTION_KEY)


@pytest.fixture(autouse=True)
def test_ortami_ayarlari(settings, monkeypatch) -> None:  # noqa: ANN001
    """
    Test ortamı ayarları.

    * Hız sınırlama (throttling) varsayılan olarak KAPATILIR; aksi halde testler
      birbirini etkiler (sayaçlar paylaşımlı cache'te tutulur).
      DİKKAT: DRF, oranları `SimpleRateThrottle.THROTTLE_RATES` sınıf özniteliğine
      modül yüklenirken bağlar; bu yüzden ayar değiştirmek yetmez, sınıf özniteliği
      de yamalanmalıdır. Sınırın ÇALIŞTIĞI ayrı bir testle doğrulanır.
    * ALLOWED_HOSTS genişletilir: alan adına göre kiracı çözümlemesi test edilir.
    """
    from rest_framework.throttling import SimpleRateThrottle

    settings.ALLOWED_HOSTS = ["*"]
    monkeypatch.setattr(
        SimpleRateThrottle,
        "THROTTLE_RATES",
        {"auth": None, "user": None, "anon": None},
    )


@pytest.fixture
def tenant_a(db):  # noqa: ANN001, ANN201
    tid = uuid.uuid4()
    with tenant_context(tid):
        return Tenant.objects.create(id=tid, name="Kiracı A", slug="kiraci-a", status="active")


@pytest.fixture
def tenant_b(db):  # noqa: ANN001, ANN201
    tid = uuid.uuid4()
    with tenant_context(tid):
        return Tenant.objects.create(id=tid, name="Kiracı B", slug="kiraci-b", status="active")


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


@pytest.fixture
def api_client() -> APIClient:
    return APIClient()


@pytest.fixture
def izinli_kullanici():
    """
    Kullanıcıya verilen izin kodlarıyla bir rol atar.

    Kullanım: `izinli_kullanici(user, tenant, "employee.read", "leave.request.approve")`
    """
    from apps.rbac.models import Permission, Role, RolePermission, UserRole

    def _ver(user, tenant, *codes: str):  # noqa: ANN001
        with tenant_context(tenant.id):
            rol, _ = Role.objects.get_or_create(
                tenant=tenant, code="test-rolu", defaults={"name": "Test Rolü"}
            )
            for code in codes:
                RolePermission.objects.get_or_create(
                    tenant=tenant, role=rol, permission=Permission.objects.get(code=code)
                )
            UserRole.objects.get_or_create(tenant=tenant, user=user, role=rol)
        return user

    return _ver
