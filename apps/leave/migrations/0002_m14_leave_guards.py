# M14 — izin modülü **veritabanı guard'ları**.
#
# Django bu kuralları ifade edemez (çapraz satır karşılaştırması, durum makinesi);
# bu yüzden ham SQL ile, `BEFORE INSERT/UPDATE` tetikleyicileri olarak yazılır ve
# **INVOKER** çalışır (RLS kiracı sınırı aynen geçerli; SECURITY DEFINER kullanılmaz).
#
# Zorlanan kurallar:
#   1. `leave_policy_rules`: aynı politikada **çakışan hizmet süresi aralığı** olamaz.
#   2. `leave_balances`: **negatif bakiye** yalnızca politikası açıkça izin veriyorsa
#      oluşabilir (fail-closed; politika yoksa reddedilir).
#   3. `leave_requests`: kapalı durum kümesi + **yasak geçişler** (ör. approved → draft)
#      + `approved`/`taken` için `decided_at`, `canceled` için `canceled_at` zorunluluğu
#      + **belge zorunlu türde belgesiz onay yok** (M14 §7, fail-closed).
#   4. `approval_steps`: **adım sırası atlanamaz** (önceki adım karara bağlanmadan
#      sonraki adım onaylanamaz/reddedilemez) + karar zamanı zorunluluğu.

from django.db import migrations

# --- 1) Politika kuralı çakışma guard'ı --------------------------------------
KURAL_CAKISMA_FN = """
CREATE OR REPLACE FUNCTION hr.leave_policy_rules_overlap_guard()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = hr, pg_temp
AS $$
DECLARE
    cakisan integer;
BEGIN
    -- [min, max) yarı açık aralıklar; max NULL ise açık uçlu (sonsuz) sayılır.
    SELECT count(*) INTO cakisan
      FROM hr.leave_policy_rules r
     WHERE r.policy_id = NEW.policy_id
       AND r.deleted_at IS NULL
       AND r.id <> NEW.id
       AND r.min_service_months < COALESCE(NEW.max_service_months, 2147483647)
       AND COALESCE(r.max_service_months, 2147483647) > NEW.min_service_months;

    IF cakisan > 0 THEN
        RAISE EXCEPTION
            'Hizmet suresi kurallari cakisiyor: [% ay, % ay) araligi mevcut kuralla ortusuyor (M14)',
            NEW.min_service_months,
            COALESCE(NEW.max_service_months::text, 'acik')
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;
"""

KURAL_CAKISMA_TRG = """
DROP TRIGGER IF EXISTS trg_leave_policy_rules_overlap ON hr.leave_policy_rules;
CREATE TRIGGER trg_leave_policy_rules_overlap
    BEFORE INSERT OR UPDATE ON hr.leave_policy_rules
    FOR EACH ROW EXECUTE FUNCTION hr.leave_policy_rules_overlap_guard();
"""

# --- 2) Negatif bakiye guard'ı ----------------------------------------------
BAKIYE_FN = """
CREATE OR REPLACE FUNCTION hr.leave_balance_nonnegative_guard()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = hr, pg_temp
AS $$
DECLARE
    hesap numeric;
    izin_var boolean;
BEGIN
    -- ÜRETİLEN kolonu okumak yerine ifade burada hesaplanır (BEFORE tetikleyicide
    -- generated kolon değeri henüz güvenilir değildir).
    hesap := NEW.entitled_days + NEW.carried_forward_days + NEW.adjustment_days
             - NEW.used_days - NEW.reserved_days;

    IF hesap >= 0 THEN
        RETURN NEW;
    END IF;

    SELECT p.allow_negative_balance INTO izin_var
      FROM hr.leave_policies p
     WHERE p.tenant_id = NEW.tenant_id
       AND p.leave_type_id = NEW.leave_type_id
       AND p.deleted_at IS NULL
       AND p.status = 'active';

    IF COALESCE(izin_var, false) THEN
        RETURN NEW;
    END IF;

    RAISE EXCEPTION
        'Negatif izin bakiyesi reddedildi: kalan % gun (politika negatif bakiyeye izin vermiyor)',
        hesap
        USING ERRCODE = 'check_violation';
END;
$$;
"""

BAKIYE_TRG = """
DROP TRIGGER IF EXISTS trg_leave_balances_nonnegative ON hr.leave_balances;
CREATE TRIGGER trg_leave_balances_nonnegative
    BEFORE INSERT OR UPDATE ON hr.leave_balances
    FOR EACH ROW EXECUTE FUNCTION hr.leave_balance_nonnegative_guard();
"""

