"""Guard the project's declared license and the notices shipped with it."""

import hashlib
from pathlib import Path
import tomllib


ROOT = Path(__file__).resolve().parents[1]
LICENSE_FILES = {"LICENSE", "NOTICE", "LICENSES/legacy-MIT.txt"}


def test_package_declares_gpl_and_includes_required_notices():
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert config["project"].get("license") == "GPL-3.0-or-later"
    assert set(config["project"].get("license-files", [])) == LICENSE_FILES


def test_license_contains_complete_unmodified_gpl_v3_text():
    # Official https://www.gnu.org/licenses/gpl-3.0.txt, retrieved 2026-10-05.
    text = (ROOT / "LICENSE").read_text(encoding="utf-8").encode()
    assert hashlib.sha256(text).hexdigest() == (
        "3972dc9744f6499f0f9b2dbf76696f2ae7ad8af9b23dde66d6af86c9dfb36986"
    )


def test_original_mit_grant_is_preserved_in_full():
    legacy = ROOT / "LICENSES/legacy-MIT.txt"
    assert legacy.is_file(), "Preserve the original MIT copyright and permission notice"
    # LICENSE at ba2400ce22f1a4c9da4d6f5d2855aee309d7ffd9.
    text = legacy.read_text(encoding="utf-8").encode()
    assert hashlib.sha256(text).hexdigest() == (
        "b8182c12c62d3957ee3c715e0af894c75cf915e1911daebf3789b7618c6b3159"
    )


def test_readme_and_notice_explain_license_scope():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "GPL-3.0-or-later" in readme
    assert "LICENSES/legacy-MIT.txt" in readme
    notice_path = ROOT / "NOTICE"
    assert notice_path.is_file(), "Ship the project grant and third-party scope notice"
    notice = notice_path.read_text(encoding="utf-8")
    assert "either version 3 of the License, or" in notice
    assert "any later version" in notice
    assert "Third-party" in notice
    assert "not revoked" in notice
