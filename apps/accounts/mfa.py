"""
Çok faktörlü doğrulama (TOTP).

İlke: MFA açık bir hesap için, MFA doğrulanmadan HİÇBİR giriş yolu tamamlanmaz
(oturum ve token dahil). Uygulama görünümleri ortak `dogrula_totp` yardımcısını
kullanır; böylece yeni bir giriş yolu eklenirse tek yerden geçer.

Anahtar, veritabanında ŞİFRELİ saklanır (`apps/common/crypto.py`). Anahtar
çözülemezse giriş reddedilir (fail-closed).
"""

import pyotp

from apps.accounts.models import User
from apps.common.crypto import SecretDecryptionError, decrypt_str


class MfaRequiredError(RuntimeError):
    """MFA açık ama doğrulama kodu verilmedi."""


class MfaInvalidError(RuntimeError):
    """MFA doğrulama kodu geçersiz."""


class MfaConfigError(RuntimeError):
    """MFA açık ama anahtar okunamıyor/eksik (fail-closed)."""


def mfa_gerekli(user: User) -> bool:
    return bool(user.multi_factor_enabled)


def dogrula_totp(user: User, kod: str | None) -> None:
    """
    MFA açıksa kodu doğrular; değilse hiçbir şey yapmaz.

    Hata durumları çağıran katman tarafından uygun yanıta çevrilir.
    """
    if not mfa_gerekli(user):
        return
    if not kod:
        raise MfaRequiredError("Çok faktörlü doğrulama kodu gerekli.")

    if not user.totp_secret:
        # MFA açık ama anahtar yok: güvenli tarafta kal, girişe izin verme.
        raise MfaConfigError("MFA açık ancak TOTP anahtarı tanımlı değil.")

    try:
        sir = decrypt_str(user.totp_secret)
    except SecretDecryptionError as exc:
        raise MfaConfigError("TOTP anahtarı çözülemedi.") from exc

    if not pyotp.TOTP(sir).verify(kod.strip(), valid_window=1):
        raise MfaInvalidError("Çok faktörlü doğrulama kodu geçersiz.")


def totp_anahtari_uret() -> tuple[str, str]:
    """
    Yeni TOTP sırrı üretir.

    Döner: (şifreli_saklanacak_değer, kullanıcıya_gösterilecek_plain_secret)
    Şifreli değer veritabanına yazılır; açık sır yalnızca kayıt anında gösterilir.
    """
    from apps.common.crypto import encrypt_str

    sir = pyotp.random_base32()
    return encrypt_str(sir), sir
