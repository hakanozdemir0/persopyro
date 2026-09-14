"""
P1-B — Çalışan yaşam döngüsü servisleri (OD41–OD43, OD59–OD61; HR_ROADMAP §28.2–28.3).

**Tek durum makinesi** (kapalı küme `EmployeeStatus` değişmez):

    active   → suspended, terminated
    suspended→ active, terminated
    terminated→ active (yalnız hata düzeltme/restore), archived
    archived → (yok — YASAK)

Kurallar:
  * Geçiş **yalnız bu modülün** fonksiyonlarıyla yapılır; view/model `save()` yolu yoktur.
  * İzin verilmeyen geçiş → `LifecycleStateError` (HTTP 409'a eşlenir) ve **audit'e yazılır**.
  * Aynı duruma tekrar geçiş → **no-op** (idempotent; audit yazılmaz).
  * Erişim kesme (suspend/terminate/archive): `users.status='inactive'`,
    token iptali (mevcut `revoke_tokens_for_inactive_user` servisi) ve
    `users.auth_version++`. Reactivate erişimi **yeniden açmaz**: kullanıcı
    yeniden `active` yapılır ama oturum/token butlanır; yeni giriş zorunludur.
  * `notice_period` DB durumu DEĞİLDİR (OD41): `ihbar_döneminde()` türetilir.
  * Concurrency: durum yazımı `select_for_update` + atomik işlem içindedir; aynı
    çalışanda eşzamanlı geçişlerde ikinci çağrı ya no-op olur ya 409 alır —
    çift kayıt/çift audit üretmez.
"""

from __future__ import annotations

import datetime as dt

from django.db import transaction
from django.utils import timezone

from apps.accounts.models import UserStatus
from apps.accounts.services import revoke_tokens_for_inactive_user
from apps.audit.services import log_action
from apps.organization.models import Employee, EmployeeStatus, TerminationReason
from apps.organization.services import OrganizationAuthorizationError, OrganizationError
from apps.rbac.services.permissions import user_has_permission
from apps.rbac.services.scope import invalidate_org_scope_cache


class LifecycleStateError(OrganizationError):
    """Geçersiz yaşam döngüsü geçişi (409'a eşlenir; sessiz durum değişimi yok)."""


class LifecycleValidationError(OrganizationError):
    """Zorunlu alan/parametre hatası (400'e eşlenir)."""


#: İzin verilen geçişler — **tek doğruluk kaynağı** (HR_ROADMAP §28.2 tablosu).
IZINLI_GECISLER: dict[str, frozenset[str]] = {
    EmployeeStatus.ACTIVE: frozenset({EmployeeStatus.SUSPENDED, EmployeeStatus.TERMINATED}),
    EmployeeStatus.SUSPENDED: frozenset({EmployeeStatus.ACTIVE, EmployeeStatus.TERMINATED}),
    EmployeeStatus.TERMINATED: frozenset({EmployeeStatus.ACTIVE, EmployeeStatus.ARCHIVED}),
    EmployeeStatus.ARCHIVED: frozenset(),
}


def _aktoru_dogrula(actor, izin_kodu: str) -> None:  # noqa: ANN001
    """Aktör aktif mi ve gerekli izne sahip mi? (fail-closed)"""
    if actor is None or not getattr(actor, "is_authenticated", False) or not actor.is_active:
        raise OrganizationAuthorizationError("İşlem için aktif bir kullanıcı gerekir.")
    if not user_has_permission(actor, izin_kodu):
        raise OrganizationAuthorizationError(f"Bu işlem için '{izin_kodu}' izni gerekir.")


def _hedef_dogrula(actor, employee: Employee) -> None:  # noqa: ANN001
    """Hedef çalışan aktörle aynı kiracıda ve kapsamda mı? (fail-closed; 404 iması)"""
    if employee is None or employee.deleted_at is not None:
        raise LifecycleValidationError("Çalışan bulunamadı.")
    if str(employee.tenant_id) != str(getattr(actor, "tenant_id", None)):
        # Kiracılar arası: varlık sızdırılmaz (bkz. `_kapsamdaki_calisan` 404 ilkesi).
        raise Employee.DoesNotExist


def _gerekce_dogrula(reason: str) -> str:
    temiz = (reason or "").strip()
    if not temiz:
        raise LifecycleValidationError("Gerekçe zorunludur.")
    return temiz[:200]


def _kullanici_kilitle(employee: Employee) -> None:  # noqa: ANN001
    """Bağlı kullanıcıyı pasifleştir, token'ları iptal et ve auth_version artır."""
    if employee.user_id is None:
        return
    user = employee.user
    degisen: list[str] = []
    if user.status != UserStatus.INACTIVE:
        user.status = UserStatus.INACTIVE
        degisen.append("status")
    user.auth_version += 1  # oturum butlanır (OD59)
    degisen.append("auth_version")
    user.save(update_fields=[*degisen, "updated_at"])
    revoke_tokens_for_inactive_user(user)


