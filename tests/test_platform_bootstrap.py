"""
M9 regresyon testleri — ilk platform SUPERADMIN bootstrap komutu.

Kapsam (istenen test listesi 17–19):
 17. bootstrap komutu güvenli biçimde superadmin oluşturur
 18. duplicate durumda **sessiz yükseltme yapmaz**
 19. ham parola/token loglanmaz

Ek kurallar: varsayılan parola yok, `--password` seçeneği yok (komut satırına sızmaz),
parola politikası dayatılır, platform şemasına erişilemezse fail-closed.
"""

import io
import sys

import pytest
from django.core.management import CommandError, call_command
from django.db import connections

from apps.platform import permissions as platform_yetki
from apps.platform.constants import (
    PLATFORM_ROLE_PERMISSIONS,
    PLATFORM_SUPERADMIN_EXTRA_PERMISSIONS,
)

pytestmark = pytest.mark.django_db(databases=["default", "platform"])
connection = connections["platform"]

KOMUT = "create_platform_superadmin"
PAROLA = "Kuvvetli-Parola-42!"
GEREKCE = "ilk kurulum icin elde calistirilan bootstrap"


def _calistir(
    email: str,
    *,
    parola: str | None = PAROLA,
    reason: str = GEREKCE,
    full_name: str = "Platform Operasyon",
    monkeypatch=None,
    stdin_var: bool = True,
) -> tuple[str, str]:
    """Komutu çalıştırır ve `(stdout, stderr)` döner. Hata yükseltirse çağıran yakalar."""
    cikti, hata = io.StringIO(), io.StringIO()
    if stdin_var:
        monkeypatch.setattr(sys, "stdin", io.StringIO(f"{parola}\n" if parola else ""))
    kwargs = {
        "email": email,
        "reason": reason,
        "full_name": full_name,
        "stdout": cikti,
        "stderr": hata,
    }
    if parola is not None:
        kwargs["password_stdin"] = True
    call_command(KOMUT, **kwargs)
    return cikti.getvalue(), hata.getvalue()


def _kullanici(email: str) -> tuple | None:
    with connection.cursor() as cur:
        cur.execute(
            "SELECT id, public_id, password, status, is_active FROM platform.users"
            " WHERE email = %s",
            [email],
        )
        return cur.fetchone()


# --- 17) güvenli oluşturma ---------------------------------------------------


def test_bootstrap_superadmin_olusturur(monkeypatch) -> None:  # noqa: ANN001
    email = "bootstrap@persopyro.test"
    cikti, hata = _calistir(email, monkeypatch=monkeypatch)

    satir = _kullanici(email)
    assert satir is not None, "platform kullanıcısı oluşmadı"
    kullanici_id, public_id, parola_hash, durum, aktif = satir

    # Parola HASH'lenerek saklanır; ham değer DB'de yok.
    assert parola_hash != PAROLA
    assert parola_hash.startswith("argon2$")
    assert PAROLA not in parola_hash
    assert (durum, aktif) == ("active", True)
    assert public_id.startswith("plt-")

    # Rol ataması: superadmin, aktörsüz (granted_by NULL → sistem operasyonu).
    assert platform_yetki.active_roles(kullanici_id) == ["superadmin"]
    assert platform_yetki.effective_permissions(kullanici_id) == set(
        PLATFORM_ROLE_PERMISSIONS["superadmin"]
    ) | set(PLATFORM_SUPERADMIN_EXTRA_PERMISSIONS)
    with connection.cursor() as cur:
        cur.execute(
            "SELECT granted_by_id, granted_at IS NOT NULL FROM platform.user_roles"
            " WHERE platform_user_id = %s",
            [str(kullanici_id)],
        )
        assert cur.fetchone() == (None, True)

    # Çıktı: kullanıcı bilgisi var, PAROLA YOK.
    assert email in cikti and public_id in cikti
    assert PAROLA not in cikti and PAROLA not in hata
    assert "loglanmad" in cikti


# --- 18) duplicate → sessiz yükseltme yok -----------------------------------


def test_bootstrap_duplicate_sessiz_yukseltme_yapmaz(monkeypatch) -> None:  # noqa: ANN001
    email = "tekrar@persopyro.test"
    _calistir(email, monkeypatch=monkeypatch)
    ilk = _kullanici(email)

    with pytest.raises(CommandError, match="ZATEN var"):
        _calistir(email, parola="Baska-Parola-99!", monkeypatch=monkeypatch)

    sonra = _kullanici(email)
    assert sonra[0] == ilk[0]  # aynı kullanıcı
    assert sonra[2] == ilk[2], "mevcut parola ezilmemeli"
    assert platform_yetki.active_roles(sonra[0]) == ["superadmin"]

    with connection.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM platform.user_roles WHERE platform_user_id = %s",
            [str(sonra[0])],
        )
        assert cur.fetchone()[0] == 1, "çift rol ataması üretilmemeli"


