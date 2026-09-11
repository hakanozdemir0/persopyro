"""Ortak soyut model temelleri.

Bu modül bir Django uygulaması değildir (somut model içermez, migration üretmez);
yalnızca diğer uygulamaların kullandığı soyut sınıfları barındırır.
"""

import uuid

from django.db import models


class UUIDModel(models.Model):
    """UUID birincil anahtar (taşınabilirlik ve dağıtık üretim kolaylığı)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    class Meta:
        abstract = True


class TimeStampedModel(models.Model):
    """Oluşturma/güncelleme zaman damgaları (UTC, TIMESTAMPTZ)."""

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class SoftDeleteModel(models.Model):
    """
    Yumuşak silme.

    Kayıtlar fiziksel olarak silinmez; `deleted_at` işaretlenir. Böylece denetim
    izi ve ilişkili geçmiş korunur (docs/DATABASE.md bölüm 4).
    """

    deleted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        abstract = True

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None


class TenantScopedModel(UUIDModel, TimeStampedModel, SoftDeleteModel):
    """
    Kiracıya bağlı (tenant-scoped) iş nesnesi.

    Bu temeli kullanan her tablo:
      * `tenant_id` sütunu taşır (zorunlu),
      * veritabanında RLS (FORCE) ile korunur,
      * kiracı bağlamı olmadan hiçbir satır döndürmez.

    RLS'in etkinleştirilmesi migration içinde yapılır (ham SQL) ve bir testle
    doğrulanır: her yeni tenant-scoped tablo bu denetimden geçmelidir.
    """

    class Meta:
        abstract = True
