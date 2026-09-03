from __future__ import annotations

from pathlib import Path
import shutil

from mobiliti_saas.quote_engine.engine import (
    OFFICIAL_TEMPLATE_CONTRACT_PATH,
    OFFICIAL_TEMPLATE_PATH,
    _contract_path_for_template,
)
from mobiliti_saas.quote_engine.template_profiles import (
    SUNON_CDMX_TEMPLATE_PROFILE_ID,
    resolve_template_profile,
)


def test_registered_cdmx_asset_uses_its_matching_contract() -> None:
    profile = resolve_template_profile(SUNON_CDMX_TEMPLATE_PROFILE_ID)

    assert _contract_path_for_template(profile.template_path) == (
        profile.contract_path.resolve(strict=True)
    )


def test_official_asset_keeps_the_official_contract() -> None:
    assert _contract_path_for_template(OFFICIAL_TEMPLATE_PATH) == (
        OFFICIAL_TEMPLATE_CONTRACT_PATH.resolve(strict=True)
    )


def test_exact_official_copy_keeps_legacy_contract_validation(
    tmp_path: Path,
) -> None:
    copied_template = tmp_path / "official-copy.xlsx"
    shutil.copyfile(OFFICIAL_TEMPLATE_PATH, copied_template)

    assert _contract_path_for_template(copied_template) == (
        OFFICIAL_TEMPLATE_CONTRACT_PATH.resolve(strict=True)
    )


def test_exact_cdmx_copy_keeps_its_registered_contract(
    tmp_path: Path,
) -> None:
    profile = resolve_template_profile(SUNON_CDMX_TEMPLATE_PROFILE_ID)
    copied_template = tmp_path / "cdmx-copy.xlsx"
    shutil.copyfile(profile.template_path, copied_template)

    assert _contract_path_for_template(copied_template) == (
        profile.contract_path.resolve(strict=True)
    )


def test_unregistered_template_cannot_borrow_the_cdmx_contract(
    tmp_path: Path,
) -> None:
    arbitrary_template = tmp_path / "arbitrary.xlsx"
    arbitrary_template.write_bytes(b"not-an-xlsx")

    assert _contract_path_for_template(arbitrary_template) != (
        resolve_template_profile(
            SUNON_CDMX_TEMPLATE_PROFILE_ID,
        ).contract_path.resolve(strict=True)
    )
