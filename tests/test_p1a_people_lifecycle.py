"""
P1-A — People lifecycle veri modeli + izin altyapısı testleri.

Kapsam (kararlar kapalı; `docs/AUTHORIZATION.md` §16, `docs/HR_ROADMAP.md` §28):
  * **OD6/OD7** arşivleme, **OD9** devredilemez izinler, **OD16** rol senkronu,
    **OD30** kişisel veri yazma izni, **OD35/OD36/OD37/OD38** form yapılandırması,
    **OD41** ihbar dönemi, **OD58** işyeri, **OD59** kullanıcı↔çalışan + erişim kesme
    altyapısı, **OD60** geçmiş tabloları, **OD61** izin kodları.

Bu dosya **yalnız P1-A** (şema + izin altyapısı) doğrulamalarını içerir; lifecycle
servisleri/UI (P1-B/P1-C) burada test edilmez.
"""

import datetime as dt
import importlib
import uuid

import pytest
from django.core.exceptions import ValidationError
from django.db import DatabaseError, IntegrityError, connection, transaction

from apps.accounts.models import User
from apps.organization.employee_form_catalog import (
    FIELD_CODES,
    K3_FIELD_CODES,
    LOCKED_FIELD_CODES,
    FormConfigError,
    validate_override,
)
from apps.organization.models import (
    Department,
    Employee,
    EmployeeAssignment,
    EmployeeEmploymentTerms,
    EmployeeStatus,
    FormFieldDefinition,
    Position,
    Workplace,
)
from apps.rbac.permission_catalog import (
    ALL_PERMISSION_CODES,
    DEFAULT_ROLE_TEMPLATES,
    NON_DELEGABLE_PERMISSION_CODES,
    PLATFORM_PERMISSIONS,
)
from apps.rbac.services.roles import create_default_roles
from apps.tenancy.context import tenant_context

pytestmark = pytest.mark.django_db

BUGUN = dt.date(2026, 9, 1)

YENI_IZINLER = (
    "employee.suspend",
    "employee.terminate",
    "employee.restore",
    "employee.history.read",
    "employee.form.manage",
    "employee.sensitive.write",
    "org.workplace.read",
    "org.workplace.manage",
)

YENI_TABLOLAR = (
    "workplaces",
    "employee_employment_terms",
    "employee_assignments",
    "form_field_definitions",
)


# --- yardımcılar -------------------------------------------------------------


def _birim(tenant, kod="GM"):  # noqa: ANN001
    with tenant_context(tenant.id):
        return Department.objects.create(tenant=tenant, code=kod, name=kod.title())


def _pozisyon(tenant, kod="PZ-1"):  # noqa: ANN001
    with tenant_context(tenant.id):
        return Position.objects.create(tenant=tenant, code=kod, name="Uzman", level="mid")


def _calisan(tenant, birim, no="C-001", **ek):  # noqa: ANN001
    with tenant_context(tenant.id):
        return Employee.objects.create(
            tenant=tenant, department=birim, employee_no=no, hire_date=BUGUN, **ek
        )


def _isyeri_veri(tenant, kod="IST", **ek):  # noqa: ANN001
    """İşyeri alan sözlüğü (hata bekleyen testlerde kiracı bağlamı AÇILMAZ)."""
    veri = {
        "tenant": tenant,
        "code": kod,
        "name": f"{kod} Ofis",
        "type": "office",
        "country_code": "TR",
        "timezone": "Europe/Istanbul",
    }
    veri.update(ek)
    return veri


def _isyeri(tenant, kod="IST", **ek):  # noqa: ANN001
    with tenant_context(tenant.id):
        return Workplace.objects.create(**_isyeri_veri(tenant, kod, **ek))


def _kosullar_veri(tenant, calisan, **ek):  # noqa: ANN001
    veri = {
        "tenant": tenant,
        "employee": calisan,
        "effective_from": BUGUN,
        "employment_type": "full_time",
        "working_model": "onsite",
    }
    veri.update(ek)
    return veri


def _kosullar(tenant, calisan, **ek):  # noqa: ANN001
    with tenant_context(tenant.id):
        return EmployeeEmploymentTerms.objects.create(**_kosullar_veri(tenant, calisan, **ek))


def _atama_veri(tenant, calisan, birim, **ek):  # noqa: ANN001
    veri = {"tenant": tenant, "employee": calisan, "department": birim, "effective_from": BUGUN}
    veri.update(ek)
    return veri


