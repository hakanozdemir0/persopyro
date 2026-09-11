"""Giriş/çıkış uç noktalarının girdi doğrulaması (serializer'lar)."""

from rest_framework import serializers


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
