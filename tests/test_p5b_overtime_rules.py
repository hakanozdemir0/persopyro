"""P5b — fazla mesai kuralı girdi sözleşmesi."""

from datetime import date
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.audit.models import AuditLog
from apps.overtime.models import CompensationType, OvertimeRule
from apps.overtime.services import (
    OvertimeAuthorizationError,
    OvertimeValidationError,
    create_overtime_rule,
    list_overtime_rules,
    calculate_time_off_minutes,
    calculate_time_off_days,
    resolve_overtime_rule,
    validate_overtime_rule,
)
from apps.rbac.models import Permission, Role, RolePermission, RoleScopeType, RoleType, UserRole
from apps.tenancy.context import tenant_context
from tests.testui_helpers import giris, post

PAROLA = "Guclu-Parola-123"


def test_gecerli_fazla_mesai_kurali_girdisi_korunur() -> None:
    veri = validate_overtime_rule(
        code="FM_NORMAL",
        name="Normal fazla mesai",
        min_minutes=60,
        max_minutes=480,
        multiplier=Decimal("1.5"),
        compensation_type=CompensationType.PAY,
        effective_from=date(2026, 1, 1),
        effective_until=None,
    )

    assert veri == {
        "code": "FM_NORMAL",
        "name": "Normal fazla mesai",
        "min_minutes": 60,
        "max_minutes": 480,
        "multiplier": Decimal("1.500"),
        "compensation_type": "pay",
        "effective_from": date(2026, 1, 1),
        "effective_until": None,
    }


@pytest.mark.parametrize(
    ("alan", "deger"),
    [
        ("min_minutes", -1),
        ("max_minutes", 30),
        ("multiplier", Decimal("0")),
        ("compensation_type", "invalid"),
    ],
)
def test_gecersiz_fazla_mesai_kurali_girdisi_reddedilir(alan: str, deger) -> None:  # noqa: ANN001
    veri = {
        "code": "FM_NORMAL",
        "name": "Normal fazla mesai",
        "min_minutes": 60,
        "max_minutes": 480,
        "multiplier": Decimal("1.5"),
        "compensation_type": CompensationType.PAY,
        "effective_from": date(2026, 1, 1),
        "effective_until": None,
    }
    veri[alan] = deger

    with pytest.raises(OvertimeValidationError):
        validate_overtime_rule(**veri)


def test_bitis_tarihi_baslangictan_once_olamaz() -> None:
    with pytest.raises(OvertimeValidationError):
        validate_overtime_rule(
            code="FM_NORMAL",
            name="Normal fazla mesai",
            min_minutes=0,
            max_minutes=None,
            multiplier=Decimal("1"),
            compensation_type=CompensationType.TIME_OFF,
            effective_from=date(2026, 2, 1),
            effective_until=date(2026, 1, 31),
        )


@pytest.fixture
def lot(tenant_a, user_a):  # noqa: ANN001, ANN201
    with tenant_context(tenant_a.id):
        permission = Permission.objects.get(code="overtime.rule.manage")
        read_permission = Permission.objects.get(code="overtime.rule.read")
        role = Role.objects.create(
            tenant=tenant_a,
            name="Kural yöneticisi",
            code="overtime_rule_manager",
            role_type=RoleType.CUSTOM,
            scope_type=RoleScopeType.TENANT,
        )
        RolePermission.objects.create(tenant=tenant_a, role=role, permission=permission)
        RolePermission.objects.create(tenant=tenant_a, role=role, permission=read_permission)
        UserRole.objects.create(tenant=tenant_a, user=user_a, role=role)
    return {"admin": user_a, "tenant": tenant_a}


def test_yetkili_admin_fazla_mesai_kurali_olusturur(lot) -> None:  # noqa: ANN001
    with tenant_context(lot["admin"].tenant_id):
        kural = create_overtime_rule(
            actor=lot["admin"],
            code="FM_NORMAL",
            name="Normal fazla mesai",
            min_minutes=60,
            max_minutes=480,
            multiplier=Decimal("1.5"),
            compensation_type=CompensationType.PAY,
            effective_from=date(2026, 1, 1),
        )

    assert kural.tenant_id == lot["admin"].tenant_id
    assert kural.created_by_id == lot["admin"].pk
    assert kural.multiplier == Decimal("1.500")
    with tenant_context(lot["admin"].tenant_id):
        assert AuditLog.objects.filter(
            tenant_id=lot["admin"].tenant_id,
            action="overtime.rule.create",
            record_id=kural.pk,
        ).exists()


def test_kural_testui_api_uzerinden_yetkili_kullaniciyle_olusturulur(
    lot, csrf_client
) -> None:  # noqa: ANN001
    assert (
        giris(
            csrf_client,
            lot["tenant"],
            lot["admin"].email,
            PAROLA,
        ).status_code
        == 200
    )
    yanit = post(
        csrf_client,
        "/test-ui/api/overtime/rules",
        {
            "code": "FM_API",
            "name": "API test kuralı",
            "min_minutes": 30,
            "max_minutes": 120,
            "multiplier": "1.250",
            "compensation_type": CompensationType.TIME_OFF,
            "effective_from": "2027-01-01",
        },
    )
    assert yanit.status_code == 201
    assert yanit.json()["data"]["code"] == "FM_API"


