"""
API kimlik doğrulaması: kendi opak token modelimiz.

Neden kendi modelimiz (bkz. `ApiToken` belgesi): token doğrulaması kiracı bağlamı
KURULMADAN yapılır ve bağlamı token satırının kendisinden öğreniriz. Bu yüzden
token tablosu RLS dışıdır (kiracı verisi içermez, yalnızca özet + kiracı kimliği
tutar) ve kiracı bağlamı okunduktan hemen sonra kurulur.
"""

from django.utils import timezone
from rest_framework.authentication import BaseAuthentication, get_authorization_header
from rest_framework.exceptions import AuthenticationFailed

from apps.accounts.models import ApiToken
from apps.tenancy.context import tenant_context

KEYWORD = "Token"


def token_from_header(header_value: str):
    """
    `Authorization: Token <değer>` başlığından token kaydını çözer.

    Kiracı bağlamı GEREKTİRMEZ (token tablosu RLS dışıdır) ve kullanıcı satırını
    yüklemez. İstek hattı bu fonksiyonla kiracıyı öğrenir; asıl doğrulama
    (süre, kullanıcı durumu) `ApiTokenAuthentication` içinde yapılır.
    """
    parcalar = header_value.split()
    if len(parcalar) != 2 or parcalar[0].lower() != KEYWORD.lower():
        return None

    from apps.accounts.models import ApiToken

    try:
        kayit = ApiToken.objects.get(token_hash=ApiToken.hash_token(parcalar[1]))
    except ApiToken.DoesNotExist:
        return None
    return None if kayit.is_expired else kayit


class ApiTokenAuthentication(BaseAuthentication):
    """
    `Authorization: Token <değer>` başlığını doğrular.

    Süresi geçmiş, silinmiş (iptal edilmiş) veya pasif kullanıcıya ait token
    reddedilir. Başlık yoksa `None` döner (diğer kimlik doğrulama yolları denenir).
    """

    def authenticate(self, request):
        parcalar = get_authorization_header(request).split()
        if not parcalar or parcalar[0].lower() != KEYWORD.lower().encode():
            return None
        if len(parcalar) != 2:
            raise AuthenticationFailed("Geçersiz token başlığı.")
        try:
            token = parcalar[1].decode()
        except UnicodeError as exc:
            raise AuthenticationFailed("Geçersiz token kodlaması.") from exc

        return self.authenticate_credentials(token, request)

    def authenticate_header(self, request) -> str:  # noqa: ANN001, ARG002
        """
        401 dönebilmesi için gereklidir.

        DRF, kimlik doğrulama başlığı (WWW-Authenticate) sağlamayan sınıflarda
        403 döner; API istemcisi için doğru yanıt 401'dir.
        """
        return f'{KEYWORD} realm="api"'

    def authenticate_credentials(self, duz_metin: str, request=None):  # noqa: ANN001
        # İstek hattı token'ı zaten çözmüşse tekrar sorgu yapılmaz.
        kayit = getattr(request, "_api_token", None) if request is not None else None

        if kayit is None:
            # Bu sorgu RLS dışı bir tabloda çalışır: kiracı bağlamı henüz yok.
            # DİKKAT: `select_related("user")` KULLANILMAZ — users tablosu RLS'lidir
            # ve kiracı bağlamı kurulmadan JOIN o satırı gizler; token doğrulanamaz
            # hale gelir (bu tuzak knox ile de yaşandı, bkz. CHANGELOG 0.6.0).
            try:
                kayit = ApiToken.objects.get(token_hash=ApiToken.hash_token(duz_metin))
            except ApiToken.DoesNotExist as exc:
                raise AuthenticationFailed("Geçersiz veya iptal edilmiş token.") from exc

        if kayit.is_expired:
            raise AuthenticationFailed("Token süresi dolmuş.")

        # Bundan sonrası kiracıya bağlı veri: bağlam kurulur ve kullanıcı satırı
        # artık görünür olur (RLS).
        with tenant_context(kayit.tenant_id):
            kullanici = kayit.user
            if not kullanici.is_active or kullanici.deleted_at is not None:
                raise AuthenticationFailed("Kullanıcı aktif değil.")
            ApiToken.objects.filter(pk=kayit.pk).update(last_used_at=timezone.now())

        return kullanici, kayit
