"""
Alan (field) şifreleme yardımcıları.

Kullanım: TOTP anahtarı gibi, veritabanında düz metin durmaması gereken sırlar.

Neden ek kütüphane değil: `cryptography` zaten bağımlılık zincirinde bulunuyor
(knox üzerinden gelir); açıkça bağımlılık listesine alındı ki sürüm değişimlerinde
sessizce kaybolmasın.

Anahtar `FIELD_ENCRYPTION_KEY` ortam değişkeninden okunur (secret koda yazılmaz).
Anahtar tanımlı değilse şifre çözme **başarısız olur**; bu bilinçli bir
fail-closed davranıştır: MFA anahtarı olmadan MFA'lı kullanıcı giriş yapamaz.
"""

import os

from cryptography.fernet import Fernet, InvalidToken
from django.core.exceptions import ImproperlyConfigured

_ENV_KEY = "FIELD_ENCRYPTION_KEY"


class SecretDecryptionError(RuntimeError):
    """Şifreli değer çözülemediğinde yükseltilir (anahtar değişmiş olabilir)."""


def _fernet() -> Fernet:
    key = os.environ.get(_ENV_KEY, "").strip()
    if not key:
        raise ImproperlyConfigured(
            f"{_ENV_KEY} tanımlı değil. Sırlar şifrelenmeden saklanmaz. "
            "Yerelde scripts/bootstrap.sh bu değeri üretir."
        )
    try:
        return Fernet(key.encode())
    except (ValueError, TypeError) as exc:
        raise ImproperlyConfigured(
            f"{_ENV_KEY} geçersiz. Fernet anahtarı 32 baytlık base64 olmalıdır "
            '(üretimi: python3 -c "import base64,os;print('
            'base64.urlsafe_b64encode(os.urandom(32)).decode())").'
        ) from exc


def encrypt_str(value: str) -> str:
    """Metni şifreler (Fernet, AES-128-CBC + HMAC)."""
    return _fernet().encrypt(value.encode("utf-8")).decode("ascii")


def decrypt_str(token: str) -> str:
    """Şifreli metni çözer."""
    try:
        return _fernet().decrypt(token.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise SecretDecryptionError(
            "Şifreli değer çözülemedi: anahtar değişmiş veya değer bozulmuş."
        ) from exc
