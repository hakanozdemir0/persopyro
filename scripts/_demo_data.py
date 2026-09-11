"""
Demo verisi oluşturucu (geliştirme/smoke testi için).

`manage.py shell` içine stdin olarak verilir:
    .venv/bin/python manage.py shell < scripts/_demo_data.py

Idempotenttir: aynı kiracı/kullanıcı varsa yeniden oluşturmaz.
Parola `DEMO_PASSWORD` ortam değişkeninden okunur (koda yazılmaz).
"""

import os
import uuid
from datetime import timedelta
from decimal import Decimal

from django.utils import timezone

from apps.accounts.models import User
from apps.leave.models import (
    BalanceEntryKind,
    LeaveBalance,
    LeaveBalanceEntry,
    LeavePolicy,
    LeaveRequest,
    LeaveRequestStatus,
    LeaveType,
)
from apps.leave.services import (
    LeaveError,
    accrue_leave_entitlements,
    create_default_leave_types,
    create_leave_policy,
    create_leave_request,
)
from apps.rbac.models import Role, UserRole
from apps.rbac.services import create_default_roles
from apps.tenancy.context import tenant_context
from apps.tenancy.models import Tenant

TENANT_ID = uuid.uuid5(uuid.NAMESPACE_DNS, "demo.persopyro.local")
PAROLA = os.environ["DEMO_PASSWORD"]
EPOSTA = "demo@persopyro.test"

