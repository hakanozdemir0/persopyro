# M13 — `hr.employee_manager`: **silinmiş** çalışan/yönetici ile yeni **aktif**
# ilişki kurulamaz (OD21 fail-closed).
#
# Kural: açık (yani `effective_to IS NULL` ve `deleted_at IS NULL`) bir ilişki
# kurulacaksa, hem çalışan hem yönetici **soft-delete edilmemiş** olmalıdır.
# Silinmiş bir çalışanın altında yeni bir raporlama ilişkisi açmak, silinmiş
# kayda "canlı" bir anlam yüklerdi; ayrıca onay yönlendirmesi var olmayan bir
# kişiye giderdi.
#
# Kapsam notu: yalnızca **soft-delete** denetlenir (`deleted_at`). Çalışanın
# `status` değeri (ör. `terminated`) bu turda **engel değildir**: doküman yalnızca
# "soft-deleted employee/manager ile yeni aktif ilişki kurulamaz" der (M13 kapsamı).
# Geçmiş ilişkiler silinmez; değişiklik eski satırı **kapatır** (`effective_to`).
#
# Tetikleyici SECURITY INVOKER + sabit `search_path` + tam nitelemeli SQL; hedef
# satır görünmüyorsa (başka kiracı) `NOT FOUND` → hata (fail-closed).

from django.db import migrations

ACTIVE_GUARD = """
CREATE OR REPLACE FUNCTION hr.employee_manager_active_guard() RETURNS trigger
LANGUAGE plpgsql
SET search_path = hr, pg_temp
AS $fn$
DECLARE
    silinmis timestamptz;
BEGIN
    -- Yalnızca AÇIK (aktif) ilişkiler denetlenir; kapatılan/silinmiş satırlar serbest.
    IF NEW.effective_to IS NOT NULL OR NEW.deleted_at IS NOT NULL THEN
        RETURN NEW;
    END IF;

    SELECT e.deleted_at INTO silinmis FROM hr.employees e WHERE e.id = NEW.employee_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Calisan bulunamadi (yok veya kiraci disi): %', NEW.employee_id
            USING ERRCODE = 'check_violation';
    END IF;
    IF silinmis IS NOT NULL THEN
        RAISE EXCEPTION
            'Silinmis calisan icin yeni aktif yonetici iliskisi kurulamaz (OD21): %',
            NEW.employee_id USING ERRCODE = 'check_violation';
    END IF;

    SELECT e.deleted_at INTO silinmis FROM hr.employees e WHERE e.id = NEW.manager_employee_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Yonetici bulunamadi (yok veya kiraci disi): %', NEW.manager_employee_id
            USING ERRCODE = 'check_violation';
    END IF;
    IF silinmis IS NOT NULL THEN
        RAISE EXCEPTION
            'Silinmis calisan yonetici olarak atanamaz (OD21): %', NEW.manager_employee_id
            USING ERRCODE = 'check_violation';
    END IF;

    RETURN NEW;
END
$fn$;

COMMENT ON FUNCTION hr.employee_manager_active_guard() IS
    'OD21/M13: silinmis calisan veya yonetici ile yeni aktif raporlama iliskisini reddeder.';

DROP TRIGGER IF EXISTS trg_employee_manager_active_guard ON hr.employee_manager;
CREATE TRIGGER trg_employee_manager_active_guard
    BEFORE INSERT OR UPDATE ON hr.employee_manager
    FOR EACH ROW EXECUTE FUNCTION hr.employee_manager_active_guard();
"""

ACTIVE_GUARD_REVERSE = """
DROP TRIGGER IF EXISTS trg_employee_manager_active_guard ON hr.employee_manager;
DROP FUNCTION IF EXISTS hr.employee_manager_active_guard();
"""


class Migration(migrations.Migration):

    dependencies = [
        ("organization", "0007_org_same_tenant_guards"),
    ]

    operations = [
        migrations.RunSQL(ACTIVE_GUARD, ACTIVE_GUARD_REVERSE),
    ]