def test_kural_yetkisi_olmayan_kullanici_tarafindan_olusturulamaz(lot, tenant_a, user_a) -> None:  # noqa: ANN001
    from tests.leave_helpers import kullanici  # noqa: PLC0415

    plain_user = kullanici(tenant_a, "plain-overtime@ornek.test", "Yetkisiz")
    with tenant_context(lot["tenant"].id), pytest.raises(OvertimeAuthorizationError):
        create_overtime_rule(
            actor=plain_user,
            code="FM_NORMAL",
            name="Normal fazla mesai",
            min_minutes=0,
            max_minutes=None,
            multiplier=Decimal("1"),
            compensation_type=CompensationType.PAY,
            effective_from=date(2026, 1, 1),
        )


def test_ayni_tarih_ve_aralikta_cakisan_kural_reddedilir(lot) -> None:  # noqa: ANN001
    with tenant_context(lot["admin"].tenant_id):
        create_overtime_rule(
            actor=lot["admin"],
            code="FM_NORMAL",
            name="Normal fazla mesai",
            min_minutes=0,
            max_minutes=480,
            multiplier=Decimal("1.5"),
            compensation_type=CompensationType.PAY,
            effective_from=date(2026, 1, 1),
        )
        with pytest.raises(OvertimeValidationError):
            create_overtime_rule(
                actor=lot["admin"],
                code="FM_YUKSEK",
                name="Yüksek katsayı",
                min_minutes=240,
                max_minutes=600,
                multiplier=Decimal("2"),
                compensation_type=CompensationType.PAY,
                effective_from=date(2026, 1, 1),
            )


def test_yetkili_kullanici_yalnizca_canli_kurallari_tarih_sirasiyla_okur(lot) -> None:  # noqa: ANN001
    with tenant_context(lot["admin"].tenant_id):
        eski = create_overtime_rule(
            actor=lot["admin"],
            code="FM_ESKI",
            name="Eski fazla mesai",
            min_minutes=0,
            max_minutes=60,
            multiplier=Decimal("1"),
            compensation_type=CompensationType.PAY,
            effective_from=date(2026, 1, 1),
        )
        yeni = create_overtime_rule(
            actor=lot["admin"],
            code="FM_YENI",
            name="Yeni fazla mesai",
            min_minutes=60,
            max_minutes=120,
            multiplier=Decimal("1.5"),
            compensation_type=CompensationType.TIME_OFF,
            effective_from=date(2026, 6, 1),
        )
        OvertimeRule.objects.filter(pk=eski.pk).update(deleted_at=timezone.now())

        kurallar = list(list_overtime_rules(actor=lot["admin"]))

    assert [k.pk for k in kurallar] == [yeni.pk]


def test_kural_okuma_yetkisi_olmayan_kullanici_tarafindan_yapilamaz(lot, tenant_a) -> None:  # noqa: ANN001
    from tests.leave_helpers import kullanici  # noqa: PLC0415

    plain_user = kullanici(tenant_a, "plain-overtime-read@ornek.test", "Yetkisiz")
    with tenant_context(tenant_a.id), pytest.raises(OvertimeAuthorizationError):
        list_overtime_rules(actor=plain_user)


def test_fazla_mesai_tarih_ve_dakikaya_gore_kurali_secer(lot) -> None:  # noqa: ANN001
    with tenant_context(lot["admin"].tenant_id):
        kural = create_overtime_rule(
            actor=lot["admin"],
            code="FM_NORMAL",
            name="Normal fazla mesai",
            min_minutes=60,
            max_minutes=480,
            multiplier=Decimal("1.5"),
            compensation_type=CompensationType.PAY,
            effective_from=date(2026, 1, 1),
            effective_until=date(2026, 12, 31),
        )

        assert resolve_overtime_rule(
            tenant_id=lot["tenant"].id,
            work_date=date(2026, 6, 1),
            total_minutes=120,
        ) == kural


def test_eslesen_fazla_mesai_kurali_yoksa_reddedilir(lot) -> None:  # noqa: ANN001
    with tenant_context(lot["admin"].tenant_id), pytest.raises(OvertimeValidationError):
        resolve_overtime_rule(
            tenant_id=lot["tenant"].id,
            work_date=date(2026, 6, 1),
            total_minutes=120,
        )


def test_time_off_karsiligi_dakika_olarak_katsayili_hesaplanir() -> None:
    assert calculate_time_off_minutes(
        total_minutes=180, multiplier=Decimal("1.5")
    ) == Decimal("270.000")


def test_time_off_karsiligi_tenant_calisma_gunune_gore_gune_donusturulur() -> None:
    assert calculate_time_off_days(
        total_minutes=180, multiplier=Decimal("1.5"), workday_minutes=480
    ) == Decimal("0.5625")


def test_time_off_kredisi_yarim_gun_adimina_asagi_yuvarlanir() -> None:
    from apps.overtime.services import calculate_time_off_credit  # noqa: PLC0415

    assert calculate_time_off_credit(
        total_minutes=180, multiplier=Decimal("1.5")
    ) == {"credited_days": Decimal("0.5"), "remainder_minutes": 30}