with tenant_context(TENANT_ID):
    kiracı, kiracı_olustu = Tenant.objects.get_or_create(
        id=TENANT_ID,
        defaults={"name": "Demo Şirket", "slug": "demo", "status": "active"},
    )
    roller = create_default_roles(kiracı)

    kullanıcı, kullanıcı_olustu = User.objects.get_or_create(
        tenant=kiracı,
        email=EPOSTA,
        defaults={"first_name": "Demo", "last_name": "Kullanıcı"},
    )
    # Parola HER çalıştırmada ayarlanır: betik yeni bir rastgele parola üretir ve
    # ekrana yazar; eski parolayı korumak duman testini sessizce kırardı (401).
    kullanıcı.set_password(PAROLA)
    kullanıcı.failed_login_count = 0
    kullanıcı.lockout_until = None
    kullanıcı.save(
        update_fields=[
            "password",
            "password_changed_at",
            "failed_login_count",
            "lockout_until",
            "updated_at",
        ]
    )

    # Demo kullanıcıya kiracı yöneticisi rolü verilir.
    rol = Role.objects.get(tenant=kiracı, code="tenant_admin")
    UserRole.objects.get_or_create(tenant=kiracı, user=kullanıcı, role=rol)

    # --- Organizasyon ve çalışan verisi (M10–M13) -----------------------------
    # NOT: Yalnızca **sahte/kişisel olmayan** demo verisi yazılır; kimlik numarası,
    # IBAN gibi gerçek hayatta anlamlı alanlar bilinçli olarak BOŞ bırakılır.
    from datetime import date

    from apps.organization.models import (
        Department,
        Employee,
        EmployeeManager,
        EmployeePersonalData,
        ManagerRelationType,
        Position,
    )

    gm, _ = Department.objects.get_or_create(
        tenant=kiracı, code="GM", defaults={"name": "Genel Müdürlük"}
    )
    finans, _ = Department.objects.get_or_create(
        tenant=kiracı, code="FIN", defaults={"name": "Finans", "parent": gm}
    )
    muhasebe, _ = Department.objects.get_or_create(
        tenant=kiracı, code="MUH", defaults={"name": "Muhasebe", "parent": finans}
    )
    insan_kaynaklari, _ = Department.objects.get_or_create(
        tenant=kiracı, code="IK", defaults={"name": "İnsan Kaynakları", "parent": gm}
    )
    uzman, _ = Position.objects.get_or_create(
        tenant=kiracı, code="UZ-1", defaults={"name": "Uzman", "level": "mid"}
    )
    mudur, _ = Position.objects.get_or_create(
        tenant=kiracı, code="YON-1", defaults={"name": "Yönetici", "level": "senior"}
    )

    def _calisan(no, birim, pozisyon, ad, soyad):  # noqa: ANN001
        kisi, olustu = Employee.objects.get_or_create(
            tenant=kiracı,
            employee_no=no,
            defaults={"department": birim, "position": pozisyon, "hire_date": date(2024, 1, 15)},
        )
        if olustu:
            EmployeePersonalData.objects.get_or_create(
                employee=kisi,
                defaults={"tenant": kiracı, "first_name": ad, "last_name": soyad},
            )
        return kisi

    finans_muduru = _calisan("DEMO-001", finans, mudur, "Demo", "Yönetici")
    muhasebe_calisani = _calisan("DEMO-002", muhasebe, uzman, "Demo", "Muhasebe")
    ik_calisani = _calisan("DEMO-003", insan_kaynaklari, uzman, "Demo", "İnsanKaynakları")
    EmployeeManager.objects.get_or_create(
        tenant=kiracı,
        employee=muhasebe_calisani,
        manager_employee=finans_muduru,
        relation_type=ManagerRelationType.PRIMARY,
        effective_from=date(2024, 1, 15),
    )

    # Birim yöneticisi (UNIT kapsamı: Finans + alt ağacı) — kapsam çözümlemesi için.
    finans_yoneticisi, _ = User.objects.get_or_create(
        tenant=kiracı,
        email="finans.mudur@persopyro.test",
        defaults={"first_name": "Finans", "last_name": "Müdürü"},
    )
    finans_yoneticisi.set_password(PAROLA)
    finans_yoneticisi.save(update_fields=["password", "password_changed_at", "updated_at"])
    dept_manager_rol = Role.objects.get(tenant=kiracı, code="dept_manager")
    UserRole.objects.get_or_create(
        tenant=kiracı,
        user=finans_yoneticisi,
        role=dept_manager_rol,
        scope_org_unit=finans,
    )

    izin_sayisi = len(
        {p for p in rol.role_permissions.values_list("permission__code", flat=True)}
    )

    # --- Manuel test arayüzü (/test-ui/) için kapsam senaryosu -----------------
    # Finans müdürü = DEMO-001 kaydı; böylece yöneticinin çalışan kaydı ve
    # birincil yönetici ilişkisi (DEMO-002 → DEMO-001) tutarlı olur.
    if finans_muduru.user_id is None:
        finans_muduru.user = finans_yoneticisi
        finans_muduru.save(update_fields=["user", "updated_at"])

    # Çalışan kullanıcıları: Muhasebe (kapsam içi) ve İK (kapsam dışı/sibling).
    def _calisan_kullanicisi(eposta: str, ad: str, soyad: str, calisan):  # noqa: ANN001, ANN202
        kisi, _olustu = User.objects.get_or_create(
            tenant=kiracı,
            email=eposta,
            defaults={"first_name": ad, "last_name": soyad},
        )
        kisi.set_password(PAROLA)
        kisi.save(update_fields=["password", "password_changed_at", "updated_at"])
        employee_rol = Role.objects.get(tenant=kiracı, code="employee")
        UserRole.objects.get_or_create(tenant=kiracı, user=kisi, role=employee_rol)
        if calisan.user_id is None:
            calisan.user = kisi
            calisan.save(update_fields=["user", "updated_at"])
        return kisi

    _calisan_kullanicisi(
        "personel@persopyro.test", "Personel", "Muhasebe", muhasebe_calisani
    )
    _calisan_kullanicisi(
        "ik.calisan@persopyro.test", "İK", "Çalışan", ik_calisani
    )

    # İzin türleri, yıllık hakediş politikası ve dönem hakedişi (idempotent).
    create_default_leave_types(actor=kullanıcı, tenant=kiracı)
    yillik_tur = LeaveType.objects.get(tenant=kiracı, code="annual_leave", deleted_at__isnull=True)
    if not LeavePolicy.objects.filter(leave_type=yillik_tur, deleted_at__isnull=True).exists():
        create_leave_policy(
            actor=kullanıcı,
            leave_type=yillik_tur,
            accrual_method="fixed",
            fixed_days=Decimal("14.0"),
        )
    accrue_leave_entitlements(
        actor=kullanıcı, tenant=kiracı, period_year=timezone.localdate().year
    )

    # Demo bakiye tamamlama: onaylanan demo talepleri zamanla bakiyeyi tüketebilir
    # (elle test sırasında talepler onaylanınca). Elle test için en az 5 gün kalsın.
    # NOT: gerçek düzeltme `adjust_leave_balance` servisinden geçer ve
    # `leave.balance.manage` izni ister; bu izin şu an hiçbir rol şablonunda
    # tanımlı değil (bulgu olarak raporlandı). Demo betiği sahte verisini doğrudan yazar.
    yil = timezone.localdate().year
    for calisan in (finans_muduru, muhasebe_calisani, ik_calisani):
        bakiye = LeaveBalance.objects.filter(
            employee=calisan, leave_type=yillik_tur, period_year=yil
        ).first()
        if bakiye is None:
            continue
        kalan = bakiye.available_days
        if kalan is None or kalan >= Decimal("5.0"):
            continue
        fark = Decimal("5.0") - kalan
        bakiye.adjustment_days = bakiye.adjustment_days + fark
        bakiye.save(update_fields=["adjustment_days", "updated_at"])
        LeaveBalanceEntry.objects.create(
            tenant=kiracı,
            balance=bakiye,
            employee=calisan,
            leave_type=yillik_tur,
            period_year=yil,
            entry_kind=BalanceEntryKind.ADJUSTMENT,
            delta_days=fark,
            reason="Demo veri: elle test icin bakiye tamamlama",
            actor=kullanıcı,
        )
        print(
            f"DEMO_BAKIYE {calisan.employee_no}: +{fark} gün "
            f"(yeni kalan {bakiye.available_days})"
        )

    # Onay ekranı için bekleyen talepler: biri kapsam İÇİ (Muhasebe), biri kapsam
    # DIŞI (İK). Böylece Finans müdürü yalnızca birincisini görür/onaylayabilir.
    def _bekleyen_talep(calisan, tur, gun: int, gerekce: str):  # noqa: ANN001, ANN202
        mevcut = LeaveRequest.objects.filter(
            employee=calisan, reason=gerekce, status=LeaveRequestStatus.PENDING
        ).first()
        if mevcut is not None:
            return mevcut
        baslangic = timezone.localdate() + timedelta(days=10)
        try:
            return create_leave_request(
                actor=kullanıcı,
                employee=calisan,
                leave_type=tur,
                start_date=baslangic,
                end_date=baslangic + timedelta(days=gun - 1),
                reason=gerekce,
                submit=True,
            )
        except LeaveError as hata:
            # Bakiye yetersizse demo betiği düşmez; durum bilgilendirilir.
            print(f"DEMO_UYARI {calisan.employee_no}/{gerekce}: talep açılamadı — {hata}")
            return None

    _bekleyen_talep(muhasebe_calisani, yillik_tur, 3, "IZIN_TESTI_MUHASEBE")
    _bekleyen_talep(ik_calisani, yillik_tur, 2, "IZIN_TESTI_IK")

