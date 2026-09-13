# P5a V1 — fazla mesai **veritabanı guard'ları**.
#
# Django bu kuralları ifade edemez (kolonlar arası ifade, durum makinesi); bu yüzden
# ham SQL ile yazılır. Tetikleyici **INVOKER** çalışır (RLS kiracı sınırı aynen
# geçerlidir; SECURITY DEFINER kullanılmaz) — mevcut desen: `leave/0002_m14_leave_guards`.
#
# Zorlanan kurallar:
#   1. `description` en az 5 karakter (denetim izi: "neden" cevaplanabilir olmalı).
#   2. `total_minutes` **sunucunun hesabıyla aynı olmak zorundadır**:
#      `(end_time - start_time)` saniye cinsinden tam sayıya indirilip 60'a bölünür.
#      Böylece "süreyi istemci uydurdu" durumu **veritabanında** imkânsız hale gelir
#      (savunma derinliği: servis + DB).
#   3. Kapalı durum kümesi + **yasak geçişler**: kayıt yalnız `pending` açılır;
#      `pending → approved|rejected|canceled` dışında geçiş yoktur.
#   4. Karar/iptal alanları durumla tutarlıdır: `approved|rejected` → `decided_at`,
#      `canceled` → `canceled_at` **ve** gerekçe zorunlu (fail-closed).

from django.db import migrations

# --- 1-2) Kolon düzeyi CHECK'ler ---------------------------------------------
CHECK_ACIKLAMA = """
ALTER TABLE hr.overtime_requests
    DROP CONSTRAINT IF EXISTS overtime_requests_description_length;
ALTER TABLE hr.overtime_requests
    ADD CONSTRAINT overtime_requests_description_length
    CHECK (char_length(btrim(description)) >= 5);
"""

CHECK_SURE = """
ALTER TABLE hr.overtime_requests
    DROP CONSTRAINT IF EXISTS overtime_requests_minutes_match;
ALTER TABLE hr.overtime_requests
    ADD CONSTRAINT overtime_requests_minutes_match
    CHECK (total_minutes = ((EXTRACT(EPOCH FROM (end_time - start_time)))::int / 60));
"""

# --- 3-4) Durum makinesi guard'ı ---------------------------------------------
DURUM_FN = """
CREATE OR REPLACE FUNCTION hr.overtime_request_state_guard()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = hr, pg_temp
AS $$
DECLARE
    gecis_izinli boolean := false;
BEGIN
    IF TG_OP = 'INSERT' THEN
        -- Kayıt yalnızca onay bekleyen olarak açılır; doğrudan 'approved' yazımı
        -- onay akışını (ve denetim izini) atlatırdı (fail-closed).
        IF NEW.status <> 'pending' THEN
            RAISE EXCEPTION
                'Fazla mesai talebi yalnizca pending olarak olusturulabilir (gelen: %)',
                NEW.status
                USING ERRCODE = 'check_violation';
        END IF;
        RETURN NEW;
    END IF;

    IF NEW.status <> OLD.status THEN
        gecis_izinli := CASE OLD.status
            WHEN 'pending' THEN NEW.status IN ('approved', 'rejected', 'canceled')
            ELSE false
        END;
        IF NOT gecis_izinli THEN
            RAISE EXCEPTION
                'Gecersiz fazla mesai durum gecisi: % -> % (P5a kapali kume)',
                OLD.status, NEW.status
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;

    IF NEW.status IN ('approved', 'rejected') AND NEW.decided_at IS NULL THEN
        RAISE EXCEPTION
            '% durumundaki talep icin decided_at zorunludur', NEW.status
            USING ERRCODE = 'check_violation';
    END IF;

    IF NEW.status = 'canceled' THEN
        IF NEW.canceled_at IS NULL THEN
            RAISE EXCEPTION
                'canceled talep icin canceled_at zorunludur'
                USING ERRCODE = 'check_violation';
        END IF;
        IF btrim(NEW.cancel_reason) = '' THEN
            RAISE EXCEPTION
                'canceled talep icin iptal gerekcesi zorunludur'
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;

    RETURN NEW;
END;
$$;
"""

DURUM_TRG = """
DROP TRIGGER IF EXISTS trg_overtime_requests_state ON hr.overtime_requests;
CREATE TRIGGER trg_overtime_requests_state
    BEFORE INSERT OR UPDATE ON hr.overtime_requests
    FOR EACH ROW EXECUTE FUNCTION hr.overtime_request_state_guard();
"""


class Migration(migrations.Migration):
    dependencies = [
        ("overtime", "0001_p5a_overtime_schema"),
    ]

    operations = [
        migrations.RunSQL(
            sql=CHECK_ACIKLAMA,
            reverse_sql=(
                "ALTER TABLE hr.overtime_requests "
                "DROP CONSTRAINT IF EXISTS overtime_requests_description_length;"
            ),
        ),
        migrations.RunSQL(
            sql=CHECK_SURE,
            reverse_sql=(
                "ALTER TABLE hr.overtime_requests "
                "DROP CONSTRAINT IF EXISTS overtime_requests_minutes_match;"
            ),
        ),
        migrations.RunSQL(
            sql=DURUM_FN + DURUM_TRG,
            reverse_sql=(
                "DROP TRIGGER IF EXISTS trg_overtime_requests_state ON hr.overtime_requests;"
                "DROP FUNCTION IF EXISTS hr.overtime_request_state_guard();"
            ),
        ),
    ]
