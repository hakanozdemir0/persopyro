"""Tenant-specific policy for employee photos and documents."""

from pathlib import Path

from apps.organization.models import Employee, EmployeeDocument

DEFAULT_FILE_MAX_MB = 10
DEFAULT_FILE_EXTENSIONS = (
    "pdf",
    "jpg",
    "jpeg",
    "png",
    "webp",
    "heic",
    "heif",
    "doc",
    "docx",
    "rtf",
    "xls",
    "xlsx",
    "csv",
    "txt",
    "odt",
    "ods",
    "ppt",
    "pptx",
    "odp",
)
FILE_EXTENSION_CHOICES = tuple((value, value.upper()) for value in DEFAULT_FILE_EXTENSIONS)


def file_policy(settings_value):
    settings_value = settings_value or {}
    try:
        max_mb = float(settings_value.get("employee_storage_max_mb", DEFAULT_FILE_MAX_MB))
    except (TypeError, ValueError):
        max_mb = DEFAULT_FILE_MAX_MB
    selected = settings_value.get("employee_file_extensions", DEFAULT_FILE_EXTENSIONS)
    if not isinstance(selected, (list, tuple)):
        selected = DEFAULT_FILE_EXTENSIONS
    extensions = {str(value).lower().lstrip(".") for value in selected}
    extensions.intersection_update(DEFAULT_FILE_EXTENSIONS)
    return max_mb, extensions


def tenant_file_usage_bytes(tenant_id):
    """Count live employee file bytes using the configured storage backend."""
    used = 0
    for document in EmployeeDocument.objects.filter(
        tenant_id=tenant_id, deleted_at__isnull=True
    ).only("file"):
        if document.file:
            try:
                used += document.file.storage.size(document.file.name)
            except (OSError, NotImplementedError):
                pass
    for employee in (
        Employee.objects.filter(tenant_id=tenant_id, deleted_at__isnull=True)
        .exclude(profile_photo="")
        .only("profile_photo")
    ):
        if employee.profile_photo:
            try:
                used += employee.profile_photo.storage.size(employee.profile_photo.name)
            except (OSError, NotImplementedError):
                pass
    return used


def employee_file_usage_bytes(employee):
    used = 0
    for document in EmployeeDocument.objects.filter(
        employee=employee, deleted_at__isnull=True
    ).only("file"):
        if document.file:
            try:
                used += document.file.storage.size(document.file.name)
            except (OSError, NotImplementedError):
                pass
    if employee.profile_photo:
        try:
            used += employee.profile_photo.storage.size(employee.profile_photo.name)
        except (OSError, NotImplementedError):
            pass
    return used


def uploaded_extension(uploaded):
    return Path(uploaded.name).suffix.lower().lstrip(".")
