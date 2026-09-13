"""
Çalışan onboarding formu — **alan kataloğu** (P1-A, kod tarafı).

Kararlar (kapalı, `docs/AUTHORIZATION.md` §16):
  * **OD35** — custom alan **yok**: yalnız bu katalogdaki alanlar yapılandırılabilir;
    bilinmeyen `field_code` **fail-closed** (reddedilir).
  * **OD36** — yapılandırma izni `employee.form.manage`; denetim olayı
    `employee_form.config.update`.
  * **OD37** — **K3** alanlar hiçbir tenant tarafından **zorunlu yapılamaz**
    (gizlenebilir, toplanabilir, opsiyonel olabilir).
  * **OD38** — sürüm tablosu yok; değişiklik audit'e yazılır, mevcut kayıtlar
    geçersiz sayılmaz.

Kilit seviyeleri (`docs/HR_ROADMAP.md` §28.4):

  | Seviye | Tenant ne yapabilir |
  |---|---|
  | **A** | Yalnız etiket/sıra. **Gizleyemez, opsiyonel yapamaz** (sunucu + DB `CHECK`). |
  | **B** | Görünür/gizli + zorunlu/opsiyonel + sıra + etiket. |
  | **C** | B ile aynı + **ülke / istihdam tipi / çalışma modeli** koşuluna bağlı varsayılan. |

Veri sınıfları: `K0` (genel), `K1` (iç), `K2` (kişisel), `K3` (özel nitelikli).
Sınıflandırma **kodda** durur; tenant yalnız görünürlük/zorunluluk değiştirir
(tenant bu kolonları **yazamaz** — sunucu hesaplar).
"""

from __future__ import annotations

from dataclasses import dataclass

FORM_CODE_EMPLOYEE_ONBOARDING = "employee_onboarding"

#: Veri sınıfları (kapalı küme).
DATA_CLASSES: tuple[str, ...] = ("K0", "K1", "K2", "K3")

#: Kilit seviyeleri (kapalı küme).
LOCK_LEVELS: tuple[str, ...] = ("A", "B", "C")


@dataclass(frozen=True, slots=True)
class FormField:
    """Katalogdaki tek alan tanımı."""

    code: str
    section_code: str
    section_label: str
    lock_level: str
    data_class: str
    default_visible: bool
    default_required: bool
    sort_order: int


def _f(
    code: str,
    section_code: str,
    section_label: str,
    lock_level: str,
    data_class: str,
    sort_order: int,
    *,
    visible: bool = True,
    required: bool = True,
) -> FormField:
    return FormField(
        code=code,
        section_code=section_code,
        section_label=section_label,
        lock_level=lock_level,
        data_class=data_class,
        default_visible=visible,
        default_required=required,
        sort_order=sort_order,
    )


