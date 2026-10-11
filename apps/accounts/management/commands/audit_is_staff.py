"""
Denetim sorgusu: `is_staff` invariantı (OD19 — kapalı).

Kural: üretimde **hiçbir** kiracı kullanıcısında `is_staff = True` olmamalıdır ve
veritabanı kısıtı (`users_is_staff_disabled`) yerinde olmalıdır. Kural hem
uygulama katmanında (`User.save`) hem veritabanında dayatılır; bu komut ikisini
birden denetler (kısıt bir yedekten/elle müdahaleden sonra kaybolmuşsa yakalar).

RLS notu: `hr.users` kiracı izolasyonuna tabidir (FORCE), bu yüzden kiracı listesi
RLS'siz `hr.tenant_directory` üzerinden okunur ve her kiracı için sorgu kiracı
bağlamı içinde çalıştırılır.

Kullanım: `python manage.py audit_is_staff`
Çıkış kodu: ihlal veya eksik kısıt varsa **1** (cron/otomasyon için).
"""

from django.core.management.base import BaseCommand, CommandError
from django.db import connection

from apps.tenancy.context import tenant_context

KISIT_ADI = "users_is_staff_disabled"


class Command(BaseCommand):
    help = "is_staff invariantını denetler (OD19): hiçbir kullanıcıda True olmamalı."

    def handle(self, *args, **options) -> None:  # noqa: ANN002, ANN003, ARG002
        with connection.cursor() as cur:
            cur.execute("SELECT tenant_id, slug FROM hr.tenant_directory ORDER BY slug")
            kiraclar = cur.fetchall()
            cur.execute(
                "SELECT count(*) FROM pg_constraint"
                " WHERE conrelid = 'hr.users'::regclass AND conname = %s",
                [KISIT_ADI],
            )
            kisit_var = cur.fetchone()[0] == 1

        ihlaller: list[tuple[str, str]] = []
        aktif_toplam = 0
        for tenant_id, slug in kiraclar:
            with tenant_context(tenant_id):
                with connection.cursor() as cur:
                    cur.execute("SELECT count(*) FROM hr.users WHERE is_staff")
                    staff = cur.fetchone()[0]
                    cur.execute(
                        "SELECT count(*) FROM hr.users"
                        " WHERE deleted_at IS NULL AND status = 'active'"
                    )
                    aktif = cur.fetchone()[0]
                    cur.execute("SELECT email FROM hr.users WHERE is_staff ORDER BY email")
                    ihlaller.extend((slug, email) for (email,) in cur.fetchall())
            aktif_toplam += aktif
            self.stdout.write(f"  {slug}: aktif_kullanici={aktif} is_staff_true={staff}")

        self.stdout.write(
            f"kiracı={len(kiraclar)} aktif_kullanici={aktif_toplam} is_staff_true={len(ihlaller)}"
        )
        self.stdout.write(f"DB kısıtı {KISIT_ADI}: {'var' if kisit_var else 'YOK (uygulanmamış!)'}")

        if ihlaller:
            for slug, email in ihlaller:
                self.stderr.write(f"İHLAL: {slug} / {email} → is_staff=True")
            raise CommandError(
                f"is_staff invariantı ihlal edildi: {len(ihlaller)} kullanıcı (OD19)."
            )
        if not kisit_var:
            raise CommandError(
                f"Veritabanı kısıtı bulunamadı: {KISIT_ADI} (migration uygulanmamış olabilir)."
            )

        self.stdout.write(self.style.SUCCESS("is_staff invariantı sağlanıyor (OD19)."))
