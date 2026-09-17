# P1-B — mevcut çalışanlar için **başlangıç (baseline) atama** backfill'i (veri migration'ı).
#
# NEDEN: `create_employee` artık çalışanla birlikte ilk açık atamayı da açıyor
# (onboarding invariant; OD60 §28.6). Bu düzeltmeden ÖNCE oluşturulmuş
# çalışanlarda `employee_assignments` satırı hiç yoktur → ilk gerçek
# `change_assignment` çağrısında "eski" taraf boş kalır ve Employee Timeline
# old→new geçmişi kaybolur. 0015 backfill'i bu boşluğu yalnız kendi çalıştığı
# andaki çalışanlar için kapatmıştı; sonradan eklenenler kapsam dışı kaldı.
#
# KURAL (0015 backfill ve `create_employee` ile aynı):
#   * Yalnız `deleted_at IS NULL` çalışanlar işlenir.
#   * Yalnız **hiç atama satırı olmayan** çalışanlar (soft-delete edilmiş atama
#     dahil: herhangi bir satır varsa çalışana DOKUNULMAZ).
#   * department_id / position_id = `hr.employees` mevcut snapshot'ı.
#   * effective_from = hire_date; effective_to = NULL; created_by = NULL
#     (migration anında aktör yok; model NULL'a izin verir).
#   * reason sabittir → geri alma yalnız bu migration'ın satırlarını siler.
#   * Yeni alan/model eklenmez.
#
# IDEMPOTANS: `NOT EXISTS` + kısmi benzersizlik hedefli `ON CONFLICT DO NOTHING`.
# Mantıksal olarak tekrar çalıştırılsa bile ikinci satır üretilmez.
#
# RLS NOTU: `hr.employees` ve `hr.employee_assignments` **FORCE RLS**'tir; kiracı
# bağlamı kurulmadan ne `SELECT` ne `INSERT` satır görür — backfill sessizce
# no-op olurdu (0015'te ölçüldü). Bu yüzden her kiracı için
# `set_config('app.tenant_id', …, true)` kurulur (mevcut desen:
# `apps/organization/migrations/0015_p1a_backfill.py`,
# `apps/rbac/migrations/0011_m14_leave_permissions.py`).
# BYPASSRLS veya kalıcı güvenlik gevşetmesi YOK.

from django.db import migrations

#: Bu migration'ın ürettiği satırların sabit gerekçesi (geri alma buna göre çalışır).
REASON = "Baseline atama backfill (migration 0020)"


def _kiracilar(cur) -> list[tuple[str, str]]:  # noqa: ANN001
    """Kiracı listesi (`hr.tenant_directory` RLS dışıdır — mevcut desen)."""
    cur.execute("SELECT tenant_id, slug FROM hr.tenant_directory ORDER BY slug")
    return [(str(satir[0]), satir[1]) for satir in cur.fetchall()]


def baseline_atama_backfill(apps, schema_editor) -> None:  # noqa: ANN001, ARG001
    """Atamasız mevcut çalışanlara tek açık baseline atama yazar (idempotent)."""
    with schema_editor.connection.cursor() as cur:
        for tenant_id, slug in _kiracilar(cur):
            # FORCE RLS: bağlam kurulmadan SELECT/INSERT hiçbir satırı görmez.
            cur.execute("SELECT set_config('app.tenant_id', %s, true)", [tenant_id])
            cur.execute(
                "INSERT INTO hr.employee_assignments "
                "(id, created_at, updated_at, deleted_at, tenant_id, employee_id, department_id, "
                " position_id, effective_from, effective_to, reason, created_by_id) "
                "SELECT gen_random_uuid(), now(), now(), NULL, e.tenant_id, e.id, e.department_id, "
                "       e.position_id, e.hire_date, NULL, %s, NULL "
                "  FROM hr.employees e "
                " WHERE e.deleted_at IS NULL "
                # Herhangi bir atama satırı (silinmiş olsa bile) varsa dokunma.
                "   AND NOT EXISTS (SELECT 1 FROM hr.employee_assignments a "
                "                    WHERE a.employee_id = e.id) "
                # KISMİ benzersiz indeks: ON CONFLICT hedefi predicate ile verilmelidir
                # (predicate olmadan PostgreSQL kısmi indeksi arbiter kabul etmez).
                "ON CONFLICT (employee_id) WHERE effective_to IS NULL AND deleted_at IS NULL "
                "DO NOTHING",
                [REASON],
            )
            cur.execute(
                "SELECT count(*) FROM hr.employee_assignments WHERE reason = %s", [REASON]
            )
            print(
                "  baseline backfill: kiracı '%s' işlendi (bu migration'ın satırı: %d)"
                % (slug, cur.fetchone()[0])
            )


def baseline_atama_geri_al(apps, schema_editor) -> None:  # noqa: ANN001, ARG001
    """Geri alma: yalnız bu migration'ın ürettiği satırlar silinir."""
    with schema_editor.connection.cursor() as cur:
        cur.execute("DELETE FROM hr.employee_assignments WHERE reason = %s", [REASON])


class Migration(migrations.Migration):
    dependencies = [
        ("organization", "0019_p1a_reason_not_null"),
    ]

    operations = [
        migrations.RunPython(baseline_atama_backfill, baseline_atama_geri_al),
    ]
