"""`process_employee_terminations` — planlı çıkışları uygular (idempotent, P1-B).

Kullanım:

    python manage.py process_employee_terminations                 # tüm kiracılar
    python manage.py process_employee_terminations --tenant demo   # tek kiracı

Kural (OD41/OD43, HR_ROADMAP §28.2):
  * `termination_date <= bugün` ve `status ∈ {active, suspended}` olan
    çalışanlar **merkezi lifecycle servisi** ile `terminated` yapılır
    (erişim kesme + audit dahil).
  * Tekrar çalıştırma duplicate audit/token işlemi üretmez (status zaten
    `terminated` olanlar atlanır; geçiş servisi idempotenttir).
  * Production scheduler bağlamak P2/ops turuna bırakıldı (django-q2 mevcut;
    bu komut yalnızca elle/cron ile çalışır).
"""

from __future__ import annotations

import datetime as dt

from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.utils import timezone

from apps.organization.lifecycle import terminate_employee
from apps.organization.models import Employee, EmployeeStatus
from apps.tenancy.context import tenant_context


def _kiracilar(slug: str) -> list[tuple]:
    """Kiracı listesi — `tenant_directory` üzerinden (RLS dışı, yalnız-okuma)."""
    with connection.cursor() as cur:
        if slug:
            cur.execute("SELECT tenant_id, slug FROM hr.tenant_directory WHERE slug = %s", [slug])
            satirlar = cur.fetchall()
            if not satirlar:
                raise CommandError(f"Kiracı bulunamadı: {slug}")
            return satirlar
        cur.execute("SELECT tenant_id, slug FROM hr.tenant_directory ORDER BY slug")
        return cur.fetchall()


class Command(BaseCommand):
    help = "Vadesi gelen planlı çıkışları terminated yapar — idempotent."

    def add_arguments(self, parser) -> None:  # noqa: ANN001
        parser.add_argument(
            "--tenant", default="", help="Kiracı slug değeri (boşsa tüm kiracılar)."
        )
        parser.add_argument(
            "--as-of",
            default="",
            help="Referans tarihi (YYYY-AA-GG); boşsa bugün (test/geriye dönük senaryo).",
        )

    def handle(self, *args, **options) -> None:  # noqa: ANN002, ANN003
        bugun = (
            dt.date.fromisoformat(str(options["as_of"]))
            if options["as_of"]
            else timezone.localdate()
        )
        toplam = 0
        for tenant_id, slug in _kiracilar(str(options["tenant"] or "").strip()):
            with tenant_context(tenant_id):
                vadesi_gelen = list(
                    Employee.objects.filter(
                        deleted_at__isnull=True,
                        status__in=(EmployeeStatus.ACTIVE, EmployeeStatus.SUSPENDED),
                        termination_date__isnull=False,
                        termination_date__lte=bugun,
                    ).select_related("user")
                )
                for calisan in vadesi_gelen:
                    # Merkezi servis: geçiş + erişim kesme + audit (tek yol).
                    # aktör None → sistem işi (audit aktörsüz yazılır).
                    terminate_employee(
                        actor=None,
                        employee=calisan,
                        termination_date=calisan.termination_date,
                        termination_reason_code=calisan.termination_reason_code or "other",
                        reason="Planlı çıkış tarihi geldi (otomatik uygulama).",
                        termination_note=calisan.termination_note or "",
                        notice_end_date=calisan.notice_end_date,
                    )
                    toplam += 1
                if vadesi_gelen:
                    self.stdout.write(
                        f"{slug}: {len(vadesi_gelen)} çalışan terminated yapıldı."
                    )
        self.stdout.write(
            self.style.SUCCESS(f"PROCESS_OK terminated={toplam} as_of={bugun.isoformat()}")
        )
