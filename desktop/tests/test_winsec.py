import pytest

from komersia_desktop.winsec import SID_SYSTEM, build_icacls_args, parse_whoami_user


def test_parse_whoami():
    out = '"nt authority\\network service","S-1-5-20"\r\n'
    assert parse_whoami_user(out) == "S-1-5-20"
    with pytest.raises(ValueError):
        parse_whoami_user("n'importe quoi")


def test_icacls_args_remove_inheritance_and_grant_only_service_and_system(tmp_path):
    args = build_icacls_args(tmp_path / "f", "S-1-5-20", directory=False)
    assert args[:3] == ["icacls", str(tmp_path / "f"), "/inheritance:r"]
    grants = [a for a in args if a.startswith("*")]
    assert grants == ["*S-1-5-20:F", f"*{SID_SYSTEM}:F"]
    assert "(OI)(CI)F" in build_icacls_args(tmp_path, "S-1-5-20", directory=True)[4]
