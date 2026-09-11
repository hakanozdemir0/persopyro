"""
M5 (OD25/OD28) regresyon testleri — public ID ve prefix.

Kapsam:
  * `hr.tenants.public_id_prefix`: slug'tan türetilir, **immutable**, tam UNIQUE
    (kalıcı rezerve), rezerve adlar reddedilir, format doğru
  * `hr.users.public_id`: `<prefix>-<8×Crockford>`, **global** benzersiz,
    immutable, Crockford alfabesinde `I/L/O/U` **yok**
  * slug değişimi prefix'i ve mevcut public ID'leri **değiştirmez** (OD28)
  * kiracı bağlamı yokken üretim yapılmaz (fail-closed)
"""

import re
import uuid

import pytest
from django.db import IntegrityError, connection, transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.tenancy.context import tenant_context
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db

CROCKFORD_ALFABE = set("0123456789ABCDEFGHJKMNPQRSTVWXYZ")
PREFIX_DESENI = re.compile(r"^[a-z0-9]{3,20}$")
PUBLIC_ID_DESENI = re.compile(r"^[a-z0-9]{3,20}-[0-9A-HJKMNP-TV-Z]{8}$")


def _prefix(tenant) -> str:  # noqa: ANN001
    with tenant_context(tenant.id):
        with connection.cursor() as cur:
            cur.execute(
                "SELECT public_id_prefix FROM hr.tenants WHERE id = %s", [str(tenant.id)]
            )
            return cur.fetchone()[0]


def _public_id(user, tenant) -> str:  # noqa: ANN001
    with tenant_context(tenant.id):
        with connection.cursor() as cur:
            cur.execute("SELECT public_id FROM hr.users WHERE id = %s", [str(user.pk)])
            return cur.fetchone()[0]


# --- prefix -----------------------------------------------------------------


def test_tenant_olusturulunca_prefix_slugtan_uretilir(tenant_a) -> None:  # noqa: ANN001
    prefix = _prefix(tenant_a)
    assert PREFIX_DESENI.fullmatch(prefix), prefix
    # slug 'kiraci-a' → tire kaldırılır
    assert prefix == tenant_a.slug.replace("-", "")


def test_prefix_immutable(tenant_a) -> None:  # noqa: ANN001
    """OD28: prefix bir kez atandıktan sonra değiştirilemez (DB tetikleyicisi)."""
    mevcut = _prefix(tenant_a)
    with pytest.raises(Exception, match="public_id_prefix degistirilemez"):
        with tenant_context(tenant_a.id):
            with transaction.atomic():
                with connection.cursor() as cur:
                    cur.execute(
                        "UPDATE hr.tenants SET public_id_prefix = 'baskaonek' WHERE id = %s",
                        [str(tenant_a.id)],
                    )
    assert _prefix(tenant_a) == mevcut


def test_slug_degisimi_prefixi_degistirmez(tenant_a, user_a) -> None:  # noqa: ANN001
    """OD25/3 + OD28: rebrand/slug değişimi kimliği kaydırmaz."""
    onceki_prefix = _prefix(tenant_a)
    onceki_public_id = _public_id(user_a, tenant_a)

    with tenant_context(tenant_a.id):
        tenant_a.slug = "yeni-slug"
        tenant_a.save(update_fields=["slug", "updated_at"])

    assert _prefix(tenant_a) == onceki_prefix
    assert _public_id(user_a, tenant_a) == onceki_public_id

    # Yeni kullanıcı da ESKİ (sabit) prefix ile üretilir.
    with tenant_context(tenant_a.id):
        yeni = User.objects.create_user(
            email="yeni@ornek.test", password="Guclu-Parola-123", tenant=tenant_a
        )
    assert _public_id(yeni, tenant_a).startswith(f"{onceki_prefix}-")


def test_prefix_tam_unique_ve_kalici_rezerve(tenant_a) -> None:  # noqa: ANN001
    """OD25/4: soft-delete edilmiş kiracının prefix'i yeniden tahsis edilmez."""
    prefix = _prefix(tenant_a)
    with tenant_context(tenant_a.id):
        Tenant.objects.filter(pk=tenant_a.id).update(deleted_at=timezone.now())

    yeni_id = uuid.uuid4()
    with tenant_context(yeni_id):
        with pytest.raises(IntegrityError), transaction.atomic():
            Tenant.objects.create(
                id=yeni_id, name="Kopya", slug="baska-slug", public_id_prefix=prefix
            )


def test_rezerve_prefix_ve_slug_reddedilir(tenant_a) -> None:  # noqa: ANN001
    """OD28 rezerve listesi: `admin`, `platform`, `plt`, ... kullanılamaz."""
    yeni_id = uuid.uuid4()
    with tenant_context(yeni_id):
        with pytest.raises(IntegrityError), transaction.atomic():
            Tenant.objects.create(
                id=yeni_id, name="Rezerve", slug="rezerve-kiraci", public_id_prefix="admin"
            )

    yeni_id = uuid.uuid4()
    with tenant_context(yeni_id):
        with pytest.raises(IntegrityError), transaction.atomic():
            Tenant.objects.create(id=yeni_id, name="Rezerve Slug", slug="platform")