# --- Katalog -----------------------------------------------------------------
# Sıra numaraları **bölüm içinde** artan; UI bölümü `section_code` ile gruplar.
EMPLOYEE_FORM_FIELDS: tuple[FormField, ...] = (
    # --- A: sistem-kilitli (gizlenemez / opsiyonel yapılamaz) -----------------
    _f("first_name", "kimlik", "Kimlik", "A", "K2", 10),
    _f("last_name", "kimlik", "Kimlik", "A", "K2", 20),
    _f("email", "hesap", "Giriş Hesabı", "A", "K2", 10),
    _f("password", "hesap", "Giriş Hesabı", "A", "K2", 20),
    _f("hire_date", "istihdam", "İş Bilgileri", "A", "K0", 10),
    _f("department_id", "istihdam", "İş Bilgileri", "A", "K0", 20),
    _f("employee_no", "istihdam", "İş Bilgileri", "A", "K0", 30),
    _f("status", "istihdam", "İş Bilgileri", "A", "K0", 40),
    # --- B/C: yapılandırılabilir ---------------------------------------------
    _f("position_id", "istihdam", "İş Bilgileri", "B", "K0", 50, required=False),
    _f("manager_employee_id", "istihdam", "İş Bilgileri", "B", "K0", 60, required=False),
    _f("employment_type", "istihdam", "İş Bilgileri", "B", "K0", 70, required=False),
    _f("working_model", "istihdam", "İş Bilgileri", "B", "K0", 80, required=False),
    _f("workplace_id", "istihdam", "İş Bilgileri", "B", "K0", 90, required=False),
    _f("contract_type", "sozlesme", "Sözleşme ve Deneme Süresi", "B", "K0", 10, required=False),
    _f(
        "contract_start_date", "sozlesme", "Sözleşme ve Deneme Süresi", "B", "K0", 20,
        required=False,
    ),
    _f("contract_end_date", "sozlesme", "Sözleşme ve Deneme Süresi", "C", "K0", 30, required=False),
    _f(
        "probation_end_date", "sozlesme", "Sözleşme ve Deneme Süresi", "C", "K0", 40,
        required=False,
    ),
    _f("birth_date", "kimlik", "Kimlik", "B", "K2", 30, required=False),
    _f("national_id", "kimlik", "Kimlik", "C", "K2", 40, required=False),
    _f("citizenship", "kimlik", "Kimlik", "B", "K2", 50, required=False),
    _f("personal_email", "iletisim", "İletişim", "B", "K2", 10, required=False),
    _f("personal_phone", "iletisim", "İletişim", "B", "K2", 20, required=False),
    _f("address", "iletisim", "İletişim", "B", "K2", 30, required=False),
    _f("emergency_contact_phone", "iletisim", "İletişim", "B", "K2", 40, required=False),
    _f("emergency_contact_relation", "iletisim", "İletişim", "B", "K2", 50, required=False),
    _f("bank_name", "banka", "Banka", "B", "K2", 10, required=False),
    _f("iban", "banka", "Banka", "C", "K2", 20, required=False),
    _f("work_permit_type", "izin", "Çalışma İzni", "C", "K2", 10, required=False),
    _f("work_permit_valid_until", "izin", "Çalışma İzni", "C", "K2", 20, required=False),
    _f("blood_type", "saglik", "Sağlık (K3)", "B", "K3", 10, required=False),
    _f("health_notes", "saglik", "Sağlık (K3)", "B", "K3", 20, required=False),
    _f("disability_status", "saglik", "Sağlık (K3)", "B", "K3", 30, required=False),
    _f("biometric_ref", "saglik", "Sağlık (K3)", "B", "K3", 40, required=False),
)

#: Bilinen alan kodları (DB `CHECK` listesi bu katalogdan üretilir).
FIELD_CODES: tuple[str, ...] = tuple(field.code for field in EMPLOYEE_FORM_FIELDS)

#: Kod → alan (hızlı arama).
FIELDS_BY_CODE: dict[str, FormField] = {field.code: field for field in EMPLOYEE_FORM_FIELDS}

#: **A** seviyesi: tenant gizleyemez / opsiyonel yapamaz.
LOCKED_FIELD_CODES: frozenset[str] = frozenset(
    f.code for f in EMPLOYEE_FORM_FIELDS if f.lock_level == "A"
)

#: **K3** (özel nitelikli): tenant **zorunlu yapamaz**.
K3_FIELD_CODES: frozenset[str] = frozenset(
    f.code for f in EMPLOYEE_FORM_FIELDS if f.data_class == "K3"
)


class FormConfigError(ValueError):
    """Form yapılandırması kural ihlali (fail-closed)."""


def is_known_field(field_code: str) -> bool:
    return field_code in FIELDS_BY_CODE


def field_codes_for_section(section_code: str) -> tuple[str, ...]:
    return tuple(f.code for f in EMPLOYEE_FORM_FIELDS if f.section_code == section_code)


def validate_override(*, field_code: str, is_visible: bool, is_required: bool) -> None:
    """
    Tenant override'ını doğrular (OD35/OD37 — fail-closed).

    Kurallar:
      1. Bilinmeyen `field_code` → reddedilir (custom alan yok, OD35).
      2. **A** seviyesi alan gizlenemez veya opsiyonel yapılamaz (OD37/§28.4).
      3. **K3** alan zorunlu yapılamaz (OD37 — KVKK aşırı veri toplama).
    """
    if not is_known_field(field_code):
        raise FormConfigError(
            f"Bilinmeyen form alanı: {field_code!r}. Yalnız katalogdaki alanlar "
            "yapılandırılabilir (OD35: custom alan yok)."
        )
    if field_code in LOCKED_FIELD_CODES and (not is_visible or not is_required):
        raise FormConfigError(
            f"A seviyesi (sistem-kilitli) alan gizlenemez/opsiyonel yapılamaz: {field_code!r}."
        )
    if field_code in K3_FIELD_CODES and is_required:
        raise FormConfigError(
            f"K3 (özel nitelikli) alan zorunlu yapılamaz: {field_code!r} (OD37)."
        )
