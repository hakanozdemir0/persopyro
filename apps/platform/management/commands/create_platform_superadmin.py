"""
M9 (OD3 kapalı) — ilk platform SUPERADMIN bootstrap komutu.

    python manage.py create_platform_superadmin --email ops@ornek.com \
        --full-name "Operasyon" --reason "ilk kurulum" --password-stdin

Kurallar (docs/DATABASE.md M9 + docs/AUTHORIZATION.md §4/§6):

  * **Django Admin kullanılmaz** (OD19); bu komut açık bir yönetim operasyonudur.
  * **Gerçek kullanıcı migration içine seed edilmez**: bu komut elle/otomasyonla,
    açıkça çalıştırılır.
  * **Varsayılan parola YOK.** Parola yalnızca `--password-stdin` (boru) veya
    etkileşimli `getpass` istemiyle alınır. `--password` seçeneği **bilinçli olarak
    yoktur** (komut satırı geçmişine/süreç listesine sızmasın).
  * Parola **hash'lenerek** saklanır (Django `PASSWORD_HASHERS`; Argon2id birincil).
  * **Ham parola hiçbir yere yazılmaz/loglanmaz** (çıktıya da basılmaz).
  * **Sessiz yükseltme yok:** e-posta zaten varsa (aktif, pasif veya silinmiş)
    komut **açık hata** verir; mevcut kullanıcıya superadmin rolü sessizce eklenmez.
  * Superadmin ataması `granted_by_id = NULL` ile kaydedilir (henüz atayan aktör
    yoktur; bootstrap sistem operasyonudur) ve **gerekçe zorunludur** (≥10 karakter).

Bağlantı rolü: komut `platform` şemasına erişebilen bir rolle çalıştırılmalıdır
(`persopyro_platform` veya migration sahibi). Kiracı runtime rolü (`persopyro_app`)
platform şemasında yetkisizdir → komut **fail-closed** hata verir (istenen davranış).
"""

import sys

from django.contrib.auth.hashers import make_password
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError, connection
from django.utils import timezone

from apps.platform import permissions as platform_permissions

SUPERADMIN_ROLE = "superadmin"
PAROLA_MIN = 10


class _ParolaKullanici:
    """Parola doğrulayıcılarına kullanıcı bağlamı verir (benzerlik kontrolü için)."""

    def __init__(self, email: str, full_name: str) -> None:
        self.email = email
        self.username = email
        self.full_name = full_name
        parcalar = [p for p in (full_name or "").split() if p]
        self.first_name = parcalar[0] if parcalar else ""
        self.last_name = " ".join(parcalar[1:]) if len(parcalar) > 1 else ""