def _kullanici_aktif_yap(employee: Employee) -> None:  # noqa: ANN001
    """Bağlı kullanıcıyı yeniden active yapar; auth_version artar → eski oturum/token ölür."""
    if employee.user_id is None:
        return
    user = employee.user
    degisen: list[str] = []
    if user.status != UserStatus.ACTIVE:
        user.status = UserStatus.ACTIVE
        degisen.append("status")
    user.auth_version += 1  # yeniden giriş ZORUNLUDUR (eski oturum/token dirilmez)
    degisen.append("auth_version")
    user.save(update_fields=[*degisen, "updated_at"])
    revoke_tokens_for_inactive_user(user)


def _audit(
    *,
    actor,  # noqa: ANN001
    action: str,
    employee: Employee,  # noqa: ANN001
    changes: dict,
) -> None:
    log_action(
        tenant_id=employee.tenant_id,
        action=action,
        user=actor,
        table_name="employees",
        record_id=employee.pk,
        changes=changes,
    )


@transaction.atomic
def _gecis_yaz(
    employee: Employee,  # noqa: ANN001
    hedef: str,
) -> bool:
    """
    Satırı kilitler ve durumu yazar. Döner: yazıldı mı (False = no-op).

    `select_for_update` sayesinde eşzamanlı iki geçişte ikincisi kilit açılınca
    güncel durumu görür; çift geçiş/çift audit oluşmaz.

    terminated için tarih+neden zorunludur (DB CHECK): bu yüzden terminate yolu
    durum yazımından ÖNCE alanları örneğe yazar; burada tam örnek kaydedilir.
    """
    kilitli = Employee.objects.select_for_update().get(pk=employee.pk)
    if kilitli.status == hedef:
        employee.status = hedef  # çağıranın örneğini de hizala
        return False
    if hedef not in IZINLI_GECISLER.get(kilitli.status, frozenset()):
        raise LifecycleStateError(
            f"'{kilitli.status}' durumundan '{hedef}' durumuna geçiş izin verilmez."
        )
    kilitli.status = hedef
    kayitli_alanlar = [
        a
        for a in (
            "termination_date",
            "termination_reason_code",
            "termination_note",
            "notice_end_date",
        )
        if a in employee.__dict__ and getattr(employee, a) != getattr(kilitli, a, None)
    ]
    for alan in kayitli_alanlar:
        setattr(kilitli, alan, getattr(employee, alan))
    kilitli.save(
        update_fields=[
            "status",
            *(kayitli_alanlar if hedef == EmployeeStatus.TERMINATED else []),
            "updated_at",
        ]
    )
    employee.status = hedef
    return True


@transaction.atomic
def suspend_employee(*, actor, employee: Employee, reason: str) -> Employee:  # noqa: ANN001
    """
    `active → suspended` (OD42). Gerekçe zorunlu; erişim kesilir.

    İkinci kez çağrılırsa (zaten suspended) **no-op** döner — token/audit üretilmez.
    """
    _aktoru_dogrula(actor, "employee.suspend")
    _hedef_dogrula(actor, employee)
    gerekce = _gerekce_dogrula(reason)

    if employee.status == EmployeeStatus.SUSPENDED:
        return employee  # idempotent no-op

    if not _gecis_yaz(employee, EmployeeStatus.SUSPENDED):
        return employee
    _kullanici_kilitle(employee)
    _audit(
        actor=actor,
        action="employee.suspend",
        employee=employee,
        changes={"event": "suspend", "reason": gerekce},
    )
    invalidate_org_scope_cache(employee.tenant_id)
    return employee


