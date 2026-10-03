from importlib import import_module

import pytest


def _access_requests():
    try:
        return import_module("apps.platform.access_requests")
    except ModuleNotFoundError as exc:
        if exc.name != "apps.platform.access_requests":
            raise
        pytest.fail("Platform AccessRequest service is not implemented yet.")


def test_access_request_normalizes_purpose_and_defaults_to_one_hour() -> None:
    module = _access_requests()
    request = module.validate_access_request(
        operation="read",
        fields=["tenant.name", "tenant.tax_number"],
        purpose="Şirket yöneticisi vergi kaydını güncellememizi istedi",
    )

    assert request.operation == "read"
    assert request.fields == ("tenant.name", "tenant.tax_number")
    assert request.purpose == "Şirket yöneticisi vergi kaydını güncellememizi istedi"
    assert request.ttl_minutes == module.DEFAULT_ACCESS_TTL_MINUTES == 60


def test_access_request_rejects_unapproved_fields() -> None:
    module = _access_requests()
    with pytest.raises(module.PlatformAccessRequestError):
        module.validate_access_request(
            operation="read",
            fields=["platform.users.password"],
            purpose="Şirket yöneticisi hesap incelemesi istedi",
        )


def test_access_request_rejects_short_or_single_word_purpose() -> None:
    module = _access_requests()
    with pytest.raises(module.PlatformAccessRequestError):
        module.validate_access_request(
            operation="read",
            fields=["tenant.name"],
            purpose="Acil",
        )


def test_access_request_rejects_duration_above_one_hour() -> None:
    module = _access_requests()
    with pytest.raises(module.PlatformAccessRequestError):
        module.validate_access_request(
            operation="update",
            fields=["tenant.legal_name"],
            purpose="Şirket yöneticisi unvan düzeltmesi talep etti",
            ttl_minutes=module.MAX_ACCESS_TTL_MINUTES + 1,
        )
