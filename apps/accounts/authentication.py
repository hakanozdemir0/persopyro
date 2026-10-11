"""
API kimlik doğrulaması: kendi opak token modelimiz.

Neden kendi modelimiz (bkz. `ApiToken` belgesi): token doğrulaması kiracı bağlamı
KURULMADAN yapılır ve bağlamı token satırının kendisinden öğreniriz. Bu yüzden
token tablosu RLS dışıdır (kiracı verisi içermez, yalnızca özet + kiracı kimliği
tutar) ve kiracı bağlamı okunduktan hemen sonra kurulur.

OD26 (kapalı) sonrası reddetme sırası:
  1. token bulunamadı            → 401
  2. **iptal edilmiş** (`revoked_at`)
  3. süresi geçmiş (`expires_at`)
  4. **kiracı erişime kapalı** (askıda/iptal/silinmiş — F11)
  5. kullanıcı pasif/silinmiş (`is_active`)
Token'ın **kapsamı** burada değil, istek yetkilendirmesinde uygulanır
(`apps/rbac/services/permissions.py::authorized_permission_codes`):
`etkin izinler = kullanıcı izinleri ∩ token kapsamı`.

NOT (savunma katmanı — F11): normal istek akışında kiracı durumu **daha önce**,
istek hattında kontrol edilir ve kiracı erişime kapalıysa bağlam hiç kurulmaz
(`403 tenant_unavailable`, bkz. `apps/tenancy/middleware.py`). Buradaki kontrol
bu yüzden ikinci hattır ve istek hattı atlanırsa (ör. doğrudan çağrı, ileride yeni
bir taşıma katmanı) erişimi yine keser: `401`.
"""

from django.utils import timezone
from rest_framework.authentication import BaseAuthentication, get_authorization_header
from rest_framework.exceptions import AuthenticationFailed

from apps.accounts.models import ApiToken
from apps.tenancy.access import tenant_access_allowed
from apps.tenancy.context import tenant_context

KEYWORD = "Token"


def token_from_header(header_value: str):
    """
    `Authorization: Token <token>` başlığından token kaydını çözer.

    Kiracı bağlamı GEREKTİRMEZ (token tablosu RLS dışıdır) ve kullanıcı satırını
    yüklemez. İptal edilmiş veya süresi geçmiş token `None` döner (bağlam
    kurulmaz; erişim reddedilir).
    """
    parcalar = header_value.split()
    if len(parcalar) != 2 or parcalar[0].lower() != KEYWORD.lower():
        return None

    from apps.accounts.models import ApiToken

    try:
        kayit = ApiToken.objects.get(token_hash=ApiToken.hash_token(parcalar[1]))
    except ApiToken.DoesNotExist:
        return None
    return kayit if kayit.is_usable else None


class ApiTokenAuthentication(BaseAuthentication):
    """
    `Authorization: Token <token>` başlığını doğrular.

    İptal edilmiş, süresi geçmiş, kiracısı erişime kapalı veya pasif kullanıcıya
    ait token reddedilir. Başlık yoksa `None` döner (diğer yollar denenir).
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

        if kayit.is_revoked:
            raise AuthenticationFailed("Geçersiz veya iptal edilmiş token.")
        if kayit.is_expired:
            raise AuthenticationFailed("Token süresi dolmuş.")

        # F11: kiracı askıda/iptal/silinmişse token çalışmaz (iptal edilmemiş olsa bile).
        if not tenant_access_allowed(kayit.tenant_id):
            raise AuthenticationFailed("Kiracının erişimi kapalı.")

        # Bundan sonrası kiracıya bağlı veri: bağlam kurulur ve kullanıcı satırı
        # artık görünür olur (RLS).
        with tenant_context(kayit.tenant_id):
            kullanici = kayit.user
            if not kullanici.is_active or kullanici.deleted_at is not None:
                raise AuthenticationFailed("Kullanıcı aktif değil.")
            # P1-B (OD59): auth_version butlanması ikinci savunma hattı — token
            # iptal edilmemiş olsa bile sürüm uyuşmazsa erişim kesilir (401).
            if kayit.auth_version_snapshot is not None and int(kayit.auth_version_snapshot) != int(
                kullanici.auth_version
            ):
                raise AuthenticationFailed("Oturum geçersiz; yeni token alın.")
            ApiToken.objects.filter(pk=kayit.pk).update(last_used_at=timezone.now())

        return kullanici, kayit