@transaction.atomic
def restore_employee(*, actor, employee: Employee, reason: str) -> Employee:  # noqa: ANN001
    """
    `suspended → active` veya `terminated → active` (yalnız hata düzeltme).

    Her iki durumda da `auth_version` artar: **eski oturum/token asla dirilmez**;
    kullanıcı yeni giriş yapmak zorundadır (OD59). Terminated dönüşünde çıkış
    alanları temizlenir (geçmiş audit kayıtları korunur).
    """
    _aktoru_dogrula(actor, "employee.restore")
    _hedef_dogrula(actor, employee)
    gerekce = _gerekce_dogrula(reason)
    onceki = employee.status

    if onceki == EmployeeStatus.ACTIVE:
        return employee  # idempotent no-op

    if onceki == EmployeeStatus.ARCHIVED:
        raise LifecycleStateError("Arşivlenmiş çalışan yeniden aktifleştirilemez.")

    if not _gecis_yaz(employee, EmployeeStatus.ACTIVE):
        return employee

    if onceki == EmployeeStatus.TERMINATED:
        # Hata düzeltme/geri alma: çıkış alanları kapatılır (geçmiş audit korunur).
        temizlenen = {}
        if employee.termination_date is not None:
            temizlenen["termination_date"] = None
        if employee.termination_reason_code:
            temizlenen["termination_reason_code"] = ""
        if employee.termination_note:
            temizlenen["termination_note"] = ""
        if employee.notice_end_date is not None:
            temizlenen["notice_end_date"] = None
        if temizlenen:
            for alan, deger in temizlenen.items():
                setattr(employee, alan, deger)
            employee.save(
                update_fields=[*temizlenen.keys(), "updated_at"]
            )
        _audit(
            actor=actor,
            action="employee.restore",
            employee=employee,
            changes={
                "event": "restore_from_terminated",
                "reason": gerekce,
                "cleared": sorted(temizlenen.keys()),
                "severity": "high",
            },
        )
    else:
        _audit(
            actor=actor,
            action="employee.restore",
            employee=employee,
            changes={"event": "reactivate", "reason": gerekce},
        )

    _kullanici_aktif_yap(employee)
    invalidate_org_scope_cache(employee.tenant_id)
    return employee


@transaction.atomic
def terminate_employee(
    *,
    actor,  # noqa: ANN001
    employee: Employee,  # noqa: ANN001
    termination_date: dt.date,
    termination_reason_code: str,
    reason: str,
    termination_note: str = "",
    notice_end_date: dt.date | None = None,
) -> Employee:
    """
    İşten çıkış (OD43: tek adım — izin + tarih + neden + gerekçe).

    * `termination_date <= today` → `terminated` + erişim anında kesilir.
    * `termination_date > today` → durum korunur (active/suspended); satır
      **planlı çıkış** olur (`employee.termination.schedule` audit'i yazılır,
      `auth_version` artmaz — erişim henüz kesilmez).
    """
    _aktoru_dogrula(actor, "employee.terminate")
    _hedef_dogrula(actor, employee)
    gerekce = _gerekce_dogrula(reason)
    if employee.status == EmployeeStatus.TERMINATED:
        return employee  # idempotent no-op
    if employee.status == EmployeeStatus.ARCHIVED:
        raise LifecycleStateError("Arşivlenmiş çalışana işten çıkış verilemez.")
    if termination_reason_code not in {k for k, _ in TerminationReason.choices}:
        raise LifecycleValidationError("Geçersiz çıkış nedeni.")
    if termination_date is None:
        raise LifecycleValidationError("Çıkış tarihi zorunludur.")
    if notice_end_date is not None and notice_end_date < termination_date:
        raise LifecycleValidationError("İhbar bitişi, çıkış tarihinden önce olamaz.")

    bugun = timezone.localdate()
    if termination_date > bugun:
        # Planlı çıkış: durum değişmez; alanlar yazılır ve etiket türetilir.
        degisen: list[str] = []
        if employee.termination_date != termination_date:
            employee.termination_date = termination_date
            degisen.append("termination_date")
        if employee.termination_reason_code != termination_reason_code:
            employee.termination_reason_code = termination_reason_code
            degisen.append("termination_reason_code")
        if employee.termination_note != (termination_note or ""):
            employee.termination_note = termination_note or ""
            degisen.append("termination_note")
        if employee.notice_end_date != notice_end_date and notice_end_date is not None:
            employee.notice_end_date = notice_end_date
            degisen.append("notice_end_date")
        if degisen:
            employee.save(update_fields=[*degisen, "updated_at"])
        _audit(
            actor=actor,
            action="employee.termination.schedule",
            employee=employee,
            changes={
                "event": "termination_scheduled",
                "termination_date": termination_date.isoformat(),
                "termination_reason_code": termination_reason_code,
                "reason": gerekce,
            },
        )
        return employee

    # CHECK nedeniyle alanlar durum yazımından ÖNCE örneğe yazılır; _gecis_yaz
    # bunları kilitli satıra taşır (tek UPDATE: status + çıkış alanları).
    employee.termination_date = termination_date
    employee.termination_reason_code = termination_reason_code
    employee.termination_note = termination_note or ""
    if notice_end_date is not None:
        employee.notice_end_date = notice_end_date
    if not _gecis_yaz(employee, EmployeeStatus.TERMINATED):
        return employee
    _kullanici_kilitle(employee)
    _audit(
        actor=actor,
        action="employee.terminate",
        employee=employee,
        changes={
            "event": "terminate",
            "termination_date": termination_date.isoformat(),
            "termination_reason_code": termination_reason_code,
            "reason": gerekce,
        },
    )
    invalidate_org_scope_cache(employee.tenant_id)
    return employee


