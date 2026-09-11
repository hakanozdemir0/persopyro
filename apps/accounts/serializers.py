"""Giriş/çıkış uç noktalarının girdi doğrulaması (serializer'lar)."""

from rest_framework import serializers

from apps.accounts.models import TokenKind


class GirisSerializer(serializers.Serializer):
    """
    Giriş girdisi.

    `tenant` alanı istemciden gelir çünkü giriş anında oturum yoktur; ancak
    yalnızca kiracıyı ÇÖZMEK için kullanılır. Parola doğrulandıktan sonra kiracı
    oturuma sabitlenir ve sonraki isteklerde istemciden okunmaz.
    """

    tenant = serializers.CharField(
        max_length=190, help_text="Kiracı slug, alan adı veya kimliği"
    )
    email = serializers.EmailField()
    password = serializers.CharField(trim_whitespace=False, write_only=True)
    totp_code = serializers.CharField(
        required=False, allow_blank=True, max_length=10, write_only=True
    )


class TokenIstekSerializer(GirisSerializer):
    """
    Token üretim girdisi (OD26).

    `purpose` ve `scopes` ZORUNLUDUR: kapsamsız token üretilemez. Ayrıntılı
    doğrulama (katalog, alt küme, TTL tavanı) servis katmanındadır
    (`apps.accounts.services`); burada yalnızca biçim kontrolü yapılır.
    """

    purpose = serializers.CharField(
        max_length=500,
        trim_whitespace=True,
        help_text="Token'ın amacı/gerekçesi (en az 10 karakter).",
    )
    scopes = serializers.ListField(
        child=serializers.CharField(max_length=120),
        allow_empty=False,
        help_text="İzin kodları; sahibinin izinlerinin alt kümesi olmalıdır.",
    )
    kind = serializers.ChoiceField(
        choices=TokenKind.choices,
        required=False,
        default=TokenKind.INTERACTIVE,
        help_text="Token türü: interactive (insan) | service (entegrasyon).",
    )
    label = serializers.CharField(
        required=False, allow_blank=True, max_length=80, help_text="Cihaz/etiket"
    )
    ttl_hours = serializers.IntegerField(
        required=False,
        allow_null=True,
        min_value=1,
        help_text="İsteğe bağlı ömür (saat); tür başına üst sınır sunucuda dayatılır.",
    )
