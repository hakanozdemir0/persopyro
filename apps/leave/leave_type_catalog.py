"""İzin türü katalogu (sistem kodları).

M14/izin modülü — **sistem izin türleri** burada tanımlıdır; kiracı bunları
kopyalar (`is_system=True`) ve kendi türlerini ekleyebilir. Sistem kodları
**rezervedir**: kiracı aynı kodu yeniden tanımlayamaz (kısmi benzersizlik) ve
servis katmanı sistem türünün kodunu değiştirmeye/soft-delete etmeye izin vermez
(OD16 ruhu: katalog evrimi).

Kod listesi ve bayraklar:
  * **docs'ta tanımlı değildir** — docs/DATABASE.md `hr.leave_types` kartı yalnızca
    tabloyu ve RLS'i tanımlar; kategori kapalı kümesi referans tasarımdan
    (`~/hrsaas_db/05_leave_management.sql`: `annual`/`sick`/`unpaid`/`maternity`/
    `paternity`/`bereavement`/`other`) alınmıştır.
  * Tür **kodları** kullanıcı isteğindeki listedir; bayraklar güvenli varsayılandır
    ve kiracı tarafından panelden değiştirilebilir (seed = örnek/varsayılan).
Sistem kodlarıyla çakışma engeli: `LeaveType.is_system` + `(tenant_id, code)`
kısmi benzersizliği + servis guard'ı.
"""

from __future__ import annotations

from django.db import models


class LeaveCategory(models.TextChoices):
    """İzin kategorisi — referans tasarımdaki kapalı küme."""

    ANNUAL = "annual", "Yıllık"
    SICK = "sick", "Hastalık"
    UNPAID = "unpaid", "Ücretsiz"
    MATERNITY = "maternity", "Doğum"
    PATERNITY = "paternity", "Babalık"
    BEREAVEMENT = "bereavement", "Ölüm/İzin"
    OTHER = "other", "Diğer"


#: Kiracı kurulumunda oluşturulan **varsayılan/örnek** izin türleri.
#: `uses_balance=True` olan türler için bakiye/hakediş (M14 §2) gerekir;
#: `requires_document=True` olan tür belge olmadan **onaylanamaz** (fail-closed).
DEFAULT_LEAVE_TYPES: tuple[dict, ...] = (
    {
        "code": "annual_leave",
        "name": "Yıllık İzin",
        "category": LeaveCategory.ANNUAL,
        "is_paid": True,
        "uses_balance": True,
        "requires_approval": True,
        "requires_document": False,
    },
    {
        "code": "annual_leave_block",
        "name": "Toplu Yıllık İzin",
        "category": LeaveCategory.ANNUAL,
        "is_paid": True,
        "uses_balance": True,
        "requires_approval": False,  # İK toplu atar; bireysel onay aranmaz
        "requires_document": False,
    },
    {
        "code": "work_from_home",
        "name": "Uzaktan Çalışma",
        "category": LeaveCategory.OTHER,
        "is_paid": True,
        "uses_balance": False,
        "requires_approval": True,
        "requires_document": False,
    },
    {
        "code": "flex_time",
        "name": "Esnek Çalışma",
        "category": LeaveCategory.OTHER,
        "is_paid": True,
        "uses_balance": False,
        "requires_approval": True,
        "requires_document": False,
    },
    {
        "code": "sick_leave",
        "name": "Hastalık İzni",
        "category": LeaveCategory.SICK,
        "is_paid": True,
        "uses_balance": False,
        "requires_approval": True,
        "requires_document": True,  # belge olmadan onaylanamaz (M14 §7)
    },
    {
        "code": "administrative_leave",
        "name": "İdari İzin",
        "category": LeaveCategory.OTHER,
        "is_paid": True,
        "uses_balance": False,
        "requires_approval": True,
        "requires_document": False,
    },
    {
        "code": "paternity_leave",
        "name": "Babalık İzni",
        "category": LeaveCategory.PATERNITY,
        "is_paid": True,
        "uses_balance": False,
        "requires_approval": True,
        "requires_document": False,
    },
)

#: Sistem kodu kümesi (rezerve) — kiracı türleri bu kodları kullanamaz.
SYSTEM_LEAVE_TYPE_CODES: frozenset[str] = frozenset(t["code"] for t in DEFAULT_LEAVE_TYPES)


class AccrualMethod(models.TextChoices):
    """Yıllık izin hakediş yöntemi (M14 §2)."""

    FIXED = "fixed", "Sabit (yılda sabit gün)"
    TENURE_BASED = "tenure_based", "Kıdeme göre (hizmet süresi kuralı)"


#: Hizmet süresi kuralı aralıkları **[min, max)** yarı açıktır:
#: örnek "0–5 yıl → 14 gün, 5+ yıl → 21 gün" için kural 1: `min=0, max=60`,
#: kural 2: `min=60, max=NULL` (ay cinsinden). 60. ay ikinci kurala düşer.
RULE_RANGE_HALF_OPEN = True
