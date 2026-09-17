"""P1-B performans: RBAC yazımlarında request-scope izin önbelleğini geçersiz kıl.

`effective_permission_codes` contextvars tabanlı request-scope önbellek kullanır
(`apps/rbac/services/permissions.py`). Bu modüldeki herhangi bir RBAC satırı
(UserRole, RolePermission, Role, UserPermissionOverride) yazıldığında/silindiğinde
önbellek geçersizleşmeli ki aynı bağlamda (test/işlem) eski izin kümesi okunmasın.

Sinyal kullanmak yerine (process-wide yan etki) model `save`/`delete` override'ı
tercih edildi: yalnız gerçekten yazılan satırda tetiklenir, import döngüsü yoktur
(invalidate, permissions modülünde; models bu modülü import etmez).
"""

from apps.rbac.models import Role, RolePermission, UserPermissionOverride, UserRole


def _invalid_et(years) -> None:
    from apps.rbac.services.permissions import invalidate_permission_cache  # noqa: PLC0415

    invalidate_permission_cache()


for _model in (UserRole, RolePermission, Role, UserPermissionOverride):
    _orig_save = _model.save
    _orig_delete = _model.delete

    def _save(self, *args, _orig=_orig_save, **kwargs):  # noqa: ANN001, ANN003
        sonuc = _orig(self, *args, **kwargs)
        _invalid_et(self)
        return sonuc

    def _delete(self, *args, _orig=_orig_delete, **kwargs):  # noqa: ANN001, ANN003
        sonuc = _orig(self, *args, **kwargs)
        _invalid_et(self)
        return sonuc

    _model.save = _save
    _model.delete = _delete