def _atama(tenant, calisan, birim, **ek):  # noqa: ANN001
    with tenant_context(tenant.id):
        return EmployeeAssignment.objects.create(**_atama_veri(tenant, calisan, birim, **ek))


_KATALOG_SATIRI = {
    "first_name": ("kimlik", "Kimlik", "A", "K2"),
    "position_id": ("istihdam", "İş Bilgileri", "B", "K0"),
    "blood_type": ("saglik", "Sağlık (K3)", "B", "K3"),
}


def _form_veri(tenant, alan_kodu, **ek):  # noqa: ANN001
    katalog = _KATALOG_SATIRI[alan_kodu]
    veri = {
        "tenant": tenant,
        "form_code": "employee_onboarding",
        "field_code": alan_kodu,
        "section_code": katalog[0],
        "section_label": katalog[1],
        "lock_level": katalog[2],
        "data_class": katalog[3],
        "is_visible": True,
        "is_required": True,
    }
    veri.update(ek)
    return veri


def _form_alani(tenant, alan_kodu, **ek):  # noqa: ANN001
    with tenant_context(tenant.id):
        return FormFieldDefinition.objects.create(**_form_veri(tenant, alan_kodu, **ek))


# ============================ 1) İZİN KATALOĞU ==============================


def test_t1_sekiz_yeni_izin_kodu_katalogda() -> None:
    """OD61: 8 yeni kiracı izin kodu katalogda ve DB'de bulunmalı."""
    from apps.rbac.models import Permission

    for kod in YENI_IZINLER:
        assert kod in ALL_PERMISSION_CODES, kod

    mevcut = set(Permission.objects.filter(code__in=YENI_IZINLER).values_list("code", flat=True))
    assert mevcut == set(YENI_IZINLER)

    assert len(ALL_PERMISSION_CODES) == 41
    assert len(PLATFORM_PERMISSIONS) == 6


def test_t2_platform_rolleri_yeni_tenant_izinlerini_almaz() -> None:
    """OD3/OD61: `platform.*` kodları kiracı şablonlarına giremez."""
    platform_kodlari = {kod for kod, _ad, _mod in PLATFORM_PERMISSIONS}
    assert platform_kodlari.isdisjoint(set(ALL_PERMISSION_CODES))

    for rol, sablon in DEFAULT_ROLE_TEMPLATES.items():
        assert platform_kodlari.isdisjoint(set(sablon["permissions"])), rol
        assert set(sablon["permissions"]).issubset(set(ALL_PERMISSION_CODES)), rol


def test_t3_varsayilan_rol_setleri_dokumanla_uyumlu(tenant_a) -> None:  # noqa: ANN001
    """OD61 hedef sayıları: 41 / 30 / 23 / 17 / 8."""
    assert len(DEFAULT_ROLE_TEMPLATES["tenant_admin"]["permissions"]) == 41
    assert len(DEFAULT_ROLE_TEMPLATES["hr_manager"]["permissions"]) == 30
    assert len(DEFAULT_ROLE_TEMPLATES["tenant_officer"]["permissions"]) == 23
    assert len(DEFAULT_ROLE_TEMPLATES["dept_manager"]["permissions"]) == 17
    assert len(DEFAULT_ROLE_TEMPLATES["employee"]["permissions"]) == 8

    # Servis, şablonları DB'ye aynı sayılarla yazar (RLS: kiracı bağlamı zorunlu).
    with tenant_context(tenant_a.id):
        create_default_roles(tenant_a)

    from apps.rbac.models import Role, RolePermission

    with tenant_context(tenant_a.id):
        for kod, sablon in DEFAULT_ROLE_TEMPLATES.items():
            rol = Role.objects.get(code=kod, tenant=tenant_a)
            sayi = RolePermission.objects.filter(role=rol).count()
            assert sayi == len(sablon["permissions"]), kod


def test_t4_okuma_izni_yazma_vermez(user_a, tenant_a, izinli_kullanici) -> None:  # noqa: ANN001
    """OD30: `employee.sensitive.read` sahibi otomatik WRITE almaz."""
    from apps.rbac.services import user_has_permission

    izinli_kullanici(user_a, tenant_a, "employee.sensitive.read")
    with tenant_context(tenant_a.id):
        assert user_has_permission(user_a, "employee.sensitive.read") is True
        assert user_has_permission(user_a, "employee.sensitive.write") is False


