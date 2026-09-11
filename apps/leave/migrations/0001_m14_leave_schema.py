# M14 (docs/DATABASE.md) — izin (leave) çekirdek şeması.
#
# Oluşturulan tablolar (hepsi `tenant_id` + RLS + FORCE hedefli):
#   * `hr.leave_types`          — kiracı bazlı izin türleri (sistem kodları rezerve)
#   * `hr.leave_policies`       — tür başına **tek** yaşayan hakediş politikası
#   * `hr.leave_policy_rules`   — kıdeme göre hakediş kuralları ([min, max) aralığı)
#   * `hr.leave_balances`       — çalışan/yıl/tür bakiyesi (`available_days` ÜRETİLEN kolon)
#   * `hr.leave_balance_entries`— append-only olay defteri (hakediş/rezerv/kullanım/iade/düzeltme)
#   * `hr.leave_requests`       — izin talebi (sunucu tarafı gün hesabı, belge referansı)
#   * `hr.approval_steps`       — **genel** onay adımları (`subject_kind` + `subject_id`)
#
# Kapalı kümeler ve kısmi benzersizlikler burada (model `Meta`); döngü/sıra/bakiye
# guard'ları `0002_m14_leave_guards`, RLS `0003_m14_rls_leave`, kiracı guard'ları
# `0004_m14_leave_same_tenant_guards` içindedir.
#

