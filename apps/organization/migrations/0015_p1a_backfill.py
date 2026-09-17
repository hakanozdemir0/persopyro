# P1-A — mevcut verinin güvenli backfill'i (veri migration'ı).
#
# KAYNAK KARARLAR (kapalı): OD38 (form konfigürasyonu), OD60 (geçmiş modeli),
# OD58 (işyeri), `docs/HR_ROADMAP.md` §28.1 ve §28.10.
#
# NE YAPILIR?
#   1. **employee_assignments**: mevcut (silinmemiş) her çalışan için `employees.department_id`
#      / `position_id` değerinden **tek açık atama** satırı üretilir; `effective_from = hire_date`.
#      Idempotent: `ON CONFLICT DO NOTHING` (tek açık atama kısmi benzersizliği).
#   2. **form_field_definitions**: her kiracı için katalogdaki **33 alanın varsayılan
#      konfigürasyonu** yazılır (görünür/zorunlu/sıra/kilit seviyesi/veri sınıfı koddan gelir).
#      Idempotent: aynı (tenant, form_code, field_code) için ikinci satır üretilmez.
#
# NE YAPILMAZ (bilinçli)?
#   * **employee_employment_terms** için satır **üretilmez**: mevcut şemada istihdam tipi /
#     çalışma modeli bilgisi **yoktur**; uydurma değer (ör. "full_time") yazmak yanlış iş
#     verisi üretirdi (§28.10). İlk satır P1-B'de gerçek form verisiyle açılır.
#   * Legacy çalışanlar için otomatik kullanıcı (login) **üretilmez**: `user_id` NULL kalabilir
#     (OD59 zorunluluğu yalnız **yeni** P1 create akışında uygulanır).
#
# RLS NOTU (ÖNEMLİ): kaynak tablo `hr.employees` **FORCE RLS**'tir; kiracı bağlamı
# kurulmazsa `SELECT` **hiçbir satır döndürmez** ve backfill sessizce no-op olur
# (ilk uygulamada ölçüldü ve düzeltildi). Bu yüzden her kiracı için
# `set_config('app.tenant_id', …, true)` ile bağlam kurulur (mevcut desen:
# `apps/rbac/migrations/0011_m14_leave_permissions.py`).

from django.db import migrations