class Command(BaseCommand):
    help = (
        "İlk platform SUPERADMIN kullanıcısını güvenli biçimde oluşturur (OD3/M9). "
        "Mevcut kullanıcıyı YÜKSELTMEZ; parolayı stdin'den veya etkileşimli sorar."
    )

    def add_arguments(self, parser) -> None:  # noqa: ANN001
        parser.add_argument("--email", required=True, help="Platform kullanıcısı e-postası.")
        parser.add_argument("--full-name", default="", help="Ad soyad (opsiyonel).")
        parser.add_argument(
            "--reason",
            default="",
            help="Bootstrap gerekçesi (zorunlu, ≥10 karakter; denetim izi için).",
        )
        parser.add_argument(
            "--password-stdin",
            action="store_true",
            help="Parolayı stdin'den (tek satır) oku. Etkileşimli terminal yoksa zorunludur.",
        )

    # --- yardımcılar --------------------------------------------------------

    def _platform_semasi_kontrol(self) -> None:
        try:
            with connection.cursor() as cur:
                cur.execute("SELECT count(*) FROM platform.users")
        except DatabaseError as exc:
            raise CommandError(
                "platform şemasına erişilemiyor. Bu komut platform yetkili bir rolle "
                "çalıştırılmalıdır (persopyro_platform veya migration sahibi). "
                f"Ayrıntı: {exc.__class__.__name__}"
            ) from exc

    def _parolayi_oku(self, options) -> str:  # noqa: ANN001
        if options["password_stdin"]:
            parola = sys.stdin.readline().rstrip("\n")
            if not parola:
                raise CommandError("--password-stdin boş parola okundu; işlem durduruldu.")
            return parola

        if not sys.stdin.isatty():
            raise CommandError(
                "Parola kaynağı yok: etkileşimli terminal olmadan çalışıyorsunuz. "
                "`--password-stdin` ile parolayı borudan verin."
            )

        import getpass  # yerel import: yalnızca etkileşimli yolda gerekir

        ilk = getpass.getpass("Platform SUPERADMIN parolası: ")
        tekrar = getpass.getpass("Parolayı tekrar girin: ")
        if ilk != tekrar:
            raise CommandError("Parolalar eşleşmiyor; işlem durduruldu.")
        return ilk

    def _parolayi_dogrula(self, parola: str, email: str, full_name: str) -> None:
        if len(parola) < PAROLA_MIN:
            raise CommandError(f"Parola en az {PAROLA_MIN} karakter olmalıdır.")
        try:
            validate_password(parola, user=_ParolaKullanici(email, full_name))
        except DjangoValidationError as exc:
            raise CommandError("Parola politikasına uymuyor: " + "; ".join(exc.messages)) from exc

    # --- akış ---------------------------------------------------------------

    def handle(self, *args, **options) -> None:  # noqa: ANN002, ANN003, ARG002
        email = (options["email"] or "").strip().lower()
        full_name = (options["full_name"] or "").strip()
        reason = (options["reason"] or "").strip()

        if "@" not in email:
            raise CommandError("Geçerli bir e-posta verin (--email).")
        if len(reason) < 10:
            raise CommandError(
                "Bootstrap gerekçesi zorunludur (≥10 karakter) — denetim izi için (--reason)."
            )

        self._platform_semasi_kontrol()

        # Sessiz yükseltme yok: kayıt varsa (her durumda) açık hata.
        with connection.cursor() as cur:
            cur.execute(
                "SELECT id, status, is_active, deleted_at FROM platform.users WHERE email = %s",
                [email],
            )
            mevcut = cur.fetchone()
        if mevcut is not None:
            _, durum, aktif, deleted_at = mevcut
            ayrinti = (
                "soft-delete edilmiş" if deleted_at is not None else f"durum={durum}, aktif={aktif}"
            )
            raise CommandError(
                f"Bu e-posta ile platform kullanıcısı ZATEN var ({ayrinti}). Bu komut mevcut "
                "kullanıcıyı yükseltmez (sessiz yükseltme yasak). Rol ataması gerekiyorsa "
                "yetkili bir superadmin `assign_role` akışını kullanmalıdır."
            )

        parola = self._parolayi_oku(options)
        self._parolayi_dogrula(parola, email, full_name)

        with connection.cursor() as cur:
            cur.execute(
                """
                INSERT INTO platform.users
                    (email, full_name, password, status, is_active, password_changed_at)
                VALUES (%s, %s, %s, 'active', true, %s)
                RETURNING id, public_id
                """,
                [email, full_name, make_password(parola), timezone.now()],
            )
            kullanici_id, public_id = cur.fetchone()

        try:
            platform_permissions.bootstrap_assign_role(
                target_user_id=kullanici_id,
                role_code=SUPERADMIN_ROLE,
                reason=reason,
            )
        except platform_permissions.PlatformAuthorizationError as exc:
            # Rol atanamazsa kullanıcı da oluşmamalı: transaction geri alınır.
            raise CommandError(f"Superadmin rolü atanamadı: {exc}") from exc

        self.stdout.write(
            self.style.SUCCESS(
                f"Platform SUPERADMIN oluşturuldu: {email} (public_id={public_id})."
            )
        )
        self.stdout.write(f"  rol: {SUPERADMIN_ROLE} · gerekçe: {reason}")
        self.stdout.write(
            "  parola: hash'lenerek saklandı (Argon2id) — ham parola loglanmadı/gösterilmedi."
        )