def test_t4b_devredilemez_kodlar_tanimi() -> None:
    """OD9: devredilemez kod listesi dokümanla uyumlu ve devredilebilirlerle ayrık."""
    beklenen = {
        "role.manage",
        "tenant.manage",
        "user.manage",
        "employee.delete",
        "employee.suspend",
        "employee.terminate",
        "employee.restore",
        "employee.sensitive.read",
        "employee.sensitive.write",
        "employee.form.manage",
        "leave.balance.manage",
        "audit.read",
    }
    assert beklenen == set(NON_DELEGABLE_PERMISSION_CODES)


# ================================ 2) İŞYERİ =================================


def test_t5_workplace_tenant_izolasyonu(tenant_a, tenant_b) -> None:  # noqa: ANN001
    _isyeri(tenant_a, "IST")
    isyeri_b = _isyeri(tenant_b, "BER")

    with tenant_context(tenant_a.id):
        assert list(Workplace.objects.values_list("code", flat=True)) == ["IST"]
    with tenant_context(tenant_b.id):
        assert list(Workplace.objects.values_list("code", flat=True)) == ["BER"]

    # kiracı bağlamı yoksa hiçbir satır görünmez (fail-closed)
    with connection.cursor() as cur:
        cur.execute("SELECT set_config('app.tenant_id', '', true)")
    assert Workplace.objects.count() == 0
    assert isyeri_b.code == "BER"


def test_t6_workplace_cross_tenant_gorunmez(tenant_a, tenant_b) -> None:  # noqa: ANN001
    isyeri = _isyeri(tenant_a, "IST")
    with tenant_context(tenant_b.id):
        assert not Workplace.objects.filter(pk=isyeri.pk).exists()


def test_t7_workplace_ayni_kod_aktifken_reddedilir(tenant_a) -> None:  # noqa: ANN001
    _isyeri(tenant_a, "IST")
    with tenant_context(tenant_a.id), pytest.raises(IntegrityError), transaction.atomic():
        Workplace.objects.create(**_isyeri_veri(tenant_a, "IST"))


def test_t8_workplace_soft_delete_sonrasi_kod_yeniden_kullanilabilir(tenant_a) -> None:  # noqa: ANN001
    eski = _isyeri(tenant_a, "IST")
    with tenant_context(tenant_a.id):
        eski.delete()  # soft delete (deleted_at)
        yeni = Workplace.objects.create(
            tenant=tenant_a,
            code="IST",
            name="İstanbul (yeni)",
            type="office",
            country_code="TR",
            timezone="Europe/Istanbul",
        )
        assert yeni.pk != eski.pk


def test_t9_workplace_gecersiz_timezone_ve_ulke_kodu(tenant_a) -> None:  # noqa: ANN001
    with tenant_context(tenant_a.id):
        bozuk = Workplace(
            tenant=tenant_a,
            code="XXX",
            name="Test",
            type="office",
            country_code="tr",  # küçük harf → CHECK ihlali
            timezone="Mars/Olympus",  # geçersiz IANA → model doğrulaması
        )
        with pytest.raises(ValidationError):
            bozuk.full_clean()
        with pytest.raises(IntegrityError), transaction.atomic():
            bozuk.save()


# ========================== 3) İSTİHDAM KOŞULLARI ===========================


def test_t10_eet_employee_cross_tenant_reddedilir(tenant_a, tenant_b) -> None:  # noqa: ANN001
    birim_a = _birim(tenant_a, "GM")
    calisan_a = _calisan(tenant_a, birim_a)
    with (
        tenant_context(tenant_b.id),
        pytest.raises(DatabaseError, match="Kiracilar arasi referans"),
        transaction.atomic(),
        connection.cursor() as cur,
    ):
        cur.execute(
            "INSERT INTO hr.employee_employment_terms "
            "(id, created_at, updated_at, tenant_id, employee_id, effective_from, "
            " employment_type, working_model, contract_type, reason) "
            "VALUES (gen_random_uuid(), now(), now(), %s, %s, %s, 'full_time', 'onsite', '', '')",
            [str(tenant_b.id), str(calisan_a.pk), BUGUN],
        )


