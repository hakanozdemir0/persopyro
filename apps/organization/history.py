"""
P1-B — Çalışan **iş geçmişi** servisi (OD60/§28.6; HR_ROADMAP §28.6).

Generic audit dump **gösterilmez**: durum geçişleri audit'ten, atama/koşul
geçmişi domain tablolarından okunur ve business-readable olaylara eşlenir.
PII **değeri** hiçbir olayda döndürülmez (yalnız K0 iş verisi + gerekçe/aktör).
"""

from __future__ import annotations

import datetime as dt

from apps.audit.models import AuditLog
from apps.organization.models import (
    Employee,
    EmployeeAssignment,
    EmployeeEmploymentTerms,
    EmployeeManager,
)

#: §23 audit kataloğundaki lifecycle olayları (sıralamada kullanılır).
DURUM_OLAYLARI = (
    "employee.suspend",
    "employee.restore",
    "employee.reactivate",
    "employee.termination.schedule",
    "employee.terminate",
    "employee.archive",
)


def _ad(olayan_tarih: dt.date) -> str:  # noqa: ANN001
    return olayan_tarih.strftime("%d.%m.%Y")


def employee_history(employee: Employee) -> list[dict]:  # noqa: ANN001
    """
    Çalışanın birleşik iş geçmişi (kronolojik, eski → yeni).

    Olay tipleri: `created`, `assignment_changed`, `employment_terms_changed`,
    `manager_changed`, `status_changed` (alt etiket: suspend/restore/terminate/
    archive/termination_scheduled/reactivate). Her olay: `date`, `date_display`,
    `type`, `label`, `detail` (PII yok).
    """
    olaylar: list[dict] = []

    olaylar.append(
        {
            "date": employee.hire_date.isoformat(),
            "date_display": _ad(employee.hire_date),
            "type": "created",
            "label": "Çalışan kaydı oluşturuldu",
            "detail": {"employee_no": employee.employee_no},
        }
    )

    # Atama geçmişi (departman/pozisyon) — domain tablosu (OD60).
    atamalar = (
        EmployeeAssignment.objects.filter(employee=employee, deleted_at__isnull=True)
        .select_related("department", "position")
        .order_by("effective_from")
    )
    onceki: EmployeeAssignment | None = None
    for atama in atamalar:
        if onceki is not None:
            olaylar.append(
                {
                    "date": atama.effective_from.isoformat(),
                    "date_display": _ad(atama.effective_from),
                    "type": "assignment_changed",
                    "label": "Görev yeri değişti",
                    "detail": {
                        "old_department": onceki.department.name,
                        "new_department": atama.department.name,
                        "old_position": onceki.position.name if onceki.position else None,
                        "new_position": atama.position.name if atama.position else None,
                        "reason": atama.reason,
                    },
                }
            )
        onceki = atama

    # İstihdam koşulları geçmişi (OD60).
    kosullar = list(
        EmployeeEmploymentTerms.objects.filter(
            employee=employee, deleted_at__isnull=True
        ).order_by("effective_from")
    )
    for i in range(1, len(kosullar)):
        eski, yeni = kosullar[i - 1], kosullar[i]
        olaylar.append(
            {
                "date": yeni.effective_from.isoformat(),
                "date_display": _ad(yeni.effective_from),
                "type": "employment_terms_changed",
                "label": "İstihdam koşulları güncellendi",
                "detail": {
                    "old_employment_type": eski.employment_type,
                    "new_employment_type": yeni.employment_type,
                    "old_working_model": eski.working_model,
                    "new_working_model": yeni.working_model,
                    "reason": yeni.reason,
                },
            }
        )

    # Yönetici geçmişi (OD60: mevcut tablo).
    iliskiler = (
        EmployeeManager.objects.filter(
            employee=employee, relation_type="primary", deleted_at__isnull=True
        )
        .select_related("manager_employee")
        .order_by("effective_from")
    )
    for i, iliski in enumerate(iliskiler):
        # İlk ilişki de bir olaydır: "yönetici atandı" (değişiklik olmasa bile
        # geçmiş sorgusu 'kimin yöneticisiydi?' sorusunu yanıtlamalı — OD60).
        etiket = "Yönetici değişti" if i > 0 else "Yönetici atandı"
        onceki_iliski = iliskiler[i - 1] if i > 0 else None
        olaylar.append(
            {
                "date": iliski.effective_from.isoformat(),
                "date_display": _ad(iliski.effective_from),
                "type": "manager_changed",
                "label": etiket,
                "detail": {
                    "old_manager_employee_no": (
                        onceki_iliski.manager_employee.employee_no
                        if onceki_iliski
                        else None
                    ),
                    "new_manager_employee_no": iliski.manager_employee.employee_no,
                },
            }
        )

    # Durum geçişleri — audit (append-only) üzerinden (OD60 §28.6).
    auditler = (
        AuditLog.objects.filter(
            record_id=employee.pk,
            action__in=DURUM_OLAYLARI + ("employee.status_change",),
        )
        .order_by("created_at")
        .values_list("action", "changes", "created_at")
    )
    etiketler = {
        "employee.suspend": "Askıya alındı",
        "employee.restore": "Yeniden aktifleştirildi",
        "employee.reactivate": "Yeniden aktifleştirildi",
        "employee.termination.schedule": "Çıkış planlandı",
        "employee.terminate": "İşten çıkarıldı",
        "employee.archive": "Arşivlendi",
    }
    for action, changes, created in auditler:
        if action == "employee.termination.schedule":
            tarih = (changes or {}).get("termination_date")
            olaylar.append(
                {
                    "date": created.date().isoformat(),
                    "date_display": _ad(created.date()),
                    "type": "status_changed",
                    "label": (
                        f"İşten çıkış tarihi {_ad(dt.date.fromisoformat(tarih))} olarak planlandı"
                        if tarih
                        else etiketler[action]
                    ),
                    "detail": {"reason": (changes or {}).get("reason")},
                }
            )
            continue
        if action in etiketler:
            olaylar.append(
                {
                    "date": created.date().isoformat(),
                    "date_display": _ad(created.date()),
                    "type": "status_changed",
                    "label": etiketler[action],
                    "detail": {"reason": (changes or {}).get("reason")},
                }
            )

    olaylar.sort(key=lambda o: o["date"])
    return olaylar
