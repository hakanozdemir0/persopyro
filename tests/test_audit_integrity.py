"""
F9 regresyon testleri — denetim kaydı bütünlüğü (append-only).

Kural (docs/SECURITY.md F9/T17, docs/DATABASE.md): denetim kaydı değiştirilemez.
Kullanıcı silinmeye çalışıldığında `AuditLog.user` ilişkisi `PROTECT` olduğu için
Django'nun toplayıcısı (collector) **hiçbir SQL üretmeden** `ProtectedError` verir;
yani denetim satırı UPDATE edilmez (eski `SET_NULL` davranışı satırı NULL'a çekerdi).

Ölçüm notu: Django, foreign key DDL'inde `ON DELETE` maddesi **yazmaz**
(`sql_create_fk` şablonu); `on_delete` davranışı tamamen ORM katmanında uygulanır.
Bu yüzden veritabanı kısıtı `NO ACTION`'dır ve ham SQL ile silme de engellenir —
bu test onu da doğrular.
"""

import pytest
from django.db import IntegrityError, connection, transaction
from django.db.models import ProtectedError
from django.utils import timezone

from apps.accounts.models import User
from apps.audit.models import AuditAction, AuditLog
from apps.tenancy.context import tenant_context

pytestmark = pytest.mark.django_db


def _denetim_kaydi(tenant, user):  # noqa: ANN001, ANN202
    return AuditLog.objects.create(
        tenant=tenant,
        user=user,
        action=AuditAction.CREATE,
        table_name="users",
        record_id=user.pk,
        changes={"alan": "deger"},
    )


# --- 7. PROTECT çalışır ----------------------------------------------------


def test_audit_kaydi_olan_kullanici_orm_ile_silinemez(tenant_a, user_a) -> None:  # noqa: ANN001
    with tenant_context(tenant_a.id):
        _denetim_kaydi(tenant_a, user_a)
        assert AuditLog.objects.filter(user=user_a).count() == 1

        with pytest.raises(ProtectedError):
            user_a.delete()

        assert User.objects.filter(pk=user_a.pk).count() == 1


def test_veritabani_seviyesinde_de_silinemez(tenant_a, user_a) -> None:  # noqa: ANN001, PT012
    """
    FK davranışı `NO ACTION`: ham SQL DELETE de engellenir.

    DİKKAT: Django FK'ları `DEFERRABLE INITIALLY DEFERRED` oluşturur, yani kısıt
    denetimi **commit anında** yapılır. Bu yüzden testte `SET CONSTRAINTS ALL
    IMMEDIATE` ile denetim hemen tetiklenir (aksi halde hata sadece commit'te çıkar).
    """
    with tenant_context(tenant_a.id):
        _denetim_kaydi(tenant_a, user_a)

        with pytest.raises(IntegrityError):
            with transaction.atomic(), connection.cursor() as cur:
                cur.execute("DELETE FROM hr.users WHERE id = %s", [str(user_a.pk)])
                cur.execute("SET CONSTRAINTS ALL IMMEDIATE")

        assert User.objects.filter(pk=user_a.pk).count() == 1


# --- 8. Denetim kaydı değişmez --------------------------------------------


def test_denetim_kaydi_degismez(tenant_a, user_a) -> None:  # noqa: ANN001
    with tenant_context(tenant_a.id):
        kayit = _denetim_kaydi(tenant_a, user_a)
        onceki_user_id = kayit.user_id
        onceki_updated_at = kayit.updated_at
        onceki_changes = dict(kayit.changes)

        with pytest.raises(ProtectedError):
            user_a.delete()

        kayit.refresh_from_db()
        assert kayit.user_id == onceki_user_id  # NULL'a çekilmedi
        assert kayit.updated_at == onceki_updated_at  # UPDATE yapılmadı
        assert kayit.changes == onceki_changes
        # OD21: denetim kaydı soft-delete almaz — modelde `deleted_at` alanı yoktur.
        assert not hasattr(kayit, "deleted_at")


def test_denetim_tablosunda_uygulama_rolunun_yazma_yetkisi_yok() -> None:
    """
    Append-only garanti (yetki düzeyi): `persopyro_app` yalnızca INSERT + SELECT.

    NOT: Testler sahip (owner) rolle bağlanır, bu yüzden davranış çalışma zamanında
    değil **yetki sorgusu** ile doğrulanır (aynı yaklaşım: tests/test_database_roles.py).
    """
    from django.conf import settings

    with connection.cursor() as cur:
        for yetki in ("UPDATE", "DELETE", "TRUNCATE"):
            cur.execute(
                "SELECT has_table_privilege(%s, 'hr.audit_logs', %s)",
                [settings.APP_DB_ROLE, yetki],
            )
            assert cur.fetchone()[0] is False, f"uygulama rolü audit_logs: {yetki} yetkisi var"
        for yetki in ("INSERT", "SELECT"):
            cur.execute(
                "SELECT has_table_privilege(%s, 'hr.audit_logs', %s)",
                [settings.APP_DB_ROLE, yetki],
            )
            assert cur.fetchone()[0] is True


def test_denetim_kaydi_zaman_damgasi_ileri_gider(tenant_a, user_a) -> None:  # noqa: ANN001
    """Regresyon koruması: kayıt oluşturulabiliyor ve `created_at` UTC damgası taşır."""
    with tenant_context(tenant_a.id):
        kayit = _denetim_kaydi(tenant_a, user_a)
        assert kayit.created_at <= timezone.now()
