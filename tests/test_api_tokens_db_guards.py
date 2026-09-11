"""
Token tablosunun VERİTABANI düzeyindeki son savunma hattı (OD26 / F8).

Uygulama katmanı atlanabilir (yönetim kabuğu, elle SQL, veri göçü betiği). Bu
yüzden kapsam doğrulaması, değişmezlik ve **tek yönlü iptal** veritabanında
tetikleyici/CHECK ile dayatılır. Bu testler ORM'i devre dışı bırakıp ham SQL
kullanır: korumanın yalnızca Python'da olmadığını kanıtlar.

Not: her test ifadesi savepoint içinde çalıştırılır; test sonunda dış işlem
pytest-django tarafından geri alınır (veritabanında iz kalmaz).
"""

import uuid

import pytest
from django.db import connection, transaction
from django.utils import timezone

from apps.accounts.models import ApiToken
from apps.tenancy.context import tenant_context

pytestmark = pytest.mark.django_db

INSERT_SQL = (
    "INSERT INTO hr.api_tokens (id, token_hash, label, token_kind, purpose, scopes,"
    " expires_at, created_at, tenant_id, user_id, last_used_at, revoked_at,"
    " revoked_reason) VALUES (%s, %s, '', 'interactive', %s, %s, %s, now(), %s, %s,"
    " NULL, NULL, NULL)"
)
AMAC = "veritabani koruma testi amaci"


def _ekle(scopes, *, tenant, user):  # noqa: ANN001
    """Geçerli/geçersiz kapsamla satır ekler (istisna yükseltebilir)."""
    with connection.cursor() as cur:
        cur.execute(
            INSERT_SQL,
            [
                str(uuid.uuid4()),
                uuid.uuid4().hex * 2,  # 64 karakterlik benzersiz özet
                AMAC,
                scopes,
                timezone.now(),
                str(tenant.id),
                str(user.id),
            ],
        )


def _satir_uret(token, *, tenant, user) -> None:  # noqa: ANN001
    """Düz metin token'ı olan bir satır üretir (ORM; tetikleyici geçer)."""
    with connection.cursor() as cur:
        cur.execute(
            INSERT_SQL,
            [
                str(uuid.uuid4()),
                ApiToken.hash_token(token),
                AMAC,
                ["employee.read"],
                timezone.now(),
                str(tenant.id),
                str(user.id),
            ],
        )


@pytest.mark.parametrize(
    "scopes",
    [
        ["yok.boyle.izin"],  # katalogda yok
        ["platform.tenants.read"],  # platform ad alanı yasak
        ["*"],  # joker yasak
        [],  # aktif satırda boş kapsam yasak
        ["employee.read", "employee.read"],  # tekrar yasak
    ],
)
def test_tetikleyici_kapsamlari_reddeder(scopes, tenant_a, user_a) -> None:  # noqa: ANN001
    """Aktif satırda kapsam kuralları veritabanında dayatılır."""
    with pytest.raises(Exception, match="api_tokens"):
        with transaction.atomic():
            _ekle(scopes, tenant=tenant_a, user=user_a)


def test_tetikleyici_iptal_edilmis_satirda_bos_kapsama_izin_verir(tenant_a, user_a) -> None:
    """
    Boş kapsam YALNIZCA iptal edilmiş satırda geçerlidir (F10 sentinel durumu).

    Kapsamsız geçmiş token'lar iptal edilirken `scopes='{}'` yazılır; bu durum
    meşrudur ve CHECK ihlali sayılmaz.
    """
    sql = (
        "INSERT INTO hr.api_tokens (id, token_hash, label, token_kind, purpose, scopes,"
        " expires_at, created_at, tenant_id, user_id, revoked_at, revoked_reason)"
        " VALUES (%s, %s, '', 'interactive', %s, %s, %s, now(), %s, %s, now(),"
        " 'policy_migration')"
    )
    with transaction.atomic():
        with connection.cursor() as cur:
            cur.execute(
                sql,
                [
                    str(uuid.uuid4()),
                    uuid.uuid4().hex * 2,
                    "policy_migration (kapsamsız dönem)",
                    [],
                    timezone.now(),
                    str(tenant_a.id),
                    str(user_a.id),
                ],
            )


