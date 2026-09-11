"""Şifrelenmiş alanlar (K3 sınıfı özel nitelikli kişisel veri)."""

from django.db import models

from apps.common.crypto import decrypt_str, encrypt_str


class EncryptedTextField(models.TextField):
    """
    Fernet ile şifrelenmiş metin alanı (`docs/DATABASE.md` K3 sınıfı).

    * Yazma: değer **her kayıtta yeniden şifrelenir** (deterministik değildir →
      şifreli metin üzerinden arama/eşitlik **yapılamaz**, bu bilinçlidir).
    * Okuma: çözülemezse `SecretDecryptionError` yükseltilir (fail-closed);
      bozuk/anahtarı değişmiş veri sessizce boş dönmez.
    * Anahtar `FIELD_ENCRYPTION_KEY` ortam değişkenindedir (secret koda yazılmaz).
    """

    description = "Fernet ile şifrelenmiş metin (K3)"

    def get_prep_value(self, value):  # noqa: ANN001, ANN201
        if value is None:
            return None
        return encrypt_str(str(value))

    def from_db_value(self, value, expression, connection):  # noqa: ANN001, ANN201, ARG002
        if value is None:
            return None
        return decrypt_str(value)


class EncryptedCharField(EncryptedTextField):
    """`EncryptedTextField`'in kısa metin varyantı (aynı kurallar)."""

    def __init__(self, *args, **kwargs):  # noqa: ANN002, ANN003
        # Şifreli metin düz metinden uzundur; sütun sınırı buna göre geniş tutulur.
        kwargs.setdefault("max_length", 500)
        super().__init__(*args, **kwargs)