def test_bootstrap_mevcut_admin_i_sessizce_yukseltmez(monkeypatch) -> None:  # noqa: ANN001
    """E-posta platform_admin olan bir kullanıcıya aitse komut HATA verir ve rol değişmez."""
    email = "mevcut-admin@persopyro.test"
    with connection.cursor() as cur:
        cur.execute(
            "INSERT INTO platform.users (email, full_name, password)"
            " VALUES (%s, %s, %s) RETURNING id",
            [email, "Mevcut", "argon2$mevcut"],
        )
        kullanici_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO platform.user_roles (platform_user_id, role_id)"
            " SELECT %s, id FROM platform.roles WHERE code = 'platform_admin'",
            [str(kullanici_id)],
        )

    with pytest.raises(CommandError, match="ZATEN var"):
        _calistir(email, monkeypatch=monkeypatch)

    assert platform_yetki.active_roles(kullanici_id) == ["platform_admin"]
    assert platform_yetki.has_permission(kullanici_id, "platform.role.assign") is False


def test_bootstrap_soft_deleted_kullaniciyi_canlandirmaz(monkeypatch) -> None:  # noqa: ANN001
    email = "silinmis@persopyro.test"
    _calistir(email, monkeypatch=monkeypatch)
    with connection.cursor() as cur:
        cur.execute(
            "UPDATE platform.users SET deleted_at = now() WHERE email = %s", [email]
        )

    with pytest.raises(CommandError, match="soft-delete"):
        _calistir(email, monkeypatch=monkeypatch)

    satir = _kullanici(email)
    assert satir is not None


# --- 19) parola/log sızıntısı yok ------------------------------------------


def test_bootstrap_raw_parola_loglanmaz(monkeypatch) -> None:  # noqa: ANN001
    """Çıktı, uyarı ve hata akışlarında ham parola geçmez."""
    email = "log@persopyro.test"
    cikti, hata = _calistir(email, monkeypatch=monkeypatch)
    toplam = cikti + hata
    assert PAROLA not in toplam
    assert "password_stdin" not in toplam
    # Parola yalnızca hash olarak var; ham hâli ne DB'de ne çıktıda.
    assert _kullanici(email)[2] != PAROLA

    # Yeniden çalıştırma hatası da parolayı yansıtmaz.
    hata_metni = io.StringIO()
    with pytest.raises(CommandError) as exc:
        _calistir(email, parola="Yeni-Parola-123!", monkeypatch=monkeypatch)
    assert PAROLA not in str(exc.value)
    assert "Yeni-Parola-123!" not in str(exc.value)
    assert hata_metni.getvalue() == ""


def test_komutta_password_secenegi_yok() -> None:
    """`--password` bilinçli olarak yok: parola komut satırına/süreç listesine sızmaz."""
    from django.core.management import get_commands, load_command_class

    komutlar = get_commands()
    assert KOMUT in komutlar
    komut = load_command_class(komutlar[KOMUT], KOMUT)
    parser = komut.create_parser("manage.py", KOMUT)
    secenekler = {s for a in parser._actions for s in a.option_strings}
    assert "--password-stdin" in secenekler
    assert "--password" not in secenekler
    assert "--noinput" not in secenekler and "--no-input" not in secenekler


# --- ek kurallar -------------------------------------------------------------


def test_bootstrap_parola_politikasi_ve_gerekce(monkeypatch) -> None:  # noqa: ANN001
    with pytest.raises(CommandError, match="en az"):
        _calistir("kisa@persopyro.test", parola="kisa", monkeypatch=monkeypatch)

    with pytest.raises(CommandError, match="politikasına"):
        _calistir("zayif@persopyro.test", parola="1234567890", monkeypatch=monkeypatch)

    with pytest.raises(CommandError, match="gerekçe"):
        _calistir("gerekcesiz@persopyro.test", reason="kisa", monkeypatch=monkeypatch)

    # Hiçbiri oluşturulmamış olmalı (fail-closed: kısmi kayıt yok).
    with connection.cursor() as cur:
        cur.execute("SELECT count(*) FROM platform.users")
        assert cur.fetchone()[0] == 0
        cur.execute("SELECT count(*) FROM platform.user_roles")
        assert cur.fetchone()[0] == 0


def test_bootstrap_parola_kaynagi_yoksa_durur(monkeypatch) -> None:  # noqa: ANN001
    """Etkileşimli terminal yok ve `--password-stdin` verilmedi → açık hata."""
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    with pytest.raises(CommandError, match="Parola kaynağı yok"):
        call_command(
            KOMUT,
            email="kaynaksiz@persopyro.test",
            reason=GEREKCE,
            stdout=io.StringIO(),
            stderr=io.StringIO(),
        )


def test_bootstrap_gerekcesi_zorunlu() -> None:
    """`bootstrap_assign_role` gerekçesiz çağrılamaz (denetim izi)."""
    with connection.cursor() as cur:
        cur.execute(
            "INSERT INTO platform.users (email, full_name, password) VALUES"
            " ('gerekce@persopyro.test', 'X', 'argon2$x') RETURNING id"
        )
        kullanici_id = cur.fetchone()[0]

    with pytest.raises(platform_yetki.PlatformAuthorizationError, match="gerekçe"):
        platform_yetki.bootstrap_assign_role(
            target_user_id=kullanici_id, role_code="superadmin", reason="kisa"
        )

    # Bilinmeyen rol de reddedilir.
    with pytest.raises(platform_yetki.PlatformAuthorizationError, match="Bilinmeyen"):
        platform_yetki.bootstrap_assign_role(
            target_user_id=kullanici_id, role_code="root", reason=GEREKCE
        )