def test_tetikleyici_degismez_alanlari_korur(tenant_a, user_a) -> None:  # noqa: ANN001
    """Kapsam/amaç/tür/süre değişmez; `last_used_at` değişebilir."""
    with tenant_context(tenant_a.id):
        duz_metin = "degismezlik-testi-token-degeri"
        _satir_uret(duz_metin, tenant=tenant_a, user=user_a)
        ozet = ApiToken.hash_token(duz_metin)

        for sql in (
            (
                "UPDATE hr.api_tokens SET scopes = ARRAY['leave.request.approve']"
                " WHERE token_hash = %s"
            ),
            "UPDATE hr.api_tokens SET purpose = 'baska bir amac metni' WHERE token_hash = %s",
            "UPDATE hr.api_tokens SET expires_at = now() + interval '1 year' WHERE token_hash = %s",
            "UPDATE hr.api_tokens SET token_kind = 'service' WHERE token_hash = %s",
        ):
            with pytest.raises(Exception, match="degistirilemez"):
                with transaction.atomic():
                    with connection.cursor() as cur:
                        cur.execute(sql, [ozet])

        # İzinli alan: son kullanım zamanı.
        with transaction.atomic():
            with connection.cursor() as cur:
                cur.execute(
                    "UPDATE hr.api_tokens SET last_used_at = now() WHERE token_hash = %s",
                    [ozet],
                )


def test_tetikleyici_iptalin_geri_alinmasini_engeller(tenant_a, user_a) -> None:  # noqa: ANN001
    """İptal tek yönlüdür: `revoked_at` tekrar NULL yapılamaz."""
    with tenant_context(tenant_a.id):
        duz_metin = "tek-yonlu-iptal-testi-degeri"
        _satir_uret(duz_metin, tenant=tenant_a, user=user_a)
        ozet = ApiToken.hash_token(duz_metin)

        with transaction.atomic():
            with connection.cursor() as cur:
                cur.execute(
                    "UPDATE hr.api_tokens SET revoked_at = now(), revoked_reason = 'logout'"
                    " WHERE token_hash = %s",
                    [ozet],
                )

        with pytest.raises(Exception, match="tek yonludur"):
            with transaction.atomic():
                with connection.cursor() as cur:
                    cur.execute(
                        "UPDATE hr.api_tokens SET revoked_at = NULL, revoked_reason = NULL"
                        " WHERE token_hash = %s",
                        [ozet],
                    )


def test_uygulama_rolu_token_yetkileri_daraltilmistir() -> None:  # noqa: ANN001
    """
    M30: uygulama rolü token satırı SİLEMEZ ve kapsamı değiştiremez.

    İptal işareti (defense in depth) yalnızca izinli üç kolonda yazılabilir.
    """
    from django.conf import settings

    rol = settings.APP_DB_ROLE
    with connection.cursor() as cur:
        cur.execute(
            "SELECT has_table_privilege(%s,'hr.api_tokens','UPDATE'),"
            " has_table_privilege(%s,'hr.api_tokens','DELETE'),"
            " has_column_privilege(%s,'hr.api_tokens','last_used_at','UPDATE'),"
            " has_column_privilege(%s,'hr.api_tokens','revoked_at','UPDATE'),"
            " has_column_privilege(%s,'hr.api_tokens','revoked_reason','UPDATE'),"
            " has_column_privilege(%s,'hr.api_tokens','scopes','UPDATE')",
            [rol] * 6,
        )
        tablo_update, tablo_delete, kolon_last, kolon_revoked, kolon_reason, kolon_scopes = (
            cur.fetchone()
        )

    assert tablo_update is False, "tablo düzeyinde UPDATE geri alınmalı (M30)"
    assert tablo_delete is False, "DELETE yetkisi geri alınmalı (iptal izi silinemez)"
    assert kolon_last is True
    assert kolon_revoked is True
    assert kolon_reason is True
    assert kolon_scopes is False, "kapsam sonradan değiştirilemez"