# --- 3) Talep durum makinesi guard'ı ----------------------------------------
TALEP_FN = """
CREATE OR REPLACE FUNCTION hr.leave_request_state_guard()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = hr, pg_temp
AS $$
DECLARE
    gecis_izinli boolean := false;
    belge_gerekli boolean;
BEGIN
    IF TG_OP = 'INSERT' THEN
        -- Talepler yalnızca taslak veya onaya sunulmuş olarak açılır; doğrudan
        -- 'approved' yazımı bakiye tüketimini atlatırdı (fail-closed).
        IF NEW.status NOT IN ('draft', 'pending') THEN
            RAISE EXCEPTION
                'Izin talebi yalnizca draft/pending durumunda olusturulabilir (gelen: %)',
                NEW.status
                USING ERRCODE = 'check_violation';
        END IF;
        IF NEW.status = 'pending' AND NEW.submitted_at IS NULL THEN
            RAISE EXCEPTION
                'pending talep icin submitted_at zorunludur'
                USING ERRCODE = 'check_violation';
        END IF;
        RETURN NEW;
    END IF;

    IF NEW.status <> OLD.status THEN
        gecis_izinli := CASE OLD.status
            WHEN 'draft'    THEN NEW.status IN ('pending', 'canceled')
            WHEN 'pending'  THEN NEW.status IN ('approved', 'rejected', 'canceled')
            WHEN 'approved' THEN NEW.status IN ('canceled', 'taken')
            ELSE false
        END;
        IF NOT gecis_izinli THEN
            RAISE EXCEPTION
                'Gecersiz izin talebi durum gecisi: % -> % (M14 kapali kume)',
                OLD.status, NEW.status
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;

    IF NEW.status IN ('approved', 'taken') AND NEW.decided_at IS NULL THEN
        RAISE EXCEPTION
            '% durumundaki talep icin decided_at zorunludur', NEW.status
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.status = 'canceled' AND NEW.canceled_at IS NULL THEN
        RAISE EXCEPTION
            'canceled talep icin canceled_at zorunludur'
            USING ERRCODE = 'check_violation';
    END IF;

    IF NEW.status = 'approved' THEN
        SELECT t.requires_document INTO belge_gerekli
          FROM hr.leave_types t
         WHERE t.id = NEW.leave_type_id;
        IF COALESCE(belge_gerekli, false) AND NEW.document_ref IS NULL THEN
            RAISE EXCEPTION
                'Belge zorunlu izin turu: belge referansi olmadan onaylanamaz (M14 §7)'
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;

    RETURN NEW;
END;
$$;
"""

TALEP_TRG = """
DROP TRIGGER IF EXISTS trg_leave_requests_state ON hr.leave_requests;
CREATE TRIGGER trg_leave_requests_state
    BEFORE INSERT OR UPDATE ON hr.leave_requests
    FOR EACH ROW EXECUTE FUNCTION hr.leave_request_state_guard();
"""

# --- 4) Onay adımı sıra guard'ı ---------------------------------------------
ADIM_FN = """
CREATE OR REPLACE FUNCTION hr.approval_step_order_guard()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = hr, pg_temp
AS $$
DECLARE
    bekleyen integer;
BEGIN
    -- Karar (approved/rejected) verilebilmesi için daha küçük adımların
    -- karara bağlanmış (approved/skipped) olması gerekir: sıra atlanamaz.
    IF NEW.status IN ('approved', 'rejected') THEN
        SELECT count(*) INTO bekleyen
          FROM hr.approval_steps s
         WHERE s.subject_kind = NEW.subject_kind
           AND s.subject_id = NEW.subject_id
           AND s.deleted_at IS NULL
           AND s.id <> NEW.id
           AND s.step_no < NEW.step_no
           AND s.status NOT IN ('approved', 'skipped');

        IF bekleyen > 0 THEN
            RAISE EXCEPTION
                'Onay sirasi atlanamaz: % numarali adimdan once karara baglanmamis adim var (M14 §8)',
                NEW.step_no
                USING ERRCODE = 'check_violation';
        END IF;

        IF NEW.decided_at IS NULL THEN
            RAISE EXCEPTION
                'Karar verilen onay adimi icin decided_at zorunludur'
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;

    RETURN NEW;
END;
$$;
"""

ADIM_TRG = """
DROP TRIGGER IF EXISTS trg_approval_steps_order ON hr.approval_steps;
CREATE TRIGGER trg_approval_steps_order
    BEFORE INSERT OR UPDATE ON hr.approval_steps
    FOR EACH ROW EXECUTE FUNCTION hr.approval_step_order_guard();
"""


def _sil(sql: str) -> str:
    return sql


class Migration(migrations.Migration):
    dependencies = [
        ("leave", "0001_m14_leave_schema"),
    ]

    operations = [
        migrations.RunSQL(
            sql=KURAL_CAKISMA_FN + KURAL_CAKISMA_TRG,
            reverse_sql=(
                "DROP TRIGGER IF EXISTS trg_leave_policy_rules_overlap ON hr.leave_policy_rules;"
                "DROP FUNCTION IF EXISTS hr.leave_policy_rules_overlap_guard();"
            ),
        ),
        migrations.RunSQL(
            sql=BAKIYE_FN + BAKIYE_TRG,
            reverse_sql=(
                "DROP TRIGGER IF EXISTS trg_leave_balances_nonnegative ON hr.leave_balances;"
                "DROP FUNCTION IF EXISTS hr.leave_balance_nonnegative_guard();"
            ),
        ),
        migrations.RunSQL(
            sql=TALEP_FN + TALEP_TRG,
            reverse_sql=(
                "DROP TRIGGER IF EXISTS trg_leave_requests_state ON hr.leave_requests;"
                "DROP FUNCTION IF EXISTS hr.leave_request_state_guard();"
            ),
        ),
        migrations.RunSQL(
            sql=ADIM_FN + ADIM_TRG,
            reverse_sql=(
                "DROP TRIGGER IF EXISTS trg_approval_steps_order ON hr.approval_steps;"
                "DROP FUNCTION IF EXISTS hr.approval_step_order_guard();"
            ),
        ),
    ]