def test_t11_eet_tek_acik_kayit(tenant_a) -> None:  # noqa: ANN001
    birim = _birim(tenant_a)
    calisan = _calisan(tenant_a, birim)
    _kosullar(tenant_a, calisan, reason="ilk")
    with tenant_context(tenant_a.id), pytest.raises(IntegrityError), transaction.atomic():
        EmployeeEmploymentTerms.objects.create(
            **_kosullar_veri(tenant_a, calisan, reason="ikinci")
        )


def test_t12_eet_gecersiz_tarih_araligi(tenant_a) -> None:  # noqa: ANN001
    birim = _birim(tenant_a)
    calisan = _calisan(tenant_a, birim)
    with tenant_context(tenant_a.id), pytest.raises(IntegrityError), transaction.atomic():
        EmployeeEmploymentTerms.objects.create(
            **_kosullar_veri(
                tenant_a,
                calisan,
                effective_from=BUGUN,
                effective_to=BUGUN - dt.timedelta(days=1),
            )
        )


def test_t13_eet_gecmis_kayit_korunur(tenant_a) -> None:  # noqa: ANN001
    """OD60: eski satır kapatılır, yenisi açılır; ikisi birlikte sorgulanabilir."""
    birim = _birim(tenant_a)
    calisan = _calisan(tenant_a, birim)
    eski = _kosullar(tenant_a, calisan, reason="ilk koşul")
    with tenant_context(tenant_a.id):
        eski.effective_to = BUGUN + dt.timedelta(days=30)
        eski.save(update_fields=["effective_to", "updated_at"])
    yeni = _kosullar(
        tenant_a,
        calisan,
        effective_from=BUGUN + dt.timedelta(days=31),
        employment_type="part_time",
        reason="yeni koşul",
    )

    with tenant_context(tenant_a.id):
        kayitlar = list(
            EmployeeEmploymentTerms.objects.filter(employee=calisan).order_by("effective_from")
        )
        assert [k.pk for k in kayitlar] == [eski.pk, yeni.pk]
        assert kayitlar[0].effective_to is not None
        assert kayitlar[1].effective_to is None
        assert kayitlar[1].employment_type == "part_time"


# ============================ 4) ATAMA GEÇMİŞİ ==============================


def test_t14_assignment_tek_acik(tenant_a) -> None:  # noqa: ANN001
    birim = _birim(tenant_a)
    calisan = _calisan(tenant_a, birim)
    _atama(tenant_a, calisan, birim)
    with tenant_context(tenant_a.id), pytest.raises(IntegrityError), transaction.atomic():
        EmployeeAssignment.objects.create(**_atama_veri(tenant_a, calisan, birim))


def test_t15_assignment_department_cross_tenant(tenant_a, tenant_b) -> None:  # noqa: ANN001
    birim_a = _birim(tenant_a, "GM")
    calisan_a = _calisan(tenant_a, birim_a)
    birim_b = _birim(tenant_b, "BIRIM-B")
    with (
        tenant_context(tenant_b.id),
        pytest.raises(DatabaseError, match="Kiracilar arasi referans"),
        transaction.atomic(),
        connection.cursor() as cur,
    ):
        cur.execute(
            "INSERT INTO hr.employee_assignments "
            "(id, created_at, updated_at, deleted_at, tenant_id, employee_id, department_id, "
            " effective_from, reason) "
            "VALUES (gen_random_uuid(), now(), now(), NULL, %s, %s, %s, %s, '')",
            [str(tenant_b.id), str(calisan_a.pk), str(birim_b.pk), BUGUN],
        )


def test_t16_assignment_position_cross_tenant(tenant_a, tenant_b) -> None:  # noqa: ANN001
    birim_a = _birim(tenant_a, "GM")
    calisan_a = _calisan(tenant_a, birim_a)
    pozisyon_b = _pozisyon(tenant_b, "PZ-B")
    with (
        tenant_context(tenant_b.id),
        pytest.raises(DatabaseError, match="Kiracilar arasi referans"),
        transaction.atomic(),
        connection.cursor() as cur,
    ):
        cur.execute(
            "INSERT INTO hr.employee_assignments "
            "(id, created_at, updated_at, deleted_at, tenant_id, employee_id, department_id, "
            " position_id, effective_from, reason) "
            "VALUES (gen_random_uuid(), now(), now(), NULL, %s, %s, %s, %s, %s, '')",
            [str(tenant_b.id), str(calisan_a.pk), str(birim_a.pk), str(pozisyon_b.pk), BUGUN],
        )


