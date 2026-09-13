# P5a V1 — fazla mesai şeması: `hr.overtime_requests`.
#
# Kaynak kararlar: docs/HR_ROADMAP.md §11 (fazla mesai ayrıntılı tasarım) + P5a.
# Bu dilim yalnız **talep + onay temeli**dir:
#   * VAR: çalışan, tarih, saat aralığı, sunucu tarafı `total_minutes`, açıklama,
#     kapalı durum kümesi, karar/iptal alanları, denetim izi.
#   * YOK (bilinçli, ileriki faz): `hr.overtime_rules` (katsayı kuralları),
#     `hr.overtime_entries` (katsayı dilimleri), ücret hesabı (P5b), izin karşılığı
#     (TIME_OFF yazımı), `attendance_record_id` (M15 tablosu henüz yok — §10),
#     `compensation_type_snapshot`/`pay_multiplier_snapshot` (kural motoru çıktısı).
#   * `compensation_type` kolonu bu dilimde **dolmaz** (kural motoru gelene kadar);
#     "ayarlanmamış" değeri boş dizedir (DJ001: string alanlarda `null=True` yok).
#
# Kolon adı sapması: yol haritası §11 `date` der; burada **`work_date`** kullanıldı —
# `date` PostgreSQL'de tip adıdır ve M15 tasarımı da `work_date` kullanır (aynı kavram
# iki modülde aynı adla anılır). Karar veri değil ad düzeyindedir; raporlandı.

import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('organization', '0019_p1a_reason_not_null'),
        ('tenancy', '0009_od25_public_id_columns'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='OvertimeRequest',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('deleted_at', models.DateTimeField(blank=True, null=True)),
                ('work_date', models.DateField(verbose_name='tarih')),
                ('start_time', models.TimeField(verbose_name='başlangıç saati')),
                ('end_time', models.TimeField(verbose_name='bitiş saati')),
                ('total_minutes', models.PositiveIntegerField(help_text='Sunucuda `end_time - start_time` ile hesaplanır; istemciden alınmaz.', verbose_name='toplam süre (dakika)')),
                ('description', models.TextField(help_text="Fazla mesainin gerekçesi (denetim izi; asgari uzunluk DB guard'ı).", verbose_name='açıklama / neden')),
                ('status', models.CharField(choices=[('pending', 'Onay bekliyor'), ('approved', 'Onaylandı'), ('rejected', 'Reddedildi'), ('canceled', 'İptal edildi')], default='pending', max_length=20, verbose_name='durum')),
                ('compensation_type', models.CharField(blank=True, choices=[('pay', 'Ücret'), ('time_off', 'İzin')], default='', help_text='Boş dize = ayarlanmamış (P5b/P5a kural motoru gelene kadar dolmaz; DJ001 gereği `null=True` kullanılmaz).', max_length=20, verbose_name='karşılık türü')),
                ('decided_at', models.DateTimeField(blank=True, null=True, verbose_name='karar zamanı')),
                ('canceled_at', models.DateTimeField(blank=True, null=True, verbose_name='iptal zamanı')),
                ('cancel_reason', models.TextField(blank=True, verbose_name='iptal gerekçesi')),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='created_overtime_requests', to=settings.AUTH_USER_MODEL, verbose_name='kaydı açan')),
                ('decided_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='decided_overtime_requests', to=settings.AUTH_USER_MODEL, verbose_name='karar veren')),
                ('employee', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='overtime_requests', to='organization.employee', verbose_name='çalışan')),
                ('tenant', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='overtime_requests', to='tenancy.tenant', verbose_name='kiracı')),
            ],
            options={
                'verbose_name': 'fazla mesai talebi',
                'verbose_name_plural': 'fazla mesai talepleri',
                'db_table': 'overtime_requests',
                'ordering': ['-work_date', '-start_time'],
                'indexes': [models.Index(fields=['tenant', 'status', 'work_date'], name='overtime_requests_status_idx'), models.Index(fields=['tenant', 'employee', 'work_date'], name='overtime_requests_emp_idx')],
                'constraints': [models.CheckConstraint(condition=models.Q(('end_time__gt', models.F('start_time'))), name='overtime_requests_time_order'), models.CheckConstraint(condition=models.Q(('total_minutes__gt', 0)), name='overtime_requests_minutes_positive'), models.CheckConstraint(condition=models.Q(('total_minutes__lte', 960)), name='overtime_requests_minutes_max'), models.CheckConstraint(condition=models.Q(('status__in', ['pending', 'approved', 'rejected', 'canceled'])), name='overtime_requests_status_valid'), models.CheckConstraint(condition=models.Q(('compensation_type', ''), ('compensation_type__in', ['pay', 'time_off']), _connector='OR'), name='overtime_requests_compensation_valid'), models.CheckConstraint(condition=models.Q(models.Q(('status', 'canceled'), _negated=True), models.Q(('cancel_reason', ''), _negated=True), _connector='OR'), name='overtime_requests_cancel_reason'), models.UniqueConstraint(condition=models.Q(('deleted_at__isnull', True)), fields=('employee', 'work_date', 'start_time', 'end_time'), name='overtime_requests_slot_uniq')],
            },
        ),
    ]
