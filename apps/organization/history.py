"""
P1-B — Çalışan **iş geçmişi** servisi (OD60/§28.6; HR_ROADMAP §28.6).

Generic audit dump **gösterilmez**: durum geçişleri audit'ten, atama/koşul
geçmişi domain tablolarından okunur ve business-readable olaylara eşlenir.
PII **değeri** hiçbir olayda döndürülmez (yalnız K0 iş verisi + gerekçe/aktör).
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal, InvalidOperation

from django.db.models import Q
from django.utils import timezone

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


def _aktor_etiketi(user) -> str:  # noqa: ANN001
    if user is None:
        return "Sistem"
    ad = f"{user.first_name} {user.last_name}".strip()
    return ad or user.email


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
        created = timezone.localtime(created)
        if action == "employee.termination.schedule":
            tarih = (changes or {}).get("termination_date")
            olaylar.append(
                {
                    "date": created.date().isoformat(),
                    "date_display": _ad(created.date()),
                    "time_display": created.strftime("%H:%M"),
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
                    "time_display": created.strftime("%H:%M"),
                    "type": "status_changed",
                    "label": etiketler[action],
                    "detail": {"reason": (changes or {}).get("reason")},
                }
            )

    # Çalışan detayında yapılan diğer işlemler: kişisel veri, özel alan ve
    # parola değişiklikleri. Audit satırı çalışan kaydıyla ilişkilidir; parola
    # işlemi kullanıcı kaydına yazıldığı için `changes.employee_id` ile bulunur.
    genel_etiketler = {
        "employee.personal_data.create": "Kişisel bilgiler oluşturuldu",
        "employee.personal_data.update": "Kişisel bilgiler güncellendi",
        "employee.personal_data.erase": "Kişisel veriler silindi ve kayıt arşivlendi",
        "user.password.reset": "Parola güncellendi",
        "user.email.update": "Kurumsal e-posta güncellendi",
        "employee.manager.change": "Yönetici bilgisi güncellendi",
        "employee.primary_manager_set": "Yönetici bilgisi güncellendi",
        "employee.manager.close": "Yönetici bilgisi güncellendi",
        "employee.department_change": "Birim bilgisi güncellendi",
        "employee.assignment.change": "Görev bilgileri güncellendi",
        "employee.terms.change": "Çalışma koşulları güncellendi",
        "role.assign": "Rol atandı",
        "role.revoke": "Rol kaldırıldı",
        "role.restore": "Rol geri alındı",
    }
    genel_olaylar = AuditLog.objects.filter(
        Q(record_id=employee.pk)
        | Q(
            record_id=employee.user_id,
            action__in=("user.password.reset", "user.email.update"),
        )
        | Q(changes__employee_id=str(employee.pk))
        | Q(changes__user_id=str(employee.user_id))
        | Q(changes__target_user_id=str(employee.user_id))
        if employee.user_id
        else Q(record_id=employee.pk),
        tenant_id=employee.tenant_id,
    ).exclude(action__in=DURUM_OLAYLARI + ("employee.status_change",)).select_related("user").order_by("created_at")
    for kayit in genel_olaylar:
        created_at = timezone.localtime(kayit.created_at)
        etiket = genel_etiketler.get(kayit.action)
        if not etiket:
            continue
        degisimler = (kayit.changes or {}).get("alan_degisiklikleri") or {}
        aktor = _aktor_etiketi(kayit.user)
        detay = {"Yapan": aktor}
        if degisimler and kayit.action != "user.password.reset":
            detay["Değişiklikler"] = degisimler
        olaylar.append(
            {
                "date": created_at.date().isoformat(),
                "date_display": _ad(created_at.date()),
                "time_display": created_at.strftime("%H:%M"),
                "type": "employee_update",
                "label": etiket,
                "summary": f"Yapan: {aktor}",
                "detail": detay,
            }
        )

    # İzin / fazla mesai kararları (mevcut verilerden; uydurma yok) — Timeline.
    from apps.overtime.models import OvertimeRequest, OvertimeStatus  # noqa: PLC0415

    # İzin talebi olayları (oluşturuldu/onaylandı/reddedildi/iptal/kullanıldı) ve
    # izin hakkı-bakiye olayları (hakediş/düzeltme) mevcut kayıtlardan üretilir.
    olaylar.extend(_izin_talep_olaylari(employee))
    olaylar.extend(_izin_bakiye_olaylari(employee))

    for fm in OvertimeRequest.objects.filter(
        employee=employee, deleted_at__isnull=True, decided_at__isnull=False
    ).order_by("decided_at"):
        if fm.status == OvertimeStatus.APPROVED:
            etiket = "Fazla mesai onaylandı"
        elif fm.status == OvertimeStatus.REJECTED:
            etiket = "Fazla mesai reddedildi"
        else:
            continue
        decided_at = timezone.localtime(fm.decided_at)
        olaylar.append(
            {
                "date": decided_at.date().isoformat(),
                "date_display": _ad(decided_at.date()),
                "type": "overtime_decision",
                "label": etiket,
                "detail": {
                    "work_date": fm.work_date.isoformat(),
                    "total_minutes": fm.total_minutes,
                },
            }
        )

    olaylar.sort(key=lambda o: o["date"])
    return olaylar


# --- İzin olayları (mevcut kayıtlardan; uydurma veri yok) --------------------


def _gun_metni(deger) -> str:  # noqa: ANN001
    """Gün değerini insan-okunur metne çevirir (14.0 → `14`, 1.5 → `1,5`)."""
    try:
        sayi = Decimal(str(deger))
    except (InvalidOperation, ValueError):
        return str(deger)
    metin = format(sayi.normalize(), "f")
    return metin.replace(".", ",")


def _isaretli_gun(deger) -> str:  # noqa: ANN001
    """Değişim miktarını işaretli gösterir (`+2`, `-1,5`)."""
    metin = _gun_metni(deger)
    return metin if metin.startswith("-") else f"+{metin}"


def _aralik_metni(baslangic: dt.date, bitis: dt.date, gun) -> str:  # noqa: ANN001
    """`10.10.2026 – 15.10.2026 · 5 gün` biçiminde okunur aralık metni."""
    return f"{_ad(baslangic)} – {_ad(bitis)} · {_gun_metni(gun)} gün"


def _aktor_numaralari(tenant_id, user_ids: set) -> dict:  # noqa: ANN001
    """
    Kullanıcı → **çalışan numarası** eşlemesi (olay aktörü).

    Ad/e-posta gibi K1 kişisel veriler timeline detayına **yazılmaz**; yalnız iş
    kimliği (çalışan no) gösterilir. Kiracı filtresi açıkça uygulanır.
    """
    if not user_ids:
        return {}
    return {
        kayit.user_id: kayit.employee_no
        for kayit in Employee.objects.filter(
            tenant_id=tenant_id, user_id__in=list(user_ids), deleted_at__isnull=True
        )
    }


def _aktor_adlari(tenant_id, user_ids: set) -> dict:  # noqa: ANN001
    """Karar veren kullanıcıların görünen adını çalışan numarasıyla birlikte döndürür."""
    if not user_ids:
        return {}
    sonuc = {}
    for kayit in Employee.objects.filter(
        tenant_id=tenant_id, user_id__in=list(user_ids), deleted_at__isnull=True
    ).select_related("user"):
        ad = f"{kayit.user.first_name} {kayit.user.last_name}".strip() if kayit.user else ""
        sonuc[kayit.user_id] = f"{ad} ({kayit.employee_no})" if ad else kayit.employee_no
    # Kararı şirket yöneticisi gibi çalışan kaydı olmayan bir kullanıcı verdiyse
    # yine de gerçek kullanıcı adı/e-postası timeline'da gösterilir.
    from django.contrib.auth import get_user_model  # noqa: PLC0415

    user_modelu = get_user_model()
    for kullanici in user_modelu.objects.filter(
        tenant_id=tenant_id, pk__in=list(user_ids - set(sonuc))
    ):
        ad = f"{kullanici.first_name} {kullanici.last_name}".strip()
        sonuc[kullanici.pk] = ad or kullanici.email
    return sonuc


def _izin_talep_olaylari(employee: Employee) -> list[dict]:  # noqa: ANN001
    """
    İzin talebi olayları — yalnız **mevcut** alanlardan.

    * oluşturuldu → `created_at`
    * onaylandı / reddedildi → `decided_at` (+ onay adımındaki red gerekçesi)
    * iptal edildi → `canceled_at` + `cancel_reason`
    * kullanıldı → `taken` durumu; zaman/aktör **yalnız** denetim kaydında olduğu
      için `AuditLog` (`leave.request.taken`) kullanılır — olmayan olay uydurulmaz.
    """
    from apps.leave.models import (  # noqa: PLC0415
        ApprovalStep,
        ApprovalStepStatus,
        LeaveDecisionSnapshot,
        LeaveRequest,
        LeaveRequestStatus,
    )

    talepler = list(
        LeaveRequest.objects.filter(employee=employee, deleted_at__isnull=True)
        .select_related("leave_type", "decided_by")
        .order_by("created_at")
    )
    if not talepler:
        return []

    kimlikler = [talep.pk for talep in talepler]

    # Reddin gerekçesi talep satırında değil **onay adımında** tutulur.
    ret_gerekceleri: dict = {}
    for adim in ApprovalStep.objects.filter(
        subject_id__in=kimlikler,
        status=ApprovalStepStatus.REJECTED,
        deleted_at__isnull=True,
    ).order_by("step_no"):
        if adim.decision_reason and adim.subject_id not in ret_gerekceleri:
            ret_gerekceleri[adim.subject_id] = adim.decision_reason

    # İptal / "kullanıldı" işleminin zamanı ve aktörü denetim kaydındadır.
    denetim: dict = {}
    for kayit in AuditLog.objects.filter(
        table_name="leave_requests",
        record_id__in=kimlikler,
        action__in=("leave.request.cancel", "leave.request.taken"),
    ).order_by("created_at"):
        denetim.setdefault((kayit.action, kayit.record_id), kayit)

    aktorler = _aktor_numaralari(
        employee.tenant_id,
        {talep.decided_by_id for talep in talepler if talep.decided_by_id}
        | {kayit.user_id for kayit in denetim.values() if kayit.user_id},
    )
    aktor_adlari = _aktor_adlari(
        employee.tenant_id,
        {talep.decided_by_id for talep in talepler if talep.decided_by_id}
        | {kayit.user_id for kayit in denetim.values() if kayit.user_id},
    )

    # Karar anı bakiyesi (immutable snapshot) varsa karar olayına güvenli özet
    # eklenir; yoksa **hiçbir değer** yazılmaz (bugünkü bakiye geçmiş gibi sunulmaz).
    snapshotlar = {
        kayit.leave_request_id: kayit
        for kayit in LeaveDecisionSnapshot.objects.filter(leave_request_id__in=kimlikler)
    }

    olaylar: list[dict] = []
    for talep in talepler:
        created_at = timezone.localtime(talep.created_at)
        tur = talep.leave_type.name
        aralik = _aralik_metni(talep.start_date, talep.end_date, talep.days_requested)
        # Takvim aralığı K2 sınıfı değildir (maskeleme gerektirmez); gerekçe metni
        # çalışan/işleyen tarafından yazılmış iş verisidir, PII değeri dönmez.
        ortak = {"Tür": tur, "Dönem": aralik}

        olaylar.append(
            {
                "date": created_at.date().isoformat(),
                "date_display": _ad(created_at.date()),
                "type": "leave_created",
                "label": f"{tur} talebi oluşturuldu",
                "summary": aralik,
                "detail": dict(ortak),
            }
        )

        if talep.decided_at is not None and talep.status in (
            LeaveRequestStatus.APPROVED,
            LeaveRequestStatus.TAKEN,
            LeaveRequestStatus.REJECTED,
        ):
            decided_at = timezone.localtime(talep.decided_at)
            detay = dict(ortak)
            ozet = aralik
            if talep.status == LeaveRequestStatus.REJECTED:
                etiket = f"{tur} reddedildi"
                gerekce = ret_gerekceleri.get(talep.pk)
                if gerekce:
                    detay["Gerekçe"] = gerekce
                    ozet = f"Gerekçe: {gerekce}"
            else:
                etiket = f"{tur} onaylandı"
            yetkili = aktorler.get(talep.decided_by_id)
            if yetkili:
                detay["Yetkili"] = yetkili
                ozet = f"{ozet} · Yetkili: {yetkili}"
            karar_veren = aktor_adlari.get(talep.decided_by_id)
            if karar_veren:
                karar_etiketi = "Reddeden" if talep.status == LeaveRequestStatus.REJECTED else "Onaylayan"
                detay[karar_etiketi] = karar_veren
                ozet = f"{ozet} · {karar_etiketi}: {karar_veren}"
            olaylar.append(
                {
                    "date": decided_at.date().isoformat(),
                    "date_display": _ad(decided_at.date()),
                    "type": "leave_decision",
                    "label": etiket,
                    "summary": ozet,
                    "detail": detay,
                }
            )
            # §6: karar anı snapshot'ı varsa güvenli özet ("Karar sonrası kalan").
            # Yetki kapısı API katmanındadır (leave.balance.read yoksa alan çıkarılır).
            snapshot = snapshotlar.get(talep.pk)
            if snapshot is not None and snapshot.available_days is not None:
                olaylar[-1]["karar_sonrasi_kalan"] = (
                    f"{_gun_metni(snapshot.available_days)} gün"
                )
                olaylar[-1]["karar_sonrasi_kalan_snapshot_at"] = (
                    timezone.localtime(snapshot.snapshot_at).isoformat()
                )

        if talep.canceled_at is not None:
            kayit = denetim.get(("leave.request.cancel", talep.pk))
            tarih = kayit.created_at if kayit is not None else talep.canceled_at
            tarih = timezone.localtime(tarih)
            detay = dict(ortak)
            ozet = aralik
            if talep.cancel_reason:
                detay["Gerekçe"] = talep.cancel_reason
                ozet = f"Gerekçe: {talep.cancel_reason}"
            iptal_eden = aktorler.get(kayit.user_id) if kayit is not None else None
            if iptal_eden:
                detay["İptal eden"] = iptal_eden
            olaylar.append(
                {
                    "date": tarih.date().isoformat(),
                    "date_display": _ad(tarih.date()),
                    "type": "leave_canceled",
                    "label": f"{tur} talebi iptal edildi",
                    "summary": ozet,
                    "detail": detay,
                }
            )

        if talep.status == LeaveRequestStatus.TAKEN:
            kayit = denetim.get(("leave.request.taken", talep.pk))
            tarih = kayit.created_at if kayit is not None else talep.updated_at
            tarih = timezone.localtime(tarih)
            detay = dict(ortak)
            isaretleyen = aktorler.get(kayit.user_id) if kayit is not None else None
            if isaretleyen:
                detay["İşaretleyen"] = isaretleyen
            olaylar.append(
                {
                    "date": tarih.date().isoformat(),
                    "date_display": _ad(tarih.date()),
                    "type": "leave_taken",
                    "label": f"{tur} kullanıldı",
                    "summary": aralik,
                    "detail": detay,
                }
            )

    return olaylar


def _izin_bakiye_olaylari(employee: Employee) -> list[dict]:  # noqa: ANN001
    """
    İzin hakkı / bakiye olayları — append-only defterden (`leave_balance_entries`).

    * `accrual` → "<tür> hakkı tanımlandı" (ilk hakediş tanımı)
    * `adjustment` → "<tür> bakiyesi düzeltildi" (gerekçeli manuel düzeltme)

    **Tarihsel bakiye anlık görüntüsü yoktur**: defter yalnız `delta_days` saklar,
    işlem anındaki bakiye toplamı kaydedilmez. Bu yüzden burada "önceki/
    sonraki bakiye" **gösterilmez** (bugünkü bakiye geçmişmiş gibi sunulmaz);
    yalnız o işlemin değişimi, dönemi, gerekçesi ve aktörü gösterilir.
    """
    from apps.leave.models import BalanceEntryKind, LeaveBalanceEntry  # noqa: PLC0415

    kayitlar = list(
        LeaveBalanceEntry.objects.filter(employee=employee)
        .select_related("leave_type", "actor")
        .order_by("created_at")
    )
    if not kayitlar:
        return []

    aktorler = _aktor_numaralari(
        employee.tenant_id, {kayit.actor_id for kayit in kayitlar if kayit.actor_id}
    )

    olaylar: list[dict] = []
    for kayit in kayitlar:
        created_at = timezone.localtime(kayit.created_at)
        if kayit.entry_kind == BalanceEntryKind.ACCRUAL:
            etiket = f"{kayit.leave_type.name} hakkı tanımlandı"
            ozet = f"{_gun_metni(kayit.delta_days)} gün"
            tip = "leave_entitlement"
        elif kayit.entry_kind == BalanceEntryKind.ADJUSTMENT:
            etiket = f"{kayit.leave_type.name} bakiyesi düzeltildi"
            ozet = f"{_isaretli_gun(kayit.delta_days)} gün"
            tip = "leave_balance"
        else:
            continue

        detay = {
            "Tür": kayit.leave_type.name,
            "Yıl": str(kayit.period_year),
            "Değişim": ozet,
        }
        if kayit.reason:
            detay["Gerekçe"] = kayit.reason
        islemi_yapan = aktorler.get(kayit.actor_id)
        if islemi_yapan:
            detay["İşlemi yapan"] = islemi_yapan

        olaylar.append(
            {
                "date": created_at.date().isoformat(),
                "date_display": _ad(created_at.date()),
                "type": tip,
                "label": etiket,
                "summary": ozet,
                "detail": detay,
            }
        )
    return olaylar


#: Yaşananlar sayfa boyutu (server-side pagination; kural §12).
TIMELINE_PAGE_SIZE = 20


def timeline_sayfa(
    olaylar: list[dict],
    *,
    page: int = 1,
    page_size: int = TIMELINE_PAGE_SIZE,
) -> tuple[list[dict], int]:
    """
    Olay listesini **ters kronolojik** (en yeni üstte) sıralar ve sayfalar.

    * Sıralama **backend source-of-truth**: arayüz listeyi ters çevirmez; hangi
      sayfa istenirse istenilsin aynı düzende dilim döner, yeni olay ilk sayfanın
      en üstünde görünür.
    * Birincil anahtar olayın tarihi (`date`, ISO) DESC; eşitlik durumunda
      deterministik tie-breaker **liste konumu DESC** (aynı tarihli olaylar için
      sabit ve tekrarlanabilir sıra).
    """
    sirali = sorted(
        enumerate(olaylar),
        key=lambda cift: (cift[1]["date"], cift[0]),
        reverse=True,
    )
    sirali_olaylar = [olay for _, olay in sirali]
    toplam = max(1, -(-len(sirali_olaylar) // page_size))
    page = max(1, min(page, toplam))
    baslangic = (page - 1) * page_size
    return sirali_olaylar[baslangic:baslangic + page_size], toplam
