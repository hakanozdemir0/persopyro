"""
Kapsam çözümleyici sondası (fresh-install doğrulaması için).

`manage.py shell` içine stdin olarak verilir; **tek satır** özet basar:

    KAPSAM_PROBE finans_birim=2 kardes_haric=1 kapsam_ici_erisim=1
                 kapsam_disi_erisim=0 kisisel_veri=0 tenant_geneli=1

Beklenen anlamlar (demo verisi: GM → Finans → Muhasebe, GM → İK):

  * `finans_birim=2`      → dept_manager kapsamı = Finans + alt birimi (Muhasebe)
  * `kardes_haric=1`      → kardeş birim (İK) kapsamda değil
  * `kapsam_ici_erisim=1` → alt ağaçtaki çalışan için `employee.read` izinli
  * `kapsam_disi_erisim=0`→ kardeş birimdeki çalışan için DENY
  * `kisisel_veri=0`      → organizasyon kapsamı K2/K3 erişimi ÜRETMEZ
  * `tenant_geneli=1`     → kiracı yöneticisi (TENANT) kiracı içindeki çalışana erişir

Herhangi bir üretim hatası çıkarsa `KAPSAM_PROBE_HATA` basar (fail-closed ölçüm).
"""

import uuid

from apps.organization.models import Department, Employee
from apps.organization.services import can_access_employee, can_access_employee_personal_data
from apps.tenancy.context import tenant_context

TENANT_ID = uuid.uuid5(uuid.NAMESPACE_DNS, "demo.persopyro.local")

try:
    from apps.accounts.models import User
    from apps.rbac.services import user_org_scope

    with tenant_context(TENANT_ID):
        yonetici = User.objects.get(email="finans.mudur@persopyro.test")
        finans = Department.objects.get(code="FIN")
        insan_kaynaklari = Department.objects.get(code="IK")
        muhasebe_calisani = Employee.objects.get(employee_no="DEMO-002")
        ik_calisani = Employee.objects.get(employee_no="DEMO-003")

        kapsam = user_org_scope(yonetici)
        finans_birim = len(kapsam.unit_ids)
        kardes_haric = 1 if insan_kaynaklari.pk not in kapsam.unit_ids else 0
        kapsam_ici = (
            1
            if can_access_employee(yonetici, muhasebe_calisani, "employee.read")
            else 0
        )
        kapsam_disi = (
            1 if can_access_employee(yonetici, ik_calisani, "employee.read") else 0
        )
        kisisel = (
            1
            if can_access_employee_personal_data(
                yonetici, muhasebe_calisani, mfa_ok=True, purpose_given=True
            )
            else 0
        )

        # Kiracı yöneticisi (TENANT kapsamı) kiracı geneli erişir.
        kiracı_yoneticisi = User.objects.get(email="demo@persopyro.test")
        tenant_geneli = (
            1
            if can_access_employee(kiracı_yoneticisi, ik_calisani, "employee.read")
            else 0
        )

    print(
        "KAPSAM_PROBE "
        f"finans_birim={finans_birim} kardes_haric={kardes_haric} "
        f"kapsam_ici_erisim={kapsam_ici} kapsam_disi_erisim={kapsam_disi} "
        f"kisisel_veri={kisisel} tenant_geneli={tenant_geneli}"
    )
except Exception as exc:  # noqa: BLE001
    print(f"KAPSAM_PROBE_HATA {type(exc).__name__}: {exc}")