#: Katalogdan **donmuş** alan listesi: (field_code, section_code, section_label, sort_order,
#: is_visible, is_required, lock_level, data_class). Kaynak: `employee_form_catalog.py`.
KATALOG_ALANLARI: tuple[tuple[str, str, str, int, bool, bool, str, str], ...] = (
    ("first_name", "kimlik", "Kimlik", 10, True, True, "A", "K2"),
    ("last_name", "kimlik", "Kimlik", 20, True, True, "A", "K2"),
    ("email", "hesap", "Giriş Hesabı", 10, True, True, "A", "K2"),
    ("password", "hesap", "Giriş Hesabı", 20, True, True, "A", "K2"),
    ("hire_date", "istihdam", "İş Bilgileri", 10, True, True, "A", "K0"),
    ("department_id", "istihdam", "İş Bilgileri", 20, True, True, "A", "K0"),
    ("employee_no", "istihdam", "İş Bilgileri", 30, True, True, "A", "K0"),
    ("status", "istihdam", "İş Bilgileri", 40, True, True, "A", "K0"),
    ("position_id", "istihdam", "İş Bilgileri", 50, True, False, "B", "K0"),
    ("manager_employee_id", "istihdam", "İş Bilgileri", 60, True, False, "B", "K0"),
    ("employment_type", "istihdam", "İş Bilgileri", 70, True, False, "B", "K0"),
    ("working_model", "istihdam", "İş Bilgileri", 80, True, False, "B", "K0"),
    ("workplace_id", "istihdam", "İş Bilgileri", 90, True, False, "B", "K0"),
    ("contract_type", "sozlesme", "Sözleşme ve Deneme Süresi", 10, True, False, "B", "K0"),
    ("contract_start_date", "sozlesme", "Sözleşme ve Deneme Süresi", 20, True, False, "B", "K0"),
    ("contract_end_date", "sozlesme", "Sözleşme ve Deneme Süresi", 30, True, False, "C", "K0"),
    ("probation_end_date", "sozlesme", "Sözleşme ve Deneme Süresi", 40, True, False, "C", "K0"),
    ("birth_date", "kimlik", "Kimlik", 30, True, False, "B", "K2"),
    ("national_id", "kimlik", "Kimlik", 40, True, False, "C", "K2"),
    ("citizenship", "kimlik", "Kimlik", 50, True, False, "B", "K2"),
    ("personal_email", "iletisim", "İletişim", 10, True, False, "B", "K2"),
    ("personal_phone", "iletisim", "İletişim", 20, True, False, "B", "K2"),
    ("address", "iletisim", "İletişim", 30, True, False, "B", "K2"),
    ("emergency_contact_phone", "iletisim", "İletişim", 40, True, False, "B", "K2"),
    ("emergency_contact_relation", "iletisim", "İletişim", 50, True, False, "B", "K2"),
    ("bank_name", "banka", "Banka", 10, True, False, "B", "K2"),
    ("iban", "banka", "Banka", 20, True, False, "C", "K2"),
    ("work_permit_type", "izin", "Çalışma İzni", 10, True, False, "C", "K2"),
    ("work_permit_valid_until", "izin", "Çalışma İzni", 20, True, False, "C", "K2"),
    ("blood_type", "saglik", "Sağlık (K3)", 10, True, False, "B", "K3"),
    ("health_notes", "saglik", "Sağlık (K3)", 20, True, False, "B", "K3"),
    ("disability_status", "saglik", "Sağlık (K3)", 30, True, False, "B", "K3"),
    ("biometric_ref", "saglik", "Sağlık (K3)", 40, True, False, "B", "K3"),
)

FORM_KODU = "employee_onboarding"