@transaction.atomic
def archive_employee(*, actor, employee: Employee, reason: str) -> Employee:  # noqa: ANN001
    """
    `terminated → archived` (OD6: `employee.delete` = arşivleme; fiziksel silme yok).

    Geçmiş/audit/rol kayıtları korunur; erişim kesin kapalıdır; `employee_no`
    ve `public_id` yeniden kullanılmaz (yeni kayıt yeni numara alır).
    """
    _aktoru_dogrula(actor, "employee.delete")
    _hedef_dogrula(actor, employee)
    gerekce = _gerekce_dogrula(reason)

    if employee.status == EmployeeStatus.ARCHIVED:
        return employee  # idempotent no-op
    if employee.status != EmployeeStatus.TERMINATED:
        raise LifecycleStateError("Yalnızca işten çıkmış (terminated) çalışan arşivlenir.")

    if not _gecis_yaz(employee, EmployeeStatus.ARCHIVED):
        return employee
    _kullanici_kilitle(employee)
    _audit(
        actor=actor,
        action="employee.archive",
        employee=employee,
        changes={"event": "archive", "reason": gerekce},
    )
    invalidate_org_scope_cache(employee.tenant_id)
    return employee


# --- Yönetici değişikliği (OD60: yeni sistem kurulmaz; mevcut tablo kullanılır) ---


@transaction.atomic
def change_manager(
    *,
    actor,  # noqa: ANN001
    employee: Employee,  # noqa: ANN001
    manager_employee: Employee,  # noqa: ANN001
    effective_from: dt.date | None = None,
    reason: str = "",
):
    """
    Birincil yönetici değişikliği — mevcut `hr.employee_manager` altyapısı (OD60).

    Yeni manager history sistemi **kurulmaz**; `set_primary_manager` (eski aktif
    ilişki kapanır → yenisi açılır; tek aktif birincil DB kısıtı korunur) çağrılır
    ve ayrıca `employee.manager.change` audit kodu yazılır (§23 kataloğu).
    """
    from apps.organization.services import set_primary_manager  # noqa: PLC0415

    _aktoru_dogrula(actor, "employee.update")
    _hedef_dogrula(actor, employee)
    gerekce = _gerekce_dogrula(reason)
    if manager_employee is None or manager_employee.deleted_at is not None:
        raise LifecycleValidationError("Hedef yönetici bulunamadı.")
    if str(manager_employee.tenant_id) != str(employee.tenant_id):
        raise LifecycleValidationError("Yönetici başka bir kiracıya ait olamaz.")
    if manager_employee.pk == employee.pk:
        raise LifecycleValidationError("Çalışan kendi yöneticisi olamaz.")

    eski = (
        employee.manager_relations.filter(
            relation_type="primary", effective_to__isnull=True, deleted_at__isnull=True
        )
        .order_by("-effective_from")
        .first()
    )
    iliski = set_primary_manager(
        actor=actor,
        employee=employee,
        manager_employee=manager_employee,
        effective_from=effective_from,
        reason=gerekce,
    )
    _audit(
        actor=actor,
        action="employee.manager.change",
        employee=employee,
        changes={
            "event": "manager_change",
            "old_manager_employee_id": str(eski.manager_employee_id) if eski else None,
            "new_manager_employee_id": str(manager_employee.pk),
            "effective_from": iliski.effective_from.isoformat(),
            "reason": gerekce,
        },
    )
    return iliski


# --- Türetilmiş değerler (OD41) ----------------------------------------------


def ihbar_döneminde(employee: Employee, *, bugun: dt.date | None = None) -> bool:  # noqa: ANN001
    """`status ∈ {active, suspended}` ∧ `termination_date > bugün` (OD41)."""
    if employee is None or employee.status not in (
        EmployeeStatus.ACTIVE,
        EmployeeStatus.SUSPENDED,
    ):
        return False
    if employee.termination_date is None:
        return False
    return employee.termination_date > (bugun or timezone.localdate())


def notice_etiketi(employee: Employee, *, bugun: dt.date | None = None) -> dict | None:  # noqa: ANN001
    """API/UI'ya sade türetilmiş değer (DB'ye yazılmaz)."""
    if not ihbar_döneminde(employee, bugun=bugun):
        return None
    return {
        "in_notice": True,
        "label": "Çıkış sürecinde",
        "termination_date": employee.termination_date.isoformat(),
        "termination_date_display": employee.termination_date.strftime("%d.%m.%Y"),
    }