def test_t17_silinmis_birim_veya_pozisyon_acik_atamada_kullanilamaz(tenant_a) -> None:  # noqa: ANN001
    birim = _birim(tenant_a)
    pozisyon = _pozisyon(tenant_a)
    calisan = _calisan(tenant_a, birim)

    # geçmiş (kapalı) atama soft-delete edilmiş birimi kullanabilir
    kapanmis = _atama(
        tenant_a,
        calisan,
        birim,
        effective_to=BUGUN + dt.timedelta(days=10),
        position=pozisyon,
    )
    assert kapanmis.pk is not None

    with tenant_context(tenant_a.id):
        # OD21 soft-delete: `deleted_at` işaretlenir. (Model `delete()` çağrısı, açık atama
        # FK'si PROTECT olduğu için koruma hatası verir; arşivleme servisi P1-B'de gelecek.)
        Department.objects.filter(pk=birim.pk).update(deleted_at=dt.datetime.now(tz=dt.UTC))
    with (
        tenant_context(tenant_a.id),
        pytest.raises(DatabaseError, match="Silinmis/arsivlenmis kayit"),
        transaction.atomic(),
    ):
        EmployeeAssignment.objects.create(**_atama_veri(tenant_a, calisan, birim))


def test_t18_backfill_dogru_ve_idempotent(tenant_a) -> None:  # noqa: ANN001
    """OD60/§28.10: backfill her çalışan için tek açık atama üretir; tekrar çalıştırma güvenli."""
    birim = _birim(tenant_a, "GM")
    pozisyon = _pozisyon(tenant_a, "PZ-1")
    calisan = _calisan(tenant_a, birim, no="C-777", position=pozisyon)

    migration = importlib.import_module("apps.organization.migrations.0015_p1a_backfill")

    class _SahteEditor:
        connection = connection

    migration.backfill(None, _SahteEditor())
    migration.backfill(None, _SahteEditor())  # idempotans: ikinci kez satır üretmemeli

    with tenant_context(tenant_a.id):
        atamalar = list(EmployeeAssignment.objects.filter(employee=calisan))
        assert len(atamalar) == 1
        atama = atamalar[0]
        assert atama.department_id == birim.pk
        assert atama.position_id == pozisyon.pk
        assert atama.effective_from == calisan.hire_date
        assert atama.effective_to is None

        alanlar = FormFieldDefinition.objects.filter(tenant=tenant_a)
        assert alanlar.count() == len(FIELD_CODES)
        assert set(alanlar.values_list("field_code", flat=True)) == set(FIELD_CODES)


# ========================= 5) FORM ALAN YAPILANDIRMASI ======================


def test_t19_bilinmeyen_field_code_reddedilir(tenant_a) -> None:  # noqa: ANN001
    """OD35: custom alan yok — bilinmeyen kod hem serviste hem DB'de reddedilir."""
    with pytest.raises(FormConfigError):
        validate_override(field_code="custom_alan", is_visible=True, is_required=False)

    with tenant_context(tenant_a.id), pytest.raises(IntegrityError), transaction.atomic():
        FormFieldDefinition.objects.create(
            tenant=tenant_a,
            form_code="employee_onboarding",
            field_code="custom_alan",
            section_code="ozel",
            section_label="Özel",
            lock_level="B",
            data_class="K0",
        )  # katalog dışı kod → DB CHECK (ffd_field_code_known)


def test_t20_a_seviyesi_alan_gizlenemez(tenant_a) -> None:  # noqa: ANN001
    assert "first_name" in LOCKED_FIELD_CODES
    with pytest.raises(FormConfigError):
        validate_override(field_code="first_name", is_visible=False, is_required=True)

    with tenant_context(tenant_a.id), pytest.raises(IntegrityError), transaction.atomic():
        FormFieldDefinition.objects.create(**_form_veri(tenant_a, "first_name", is_visible=False))


def test_t21_a_seviyesi_alan_opsiyonel_yapilamaz(tenant_a) -> None:  # noqa: ANN001
    with pytest.raises(FormConfigError):
        validate_override(field_code="first_name", is_visible=True, is_required=False)

    with tenant_context(tenant_a.id), pytest.raises(IntegrityError), transaction.atomic():
        FormFieldDefinition.objects.create(
            **_form_veri(tenant_a, "first_name", is_required=False)
        )


