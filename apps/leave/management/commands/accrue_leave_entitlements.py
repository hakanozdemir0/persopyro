"""`accrue_leave_entitlements` — dönem hakedişini yazar (idempotent, M14 §4).

Kullanım:

    python manage.py accrue_leave_entitlements --tenant demo --period-year 2026
    python manage.py accrue_leave_entitlements --tenant demo --period-year 2026 \
        --as-of 2026-06-30 --actor-email ik@ornek.test

Notlar:
  * Zamanlayıcı (scheduler) bu turda **kapsam dışıdır**; komut elle veya dış bir
    cron ile çalıştırılabilir. Tekrar çalıştırma **duplicate hakediş üretmez**
    (bakiye+dönem başına tek `accrual` kaydı; değer değişmediyse hiç yazılmaz).
  * `--actor-email` verilirse aktörün `leave.balance.manage` izni ve kapsamı
    doğrulanır; verilmezse işlem **sistem işi** olarak (aktörsüz) çalışır ve
    denetim kaydı aktörsüz yazılır.
  * Kiracı bağlamı komut tarafından kurulur (RLS gerekir).
"""

from __future__ import annotations

from datetime import date

from django.core.management.base import BaseCommand, CommandError
from django.db import connection

from apps.leave.services import accrue_leave_entitlements
from apps.leave.services.base import LeaveError
from apps.tenancy.context import tenant_context


class Command(BaseCommand):
    help = "İzin hakedişini (accrual) hesaplar ve yazar — idempotent."

    def add_arguments(self, parser) -> None:  # noqa: ANN001
        parser.add_argument("--tenant", required=True, help="Kiracı slug değeri.")
        parser.add_argument("--period-year", type=int, required=True, help="Hakediş dönemi (yıl).")
        parser.add_argument(
            "--as-of",
            default="",
            help="Hizmet süresi referans tarihi (YYYY-AA-GG); boşsa dönem sonu (31 Aralık).",
        )
        parser.add_argument(
            "--actor-email",
            default="",
            help="İşlemi yapan kullanıcı (izin kontrolü için); boşsa sistem işi.",
        )

    def handle(self, *args, **options) -> None:  # noqa: ANN002, ANN003
        slug = options["tenant"]
        period_year = int(options["period_year"])
        as_of = self._tarih(options["as_of"]) if options["as_of"] else None

        with connection.cursor() as cur:
            cur.execute("SELECT tenant_id FROM hr.tenant_directory WHERE slug = %s", [slug])
            satir = cur.fetchone()
        if satir is None:
            raise CommandError(f"Kiracı bulunamadı: {slug}")
        tenant_id = satir[0]

        aktor = None
        if options["actor_email"]:
            from django.contrib.auth import get_user_model  # noqa: PLC0415

            user_model = get_user_model()
            aktor = user_model.objects.filter(email=options["actor_email"]).first()
            if aktor is None:
                raise CommandError(f"Kullanıcı bulunamadı: {options['actor_email']}")

        try:
            with tenant_context(tenant_id):
                sonuclar = accrue_leave_entitlements(
                    actor=aktor,
                    tenant=self._tenant(tenant_id),
                    period_year=period_year,
                    as_of=as_of,
                )
        except LeaveError as hata:
            raise CommandError(str(hata)) from hata

        yazilan = sum(1 for s in sonuclar if s.get("durum") == "yazildi")
        degismeyen = sum(1 for s in sonuclar if s.get("durum") == "degisiklik_yok")
        duzeltme = sum(1 for s in sonuclar if s.get("durum") == "duzeltme")
        atlanan = sum(1 for s in sonuclar if s.get("durum") == "politika_yok")
        ozet = (
            f"Hakediş {period_year}: yazılan={yazilan} değişmeyen={degismeyen} "
            f"düzeltme={duzeltme} politika_yok={atlanan} (toplam iş: {len(sonuclar)})"
        )
        self.stdout.write(ozet)

    @staticmethod
    def _tarih(deger: str) -> date:
        try:
            return date.fromisoformat(deger)
        except ValueError as hata:
            raise CommandError(f"Geçersiz tarih: {deger} (YYYY-AA-GG beklenir)") from hata

    @staticmethod
    def _tenant(tenant_id):  # noqa: ANN001
        from apps.tenancy.models import Tenant  # noqa: PLC0415

        return Tenant.objects.get(pk=tenant_id)