import django.db.models.deletion
import django.db.models.expressions
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('organization', '0009_org_status_checks'),
        ('tenancy', '0009_od25_public_id_columns'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='LeaveBalance',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('deleted_at', models.DateTimeField(blank=True, null=True)),
                ('period_year', models.PositiveSmallIntegerField(verbose_name='dönem (yıl)')),
                ('entitled_days', models.DecimalField(decimal_places=1, default=0, max_digits=6, verbose_name='hakediş')),
                ('carried_forward_days', models.DecimalField(decimal_places=1, default=0, max_digits=6, verbose_name='devreden')),
                ('adjustment_days', models.DecimalField(decimal_places=1, default=0, max_digits=6, verbose_name='düzeltme')),
                ('used_days', models.DecimalField(decimal_places=1, default=0, max_digits=6, verbose_name='kullanılan')),
                ('reserved_days', models.DecimalField(decimal_places=1, default=0, max_digits=6, verbose_name='rezerve (onay bekleyen)')),
                ('available_days', models.GeneratedField(db_persist=True, expression=django.db.models.expressions.CombinedExpression(django.db.models.expressions.CombinedExpression(django.db.models.expressions.CombinedExpression(django.db.models.expressions.CombinedExpression(models.F('entitled_days'), '+', models.F('carried_forward_days')), '+', models.F('adjustment_days')), '-', models.F('used_days')), '-', models.F('reserved_days')), output_field=models.DecimalField(decimal_places=1, max_digits=6))),
                ('employee', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='leave_balances', to='organization.employee', verbose_name='çalışan')),
                ('tenant', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='leave_balances', to='tenancy.tenant', verbose_name='kiracı')),
            ],
            options={
                'verbose_name': 'izin bakiyesi',
                'verbose_name_plural': 'izin bakiyeleri',
                'db_table': 'leave_balances',
                'ordering': ['-period_year'],
            },
        ),
        migrations.CreateModel(
            name='LeaveType',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('deleted_at', models.DateTimeField(blank=True, null=True)),
                ('code', models.CharField(max_length=60, verbose_name='kod')),
                ('name', models.CharField(max_length=120, verbose_name='ad')),
                ('category', models.CharField(choices=[('annual', 'Yıllık'), ('sick', 'Hastalık'), ('unpaid', 'Ücretsiz'), ('maternity', 'Doğum'), ('paternity', 'Babalık'), ('bereavement', 'Ölüm/İzin'), ('other', 'Diğer')], default='other', max_length=40, verbose_name='kategori')),
                ('is_paid', models.BooleanField(default=True, verbose_name='ücretli')),
                ('uses_balance', models.BooleanField(default=False, help_text='True ise hakediş/bakiye (leave_balances) gerekir.', verbose_name='bakiye kullanır')),
                ('requires_approval', models.BooleanField(default=True, verbose_name='onay gerektirir')),
                ('requires_document', models.BooleanField(default=False, verbose_name='belge gerektirir')),
                ('min_advance_notice_days', models.PositiveIntegerField(default=0, verbose_name='asgari ön bildirim (gün)')),
                ('max_consecutive_days', models.DecimalField(blank=True, decimal_places=1, max_digits=6, null=True, verbose_name='azami kesintisiz gün')),
                ('is_system', models.BooleanField(default=False, help_text='Kod rezervedir: değiştirilemez, silinemez (yalnızca pasifleştirilir).', verbose_name='sistem türü')),
                ('status', models.CharField(choices=[('active', 'Aktif'), ('inactive', 'Pasif'), ('archived', 'Arşiv')], default='active', max_length=20, verbose_name='durum')),
                ('tenant', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='leave_types', to='tenancy.tenant', verbose_name='kiracı')),
            ],
            options={
                'verbose_name': 'izin türü',
                'verbose_name_plural': 'izin türleri',
                'db_table': 'leave_types',
                'ordering': ['code'],
            },
        ),
        migrations.CreateModel(
            name='LeaveRequest',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('deleted_at', models.DateTimeField(blank=True, null=True)),
                ('start_date', models.DateField(verbose_name='başlangıç')),
                ('end_date', models.DateField(verbose_name='bitiş')),
                ('start_time', models.TimeField(blank=True, null=True, verbose_name='başlangıç saati')),
                ('end_time', models.TimeField(blank=True, null=True, verbose_name='bitiş saati')),
                ('is_half_day', models.BooleanField(default=False, verbose_name='yarım gün')),
                ('days_requested', models.DecimalField(decimal_places=1, max_digits=6, verbose_name='istenen gün')),
                ('reason', models.TextField(blank=True, verbose_name='gerekçe')),
                ('status', models.CharField(choices=[('draft', 'Taslak'), ('pending', 'Onay bekliyor'), ('approved', 'Onaylandı'), ('rejected', 'Reddedildi'), ('canceled', 'İptal edildi'), ('taken', 'Kullanıldı')], default='draft', max_length=20, verbose_name='durum')),
                ('submitted_at', models.DateTimeField(blank=True, null=True, verbose_name='onaya sunulma')),
                ('decided_at', models.DateTimeField(blank=True, null=True, verbose_name='karar zamanı')),
                ('canceled_at', models.DateTimeField(blank=True, null=True, verbose_name='iptal zamanı')),
                ('cancel_reason', models.TextField(blank=True, verbose_name='iptal gerekçesi')),
                ('document_ref', models.UUIDField(blank=True, help_text='M16 `hr.documents` entegrasyon noktası — belge modülü gelince gerçek FK bağlanır.', null=True, verbose_name='belge referansı')),
                ('decided_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='decided_leave_requests', to=settings.AUTH_USER_MODEL, verbose_name='karar veren')),
                ('employee', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='leave_requests', to='organization.employee', verbose_name='çalışan')),
                ('tenant', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='leave_requests', to='tenancy.tenant', verbose_name='kiracı')),
                ('leave_type', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='requests', to='leave.leavetype', verbose_name='izin türü')),
            ],
            options={
                'verbose_name': 'izin talebi',
                'verbose_name_plural': 'izin talepleri',
                'db_table': 'leave_requests',
                'ordering': ['-start_date'],
            },
        ),
        migrations.CreateModel(
            name='LeavePolicy',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('deleted_at', models.DateTimeField(blank=True, null=True)),
                ('accrual_method', models.CharField(choices=[('fixed', 'Sabit (yılda sabit gün)'), ('tenure_based', 'Kıdeme göre (hizmet süresi kuralı)')], default='fixed', max_length=20, verbose_name='hakediş yöntemi')),
                ('fixed_days', models.DecimalField(blank=True, decimal_places=1, max_digits=5, null=True, verbose_name='sabit gün (yıllık)')),
                ('allow_negative_balance', models.BooleanField(default=False, help_text="True değilse negatif bakiye DB guard'ı ile reddedilir.", verbose_name='negatif bakiye izni')),
                ('allow_refund_after_approval', models.BooleanField(default=True, help_text='Onaylanmış talebin iptalinde bakiyenin geri yüklenmesine izin verir (M14 §12).', verbose_name='onay sonrası iade')),
                ('status', models.CharField(choices=[('active', 'Aktif'), ('inactive', 'Pasif'), ('archived', 'Arşiv')], default='active', max_length=20, verbose_name='durum')),
                ('tenant', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='leave_policies', to='tenancy.tenant', verbose_name='kiracı')),
                ('leave_type', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='policies', to='leave.leavetype', verbose_name='izin türü')),
            ],
            options={
                'verbose_name': 'izin politikası',
                'verbose_name_plural': 'izin politikaları',
                'db_table': 'leave_policies',
            },
        ),
        migrations.CreateModel(
            name='LeaveBalanceEntry',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('period_year', models.PositiveSmallIntegerField(verbose_name='dönem (yıl)')),
                ('entry_kind', models.CharField(choices=[('accrual', 'Hakediş'), ('carryover', 'Devreden'), ('adjustment', 'Manuel düzeltme'), ('reservation', 'Rezervasyon (onay bekliyor)'), ('reservation_release', 'Rezervasyon iptali'), ('usage', 'Kullanım (onaylandı)'), ('refund', 'İade (iptal)')], max_length=20, verbose_name='olay türü')),
                ('delta_days', models.DecimalField(decimal_places=1, max_digits=6, verbose_name='değişim (gün)')),
                ('reason', models.TextField(blank=True, verbose_name='gerekçe')),
                ('actor', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='leave_balance_entries', to=settings.AUTH_USER_MODEL, verbose_name='işlemi yapan')),
                ('balance', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='entries', to='leave.leavebalance', verbose_name='bakiye')),
                ('employee', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='leave_balance_entries', to='organization.employee', verbose_name='çalışan')),
                ('tenant', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='leave_balance_entries', to='tenancy.tenant', verbose_name='kiracı')),
                ('leave_request', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='balance_entries', to='leave.leaverequest', verbose_name='izin talebi')),
                ('leave_type', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='balance_entries', to='leave.leavetype', verbose_name='izin türü')),
            ],
            options={
                'verbose_name': 'bakiye olay kaydı',
                'verbose_name_plural': 'bakiye olay kayıtları',
                'db_table': 'leave_balance_entries',
                'ordering': ['created_at'],
            },
        ),
        migrations.AddField(
            model_name='leavebalance',
            name='leave_type',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='balances', to='leave.leavetype', verbose_name='izin türü'),
        ),
        migrations.CreateModel(
            name='ApprovalStep',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('deleted_at', models.DateTimeField(blank=True, null=True)),
                ('subject_kind', models.CharField(choices=[('leave_request', 'İzin talebi')], default='leave_request', max_length=30, verbose_name='konu türü')),
                ('subject_id', models.UUIDField(verbose_name='konu kimliği')),
                ('step_no', models.PositiveSmallIntegerField(verbose_name='adım')),
                ('approver_role_code', models.CharField(blank=True, max_length=60, verbose_name='onaycı rol kodu')),
                ('status', models.CharField(choices=[('pending', 'Karar bekliyor'), ('approved', 'Onaylandı'), ('rejected', 'Reddedildi'), ('skipped', 'Atlandı'), ('canceled', 'İptal edildi')], default='pending', max_length=20, verbose_name='durum')),
                ('decided_at', models.DateTimeField(blank=True, null=True, verbose_name='karar zamanı')),
                ('decision_reason', models.TextField(blank=True, verbose_name='karar gerekçesi')),
                ('approver_user', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='approval_steps', to=settings.AUTH_USER_MODEL, verbose_name='onaycı (kişi)')),
                ('delegated_from', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL, verbose_name='devreden')),
                ('tenant', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='approval_steps', to='tenancy.tenant', verbose_name='kiracı')),
            ],
            options={
                'verbose_name': 'onay adımı',
                'verbose_name_plural': 'onay adımları',
                'db_table': 'approval_steps',
                'ordering': ['subject_kind', 'subject_id', 'step_no'],
                'indexes': [models.Index(fields=['tenant', 'approver_user', 'status'], name='approval_steps_approver_idx'), models.Index(fields=['subject_kind', 'subject_id'], name='approval_steps_subject_idx')],
                'constraints': [models.UniqueConstraint(condition=models.Q(('deleted_at__isnull', True)), fields=('subject_kind', 'subject_id', 'step_no'), name='approval_steps_subject_step_uniq'), models.CheckConstraint(condition=models.Q(('approver_user__isnull', False), models.Q(('approver_role_code', ''), _negated=True), _connector='OR'), name='approval_steps_approver_present'), models.CheckConstraint(condition=models.Q(('status__in', ['pending', 'approved', 'rejected', 'skipped', 'canceled'])), name='approval_steps_status_valid')],
            },
        ),
        migrations.CreateModel(
            name='LeavePolicyRule',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('deleted_at', models.DateTimeField(blank=True, null=True)),
                ('min_service_months', models.PositiveIntegerField(default=0, verbose_name='asgari hizmet (ay)')),
                ('max_service_months', models.PositiveIntegerField(blank=True, help_text='Boş ise üst sınır yoktur (açık uçlu kural).', null=True, verbose_name='azami hizmet (ay)')),
                ('entitlement_days', models.DecimalField(decimal_places=1, max_digits=5, verbose_name='hakediş (gün)')),
                ('policy', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='rules', to='leave.leavepolicy', verbose_name='politika')),
                ('tenant', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='leave_policy_rules', to='tenancy.tenant', verbose_name='kiracı')),
            ],
            options={
                'verbose_name': 'izin politikası kuralı',
                'verbose_name_plural': 'izin politikası kuralları',
                'db_table': 'leave_policy_rules',
                'ordering': ['min_service_months'],
                'indexes': [models.Index(fields=['policy', 'min_service_months'], name='leave_policy_rules_policy_idx')],
                'constraints': [models.CheckConstraint(condition=models.Q(('max_service_months__isnull', True), ('max_service_months__gt', models.F('min_service_months')), _connector='OR'), name='leave_policy_rules_range_valid'), models.CheckConstraint(condition=models.Q(('entitlement_days__gte', 0)), name='leave_policy_rules_days_nonnegative')],
            },
        ),
        migrations.AddIndex(
            model_name='leavetype',
            index=models.Index(fields=['tenant', 'status'], name='leave_types_tenant_status_idx'),
        ),
        migrations.AddConstraint(
            model_name='leavetype',
            constraint=models.UniqueConstraint(condition=models.Q(('deleted_at__isnull', True)), fields=('tenant', 'code'), name='leave_types_tenant_code_uniq'),
        ),
        migrations.AddConstraint(
            model_name='leavetype',
            constraint=models.CheckConstraint(condition=models.Q(('status__in', ['active', 'inactive', 'archived'])), name='leave_types_status_valid'),
        ),
        migrations.AddConstraint(
            model_name='leavetype',
            constraint=models.CheckConstraint(condition=models.Q(('max_consecutive_days__isnull', True), ('max_consecutive_days__gt', 0), _connector='OR'), name='leave_types_max_consecutive_positive'),
        ),
        migrations.AddIndex(
            model_name='leaverequest',
            index=models.Index(fields=['tenant', 'status', 'start_date'], name='leave_requests_status_idx'),
        ),
        migrations.AddIndex(
            model_name='leaverequest',
            index=models.Index(fields=['tenant', 'employee', 'start_date'], name='leave_requests_tenant_emp_idx'),
        ),
        migrations.AddConstraint(
            model_name='leaverequest',
            constraint=models.CheckConstraint(condition=models.Q(('end_date__gte', models.F('start_date'))), name='leave_requests_date_order'),
        ),
        migrations.AddConstraint(
            model_name='leaverequest',
            constraint=models.CheckConstraint(condition=models.Q(('days_requested__gt', 0)), name='leave_requests_days_positive'),
        ),
        migrations.AddConstraint(
            model_name='leaverequest',
            constraint=models.CheckConstraint(condition=models.Q(models.Q(('is_half_day', True), _negated=True), ('start_date', models.F('end_date')), _connector='OR'), name='leave_requests_half_day_single_date'),
        ),
        migrations.AddConstraint(
            model_name='leaverequest',
            constraint=models.CheckConstraint(condition=models.Q(('status__in', ['draft', 'pending', 'approved', 'rejected', 'canceled', 'taken'])), name='leave_requests_status_valid'),
        ),
        migrations.AddConstraint(
            model_name='leavepolicy',
            constraint=models.UniqueConstraint(condition=models.Q(('deleted_at__isnull', True)), fields=('tenant', 'leave_type'), name='leave_policies_tenant_type_uniq'),
        ),
        migrations.AddConstraint(
            model_name='leavepolicy',
            constraint=models.CheckConstraint(condition=models.Q(('accrual_method__in', ['fixed', 'tenure_based'])), name='leave_policies_method_valid'),
        ),
        migrations.AddConstraint(
            model_name='leavepolicy',
            constraint=models.CheckConstraint(condition=models.Q(models.Q(('accrual_method', 'fixed'), ('fixed_days__isnull', False)), models.Q(('accrual_method', 'tenure_based'), ('fixed_days__isnull', True)), _connector='OR'), name='leave_policies_fixed_days_consistency'),
        ),
        migrations.AddConstraint(
            model_name='leavepolicy',
            constraint=models.CheckConstraint(condition=models.Q(('fixed_days__isnull', True), ('fixed_days__gte', 0), _connector='OR'), name='leave_policies_fixed_days_nonnegative'),
        ),
        migrations.AddConstraint(
            model_name='leavepolicy',
            constraint=models.CheckConstraint(condition=models.Q(('status__in', ['active', 'inactive', 'archived'])), name='leave_policies_status_valid'),
        ),
        migrations.AddIndex(
            model_name='leavebalanceentry',
            index=models.Index(fields=['tenant', 'employee', 'period_year'], name='leave_balance_entries_emp_idx'),
        ),
        migrations.AddConstraint(
            model_name='leavebalanceentry',
            constraint=models.CheckConstraint(condition=models.Q(('entry_kind__in', ['accrual', 'carryover', 'adjustment', 'reservation', 'reservation_release', 'usage', 'refund'])), name='leave_balance_entries_kind_valid'),
        ),
        migrations.AddConstraint(
            model_name='leavebalanceentry',
            constraint=models.CheckConstraint(condition=models.Q(models.Q(('entry_kind', 'adjustment'), _negated=True), models.Q(('reason', ''), _negated=True), _connector='OR'), name='leave_balance_entries_adjustment_reason'),
        ),
        migrations.AddConstraint(
            model_name='leavebalanceentry',
            constraint=models.UniqueConstraint(condition=models.Q(('entry_kind', 'accrual')), fields=('balance', 'entry_kind', 'period_year'), name='leave_balance_entries_accrual_uniq'),
        ),
        migrations.AddConstraint(
            model_name='leavebalanceentry',
            constraint=models.UniqueConstraint(condition=models.Q(('leave_request__isnull', False)), fields=('leave_request', 'entry_kind'), name='leave_balance_entries_request_uniq'),
        ),
        migrations.AddIndex(
            model_name='leavebalance',
            index=models.Index(fields=['tenant', 'employee', 'period_year'], name='leave_balances_tenant_emp_idx'),
        ),
        migrations.AddConstraint(
            model_name='leavebalance',
            constraint=models.UniqueConstraint(condition=models.Q(('deleted_at__isnull', True)), fields=('employee', 'leave_type', 'period_year'), name='leave_balances_employee_type_period_uniq'),
        ),
        migrations.AddConstraint(
            model_name='leavebalance',
            constraint=models.CheckConstraint(condition=models.Q(('entitled_days__gte', 0), ('carried_forward_days__gte', 0), ('used_days__gte', 0), ('reserved_days__gte', 0)), name='leave_balances_components_nonnegative'),
        ),
    ]