def test_t22_k3_alan_zorunlu_yapilamaz(tenant_a) -> None:  # noqa: ANN001
    """OD37: K3 (özel nitelikli) alan zorunlu yapılamaz."""
    assert "blood_type" in K3_FIELD_CODES
    with pytest.raises(FormConfigError):
        validate_override(field_code="blood_type", is_visible=True, is_required=True)

    with tenant_context(tenant_a.id), pytest.raises(IntegrityError), transaction.atomic():
        FormFieldDefinition.objects.create(**_form_veri(tenant_a, "blood_type", is_required=True))

    # opsiyonel olarak yazılabilir (gizlenebilir de):
    alan = _form_alani(tenant_a, "blood_type", is_required=False)
    assert alan.is_required is False


def test_t23_form_config_tenant_izolasyonu(tenant_a, tenant_b) -> None:  # noqa: ANN001
    _form_alani(tenant_a, "position_id", is_visible=False)
    with tenant_context(tenant_b.id):
        assert FormFieldDefinition.objects.count() == 0


# ============================== 6) LIFECYCLE ================================


def test_t24_status_kapali_kume_korunur(tenant_a) -> None:  # noqa: ANN001
    """OD41: 4 durumluk kapalı küme değişmedi; küme dışı değer DB'de reddedilir."""
    assert [k for k, _ in EmployeeStatus.choices] == [
        "active",
        "suspended",
        "terminated",
        "archived",
    ]

    birim = _birim(tenant_a)
    with tenant_context(tenant_a.id), pytest.raises(IntegrityError), transaction.atomic():
        Employee.objects.create(
            tenant=tenant_a,
            department=birim,
            employee_no="C-999",
            hire_date=BUGUN,
            status="notice_period",
        )


def test_t25_notice_period_yeni_status_degildir() -> None:
    """OD41: `notice_period` DB durumu DEĞİL, türetilmiş etikettir."""
    assert "notice_period" not in [k for k, _ in EmployeeStatus.choices]
    assert "notice_period" not in EmployeeStatus.values


def test_t26_lifecycle_alanlari_ve_checkler(tenant_a) -> None:  # noqa: ANN001
    birim = _birim(tenant_a)
    calisan = _calisan(tenant_a, birim)

    with tenant_context(tenant_a.id):
        assert calisan.notice_end_date is None
        assert calisan.termination_reason_code == ""  # DJ001: NULL değil, boş dize
        assert calisan.termination_note == ""

        # (a) terminated → tarih + neden zorunlu
        with pytest.raises(IntegrityError), transaction.atomic():
            Employee.objects.filter(pk=calisan.pk).update(status="terminated")

        # (a2) neden kolonu NOT NULL (DJ001: boş dize = ayarlanmamış; NULL yasak)
        with pytest.raises(IntegrityError), transaction.atomic():
            Employee.objects.filter(pk=calisan.pk).update(
                status="terminated",
                termination_date=BUGUN,
                termination_reason_code=None,
            )

        # (b) geçersiz neden kodu reddedilir
        with pytest.raises(IntegrityError), transaction.atomic():
            Employee.objects.filter(pk=calisan.pk).update(
                status="terminated",
                termination_date=BUGUN,
                termination_reason_code="uydurma",
            )

        # (c) notice_end_date < termination_date reddedilir
        with pytest.raises(IntegrityError), transaction.atomic():
            Employee.objects.filter(pk=calisan.pk).update(
                status="terminated",
                termination_date=BUGUN,
                termination_reason_code="resignation",
                notice_end_date=BUGUN - dt.timedelta(days=1),
            )

        # (d) geçerli işten çıkış kabul edilir
        Employee.objects.filter(pk=calisan.pk).update(
            status="terminated",
            termination_date=BUGUN,
            termination_reason_code="resignation",
            notice_end_date=BUGUN + dt.timedelta(days=30),
            termination_note="istifa",
        )
        calisan.refresh_from_db()
        assert calisan.status == "terminated"
        assert calisan.notice_end_date == BUGUN + dt.timedelta(days=30)


def test_t27_user_kismi_benzersizlik(tenant_a, user_a) -> None:  # noqa: ANN001
    """OD59: bir kullanıcıya en fazla bir aktif/arşivlenmemiş çalışan kaydı bağlanır."""
    birim = _birim(tenant_a)
    _calisan(tenant_a, birim, no="C-001", user=user_a)

    with tenant_context(tenant_a.id), pytest.raises(IntegrityError), transaction.atomic():
        Employee.objects.create(
            tenant=tenant_a,
            department=birim,
            employee_no="C-002",
            hire_date=BUGUN,
            user=user_a,
        )

    # arşivlenmiş kayıt kısıtı işgal etmez (yeniden işe alma senaryosu)
    with tenant_context(tenant_a.id):
        Employee.objects.filter(employee_no="C-001").update(status="archived")
        yeni = Employee.objects.create(
            tenant=tenant_a,
            department=birim,
            employee_no="C-003",
            hire_date=BUGUN,
            user=user_a,
        )
        assert yeni.pk is not None