def test_prefix_otomatik_atama_ve_format_dogrulugu() -> None:
    """Format kuralı DB'de: geçersiz prefix reddedilir, geçerli olan atanır."""
    yeni_id = uuid.uuid4()
    with tenant_context(yeni_id):
        kiraci = Tenant.objects.create(id=yeni_id, name="Otomatik", slug="Acme Holding")
    assert PREFIX_DESENI.fullmatch(_prefix(kiraci))
    assert _prefix(kiraci) == "acmeholding"

    yeni_id = uuid.uuid4()
    with tenant_context(yeni_id):
        with pytest.raises(IntegrityError), transaction.atomic():
            Tenant.objects.create(
                id=yeni_id, name="Bozuk", slug="bozuk-kiraci", public_id_prefix="A!"
            )


# --- public_id --------------------------------------------------------------


def test_public_id_formati_ve_crockford_alfabesi(user_a, tenant_a) -> None:  # noqa: ANN001
    public_id = _public_id(user_a, tenant_a)
    assert PUBLIC_ID_DESENI.fullmatch(public_id), public_id
    son_ek = public_id.split("-")[-1]
    assert set(son_ek) <= CROCKFORD_ALFABE
    assert not (set(son_ek) & set("ILOU")), "Crockford alfabesinde I/L/O/U yoktur"
    assert public_id.startswith(f"{_prefix(tenant_a)}-")


def test_public_id_global_benzersiz(user_a, tenant_a, tenant_b, user_b) -> None:  # noqa: ANN001
    assert _public_id(user_a, tenant_a) != _public_id(user_b, tenant_b)

    with connection.cursor() as cur:
        cur.execute(
            "SELECT indexdef FROM pg_indexes WHERE schemaname='hr'"
            " AND tablename='users' AND indexname='users_public_id_uniq_idx'"
        )
        tanim = cur.fetchone()
    assert tanim is not None and "UNIQUE" in tanim[0].upper()

    with tenant_context(tenant_a.id):
        with pytest.raises(IntegrityError), transaction.atomic():
            User.objects.create_user(
                email="kopya@ornek.test",
                password="Guclu-Parola-123",
                tenant=tenant_a,
                public_id=_public_id(user_a, tenant_a),
            )


def test_public_id_immutable(user_a, tenant_a) -> None:  # noqa: ANN001
    mevcut = _public_id(user_a, tenant_a)
    with pytest.raises(Exception, match="public_id degistirilemez"):
        with tenant_context(tenant_a.id):
            with transaction.atomic():
                with connection.cursor() as cur:
                    cur.execute(
                        "UPDATE hr.users SET public_id = %s WHERE id = %s",
                        [f"{mevcut.split('-')[0]}-AAAAAAAA", str(user_a.pk)],
                    )
    assert _public_id(user_a, tenant_a) == mevcut


def test_baglam_yokken_public_id_uretilmez() -> None:
    """
    Fail-closed: kiracı bağlamı yoksa kimlik üretilmez, kayıt reddedilir.

    (Fonksiyon `NULL` döner → `NOT NULL` kısıtı ihlal edilir; sessizce
    kapsamsız bir kimlik üretilmez.)
    """
    with connection.cursor() as cur:
        cur.execute("SELECT hr.generate_public_id(NULL)")
        assert cur.fetchone()[0] is None


def test_crockford_ureticisi_bicim_ve_entropi() -> None:
    with connection.cursor() as cur:
        cur.execute(
            "SELECT hr.crockford_suffix(8) FROM generate_series(1, 25)"
        )
        uretilenler = [satir[0] for satir in cur.fetchall()]
    for deger in uretilenler:
        assert re.fullmatch(r"[0-9A-HJKMNP-TV-Z]{8}", deger), deger
    assert len(set(uretilenler)) > 20, "üretici yeterli çeşitlilik vermiyor"

    with connection.cursor() as cur:
        cur.execute("SELECT hr.is_reserved_name('admin'), hr.is_reserved_name('acme')")
        assert cur.fetchone() == (True, False)


def test_demo_verisi_geri_dolduruldu() -> None:
    """Mevcut kiracılar/kullanıcılar için geri doldurma değerleri yerinde."""
    with connection.cursor() as cur:
        cur.execute("SELECT tenant_id, slug FROM hr.tenant_directory ORDER BY slug")
        for tenant_id, slug in cur.fetchall():
            with tenant_context(tenant_id):
                with connection.cursor() as cur:
                    cur.execute(
                        "SELECT public_id_prefix FROM hr.tenants WHERE id = %s", [str(tenant_id)]
                    )
                    prefix = cur.fetchone()[0]
                    cur.execute(
                        "SELECT count(*) FROM hr.users WHERE public_id IS NULL"
                    )
                    eksik = cur.fetchone()[0]
            assert PREFIX_DESENI.fullmatch(prefix), f"{slug}: {prefix}"
            assert eksik == 0, f"{slug}: public_id'siz kullanıcı var"