# F11 smoke: askıya alınmış kiracı. Giriş denemesi 403 dönmelidir; bu kiracı
# yalnızca geliştirme ortamında ve yalnızca bu denetim için tutulur.
ASKIDA_ID = uuid.uuid5(uuid.NAMESPACE_DNS, "askida.persopyro.local")
with tenant_context(ASKIDA_ID):
    askida, _ = Tenant.objects.get_or_create(
        id=ASKIDA_ID,
        defaults={"name": "Askıdaki Şirket", "slug": "askida", "status": "suspended"},
    )
    if askida.status != "suspended":
        askida.status = "suspended"
        askida.save(update_fields=["status", "updated_at"])
    create_default_roles(askida)
    askida_kullanici, _ = User.objects.get_or_create(
        tenant=askida,
        email="askida@persopyro.test",
        defaults={"first_name": "Askıda", "last_name": "Kullanıcı"},
    )
    askida_kullanici.set_password(PAROLA)
    askida_kullanici.save(update_fields=["password", "password_changed_at", "updated_at"])

print(f"DEMO_HAZIR kiracı={kiracı.slug} slug={kiracı.slug} kullanıcı={EPOSTA} "
      f"rol_sayısı={len(roller)} admin_izin={izin_sayisi} "
      f"yeni_kiracı={kiracı_olustu} yeni_kullanıcı={kullanıcı_olustu} "
      f"askida_kiracı={askida.slug}({askida.status})")