# ============================ 7) AUTH VERSION ===============================


def test_t28_auth_version_varsayilan_sifir(tenant_a) -> None:  # noqa: ANN001
    with tenant_context(tenant_a.id):
        kullanici = User.objects.create_user(
            email="p1a@ornek.test",
            password="Guclu-Parola-123",
            tenant=tenant_a,
            first_name="P1",
            last_name="A",
        )
        assert kullanici.auth_version == 0


def test_t29_auth_version_negatif_olamaz(tenant_a, user_a) -> None:  # noqa: ANN001
    with tenant_context(tenant_a.id), pytest.raises(IntegrityError), transaction.atomic():
        User.objects.filter(pk=user_a.pk).update(auth_version=-1)


# ================================= 8) RLS ===================================


def test_t31_yeni_tablolarda_rls_ve_force_etkin() -> None:
    with connection.cursor() as cur:
        cur.execute(
            "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity "
            "  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            " WHERE n.nspname = 'hr' AND c.relname = ANY(%s)",
            [list(YENI_TABLOLAR)],
        )
        durum = {satir[0]: (satir[1], satir[2]) for satir in cur.fetchall()}

    assert set(durum) == set(YENI_TABLOLAR)
    for tablo, (rls, force) in durum.items():
        assert rls is True, tablo
        assert force is True, tablo


def test_t32_yeni_tablolarda_tenant_izolasyonu(tenant_a, tenant_b) -> None:  # noqa: ANN001
    birim_a = _birim(tenant_a, "GM")
    calisan_a = _calisan(tenant_a, birim_a)
    _atama(tenant_a, calisan_a, birim_a)
    _kosullar(tenant_a, calisan_a)
    _form_alani(tenant_a, "position_id")

    with tenant_context(tenant_b.id):
        assert EmployeeAssignment.objects.count() == 0
        assert EmployeeEmploymentTerms.objects.count() == 0
        assert FormFieldDefinition.objects.count() == 0
        assert Workplace.objects.count() == 0


def test_t33_platform_rolu_yeni_tablolara_erisemez() -> None:
    """Platform rolü (`persopyro_platform`) yeni HR tablolarına erişemez (varsa)."""
    with connection.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_roles WHERE rolname = 'persopyro_platform'")
        if cur.fetchone() is None:
            pytest.skip("persopyro_platform rolü yok (süper kullanıcı kurulumu gerekir)")

        for tablo in YENI_TABLOLAR:
            cur.execute(
                "SELECT has_table_privilege('persopyro_platform', %s, 'SELECT'), "
                "       has_table_privilege('persopyro_platform', %s, 'INSERT'), "
                "       has_table_privilege('persopyro_platform', %s, 'UPDATE'), "
                "       has_table_privilege('persopyro_platform', %s, 'DELETE')",
                [f"hr.{tablo}"] * 4,
            )
            assert cur.fetchone() == (False, False, False, False), tablo


# ===================== 9) UYGULAMA ROLÜ YETKİ SINIRLARI =====================


def test_t34_uygulama_rolunde_delete_geri_alindi() -> None:
    """OD21: yeni tablolarda silme soft-delete'tir → DELETE/TRUNCATE verilmez."""
    with connection.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_roles WHERE rolname = 'persopyro_app'")
        if cur.fetchone() is None:  # pragma: no cover - ortama bağlı
            pytest.skip("persopyro_app rolü yok")

        for tablo in YENI_TABLOLAR:
            cur.execute(
                "SELECT has_table_privilege('persopyro_app', %s, 'SELECT'), "
                "       has_table_privilege('persopyro_app', %s, 'INSERT'), "
                "       has_table_privilege('persopyro_app', %s, 'UPDATE'), "
                "       has_table_privilege('persopyro_app', %s, 'DELETE')",
                [f"hr.{tablo}"] * 4,
            )
            sec, ins, upd, sil = cur.fetchone()
            assert (sec, ins, upd, sil) == (True, True, True, False), tablo