def backfill(apps, schema_editor) -> None:  # noqa: ANN001, ARG001
    with schema_editor.connection.cursor() as cur:
        # Bu veri migration'ı 0015'te çalışırken henüz eklenmemiş kolonları
        # kullanamaz. Testlerde veya yeniden çalıştırmalarda daha yeni şema ile
        # çağrılabildiği için, sonradan eklenen alanları yalnızca mevcutsa ekle.
        cur.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'hr' AND table_name = 'form_field_definitions'"
        )
        mevcut_form_kolonlari = {satir[0] for satir in cur.fetchall()}
        ek_kolonlar: list[str] = []
        ek_degerler: list[str] = []
        ek_parametreler: list[object] = []
        if "field_type" in mevcut_form_kolonlari:
            ek_kolonlar.append("field_type")
            ek_degerler.append("%s")
            ek_parametreler.append("text")
        if "options" in mevcut_form_kolonlari:
            ek_kolonlar.append("options")
            ek_degerler.append("'[]'::jsonb")
        if "layout_width" in mevcut_form_kolonlari:
            ek_kolonlar.append("layout_width")
            ek_degerler.append("%s")
            ek_parametreler.append(1)
        if "is_public" in mevcut_form_kolonlari:
            ek_kolonlar.append("is_public")
            ek_degerler.append("%s")
            ek_parametreler.append(False)

        # Kiracı listesi: `hr.tenant_directory` RLS dışıdır ve app rolüne SELECT verilir.
        cur.execute("SELECT tenant_id, slug FROM hr.tenant_directory ORDER BY slug")
        kiracilar = cur.fetchall()

        for tenant_id, slug in kiracilar:
            # FORCE RLS: bağlam kurulmadan `hr.employees` görünmez (backfill no-op olurdu).
            cur.execute("SELECT set_config('app.tenant_id', %s, true)", [str(tenant_id)])

            # 1) mevcut atamalar (yalnız hiç atama satırı olmayan çalışanlar için)
            cur.execute(
                "INSERT INTO hr.employee_assignments "
                "(id, created_at, updated_at, deleted_at, tenant_id, employee_id, department_id, "
                " position_id, effective_from, effective_to, reason, created_by_id) "
                "SELECT gen_random_uuid(), now(), now(), NULL, e.tenant_id, e.id, e.department_id, "
                "       e.position_id, e.hire_date, NULL, "
                "       'P1-A backfill: mevcut atama (migration 0015)', NULL "
                "  FROM hr.employees e "
                " WHERE e.deleted_at IS NULL "
                # KISMİ benzersiz indeks: ON CONFLICT hedefi predicate ile verilmelidir
                # (predicate olmadan PostgreSQL kısmi indeksi arbiter olarak kullanmaz →
                # idempotans kaybolur; ölçülerek düzeltildi).
                "ON CONFLICT (employee_id) WHERE effective_to IS NULL AND deleted_at IS NULL "
                "DO NOTHING"
            )
            # NOT: bu sayım **tablo geneli**dir; yeni tabloların RLS'i bu migration'dan
            # SONRA (`0016_rls_p1a`) etkinleştiği için kiracı filtresi yoktur.
            cur.execute("SELECT count(*) FROM hr.employee_assignments")
            atama = cur.fetchone()[0]

            # 2) form alan varsayılanları
            for (
                field_code,
                section_code,
                section_label,
                sort_order,
                is_visible,
                is_required,
                lock_level,
                data_class,
            ) in KATALOG_ALANLARI:
                cur.execute(
                    "INSERT INTO hr.form_field_definitions "
                    "(id, created_at, updated_at, deleted_at, tenant_id, form_code, field_code, "
                    " section_code, section_label, sort_order, is_visible, is_required, "
                    " label_override, helper_text, lock_level, data_class, created_by_id"
                    + (", " + ", ".join(ek_kolonlar) if ek_kolonlar else "")
                    + ") "
                    "VALUES (gen_random_uuid(), now(), now(), NULL, %s, %s, %s, %s, %s, %s, %s, "
                    # `label_override` boş dize: kolon `0019_p1a_reason_not_null` ile NOT NULL
                    # olduğu için NULL yazımı sonradan hata verir (bu fonksiyon testlerde de
                    # çağrılır). Semantik aynıdır: boş = override yok.
                    "        %s, '', '', %s, %s, NULL"
                    + (", " + ", ".join(ek_degerler) if ek_degerler else "")
                    + ") "
                    # KISMİ benzersiz indeks → predicate zorunlu (idempotans).
                    "ON CONFLICT (tenant_id, form_code, field_code) "
                    "  WHERE deleted_at IS NULL DO NOTHING",
                    [
                        str(tenant_id),
                        FORM_KODU,
                        field_code,
                        section_code,
                        section_label,
                        sort_order,
                        is_visible,
                        is_required,
                        lock_level,
                        data_class,
                        *ek_parametreler,
                    ],
                )
            cur.execute("SELECT count(*) FROM hr.form_field_definitions")
            alan = cur.fetchone()[0]
            print(
                "  P1-A backfill: kiracı '%s' işlendi (tablo geneli: atama=%d, form alanı=%d)"
                % (slug, atama, alan)
            )


def backfill_geri_al(apps, schema_editor) -> None:  # noqa: ANN001, ARG001
    """Geri alma: yalnız bu migration'ın ürettiği satırlar silinir (atama + form alanları)."""
    with schema_editor.connection.cursor() as cur:
        cur.execute(
            "DELETE FROM hr.employee_assignments WHERE reason = %s",
            ["P1-A backfill: mevcut atama (migration 0015)"],
        )
        cur.execute("DELETE FROM hr.form_field_definitions WHERE form_code = %s", [FORM_KODU])


class Migration(migrations.Migration):
    dependencies = [
        ("organization", "0014_p1a_people_lifecycle"),
    ]

    operations = [
        migrations.RunPython(backfill, backfill_geri_al),
    ]
