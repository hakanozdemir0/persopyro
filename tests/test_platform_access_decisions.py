from pathlib import Path

from django.conf import settings


def test_company_profile_edits_need_no_extra_approval_and_email_is_optional() -> None:
    document = (Path(settings.BASE_DIR) / "docs" / "AUTHORIZATION.md").read_text(
        encoding="utf-8"
    )
    od15 = document.split("### OD15", maxsplit=1)[1].split("### OD16", maxsplit=1)[0]
    od18 = document.split("### OD18", maxsplit=1)[1].split("### OD19", maxsplit=1)[0]
    od15 = " ".join(od15.lower().split())

    assert "güncellendi" in od15
    assert "ikinci platform onayı yok" in od15
    assert "e-posta" in od15
    assert "limitleri" in od15
    assert "isterse" in od15
    assert "varsayılan olarak mail gönderilmez" in od15
    assert "KAPALI" in od18
    assert "şirket yetkilisinin ayrıca onayı aranmaz" in od18.lower()
    assert "iki farklı platform kullanıcısının onayı" in od18.lower()