def test_t35_guard_tetikleyicileri_kurulu() -> None:
    """Kiracılar arası referans ve 'silinmiş hedef' guard'ları kurulu olmalı."""
    beklenen = {
        "trg_employee_assignments_department_id_same_tenant",
        "trg_employee_assignments_employee_id_same_tenant",
        "trg_employee_assignments_position_id_same_tenant",
        "trg_employee_employment_terms_employee_id_same_tenant",
        "trg_employee_employment_terms_workplace_id_same_tenant",
        "trg_employee_assignments_department_id_open_ref_not_deleted",
        "trg_employee_assignments_position_id_open_ref_not_deleted",
        "trg_employee_employment_terms_workplace_id_open_ref_not_deleted",
    }
    with connection.cursor() as cur:
        cur.execute(
            "SELECT tgname FROM pg_trigger t "
            "  JOIN pg_class c ON c.oid = t.tgrelid "
            "  JOIN pg_namespace n ON n.oid = c.relnamespace "
            " WHERE n.nspname = 'hr' AND NOT t.tgisinternal AND c.relname = ANY(%s)",
            [list(YENI_TABLOLAR)],
        )
        kurulu = {satir[0] for satir in cur.fetchall()}
    assert beklenen.issubset(kurulu), beklenen - kurulu


def test_t36_isyeri_kapali_kume_ve_zorunlu_alanlar(tenant_a) -> None:  # noqa: ANN001
    """OD58: işyeri türü kapalı küme; `country_code`/`timezone` zorunlu."""
    with tenant_context(tenant_a.id), pytest.raises(IntegrityError), transaction.atomic():
        Workplace.objects.create(
            tenant=tenant_a,
            code="X1",
            name="X",
            type="uzay_istasyonu",
            country_code="TR",
            timezone="Europe/Istanbul",
        )

    with tenant_context(tenant_a.id), pytest.raises(IntegrityError), transaction.atomic():
        Workplace.objects.create(
            tenant=tenant_a, code="X2", name="X", type="office", country_code="TR", timezone=""
        )


def test_t37_backfill_silinmis_calisani_atlar(tenant_a) -> None:  # noqa: ANN001
    """Backfill yalnız silinmemiş çalışanlar için satır üretir."""
    birim = _birim(tenant_a, "GM")
    aktif = _calisan(tenant_a, birim, no="C-100")
    silinmis = _calisan(tenant_a, birim, no="C-101")
    silinmis_pk = silinmis.pk
    with tenant_context(tenant_a.id):
        silinmis.delete()  # soft delete (OD21)

    migration = importlib.import_module("apps.organization.migrations.0015_p1a_backfill")

    class _SahteEditor:
        connection = connection

    migration.backfill(None, _SahteEditor())

    with tenant_context(tenant_a.id):
        assert EmployeeAssignment.objects.filter(employee=aktif).count() == 1
        assert EmployeeAssignment.objects.filter(employee_id=silinmis_pk).count() == 0


def test_t38_workplace_uuid_ve_tenant_alani() -> None:
    """İşyeri PK'sı UUID'dir ve `tenant_id` zorunludur."""
    assert Workplace._meta.pk.get_internal_type() == "UUIDField"
    alan = Workplace._meta.get_field("tenant")
    assert alan.null is False
    assert uuid.UUID(str(Workplace._meta.pk.default()))

def test_t39_varsayilan_form_alanlari_uretilir_ve_idempotent(tenant_a) -> None:  # noqa: ANN001
    """Provizyon yolu: yeni kiracıya katalogdan 33 alan yazılır; tekrar çağrı eklemez."""
    from apps.organization.services import ensure_default_form_fields

    with tenant_context(tenant_a.id):
        assert FormFieldDefinition.objects.count() == 0
        ilk = ensure_default_form_fields(tenant_a)
        assert ilk == len(FIELD_CODES)
        assert FormFieldDefinition.objects.count() == len(FIELD_CODES)

        # tenant override'ı sonradan değiştirilirse provizyon onu EZMEZ
        alan = FormFieldDefinition.objects.get(field_code="position_id")
        alan.is_visible = False
        alan.save(update_fields=["is_visible", "updated_at"])

        ikinci = ensure_default_form_fields(tenant_a)
        assert ikinci == 0
        alan.refresh_from_db()
        assert alan.is_visible is False
