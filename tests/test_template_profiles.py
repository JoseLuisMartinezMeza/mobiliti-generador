import json
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "mobiliti_saas" / "worker" / "templates"
OFFICIAL_TEMPLATE = TEMPLATES / "Formato Cotizacion 2026 Oficial.xlsx"
OFFICIAL_CONTRACT = (
    TEMPLATES / "formato-cotizacion-2026-oficial.contract.json"
)
CDMX_TEMPLATE = TEMPLATES / "Formato Cotizacion Sunon CDMX V1C.xlsx"
CDMX_CONTRACT = (
    TEMPLATES / "formato-cotizacion-sunon-cdmx-v1c.contract.json"
)


def _canonical_module():
    from mobiliti_saas.quote_engine import template_profiles

    return template_profiles


def _web_module():
    from mobiliti_saas.web.mobiliti_saas.quote_engine import template_profiles

    return template_profiles


def _profile_signature(profile) -> tuple:
    return (
        profile.id,
        profile.display_name,
        profile.template_path,
        profile.contract_path,
        profile.template_contract_sha256,
        profile.composer_variant,
        profile.aliases,
    )


def test_official_profile_resolves_to_promoted_asset_and_contract() -> None:
    module = _canonical_module()

    profile = module.resolve_template_profile("official_2026_gdl")

    assert profile.id == "official_2026_gdl"
    assert profile.display_name == "Formato Cotización 2026 GDL (1)"
    assert profile.template_path == OFFICIAL_TEMPLATE
    assert profile.contract_path == OFFICIAL_CONTRACT
    assert (
        profile.template_contract_sha256
        == module.OFFICIAL_TEMPLATE_CONTRACT_SHA256
    )
    assert profile.composer_variant == "official"


@pytest.mark.parametrize(
    "legacy_value",
    [
        None,
        "",
        "   ",
        "Formato Cotizacion 2026 GDL (1).xlsx",
        "Formato Cotización 2026 GDL (1)",
        "Formato Cotizacion 2026 Oficial.xlsx",
        "Plantilla Corporativa Mobiliti 2025",
    ],
)
def test_legacy_values_resolve_to_official_profile(legacy_value) -> None:
    module = _canonical_module()

    profile = module.resolve_template_profile(legacy_value)

    assert profile.id == module.DEFAULT_TEMPLATE_PROFILE_ID
    assert profile.id == "official_2026_gdl"


def test_cdmx_profile_metadata_is_independent_before_asset_is_built() -> None:
    module = _canonical_module()

    profile = module.resolve_template_profile(
        "sunon_cdmx_v1c",
        require_files=False,
    )

    assert profile.id == "sunon_cdmx_v1c"
    assert profile.display_name == "Formato Cotización Único - Sunon CDMX V1C"
    assert profile.template_path == CDMX_TEMPLATE
    assert profile.contract_path == CDMX_CONTRACT
    assert (
        profile.template_contract_sha256
        == module.SUNON_CDMX_TEMPLATE_CONTRACT_SHA256
    )
    assert profile.composer_variant == "sunon_cdmx_v1c"
    assert profile.template_path != OFFICIAL_TEMPLATE
    assert profile.contract_path != OFFICIAL_CONTRACT


@pytest.mark.parametrize(
    "profile_id",
    ["official_2026_gdl", "sunon_cdmx_v1c"],
)
def test_profile_contract_hash_matches_registered_contract(profile_id) -> None:
    module = _canonical_module()
    profile = module.resolve_template_profile(profile_id)
    contract = json.loads(profile.contract_path.read_text(encoding="utf-8"))

    assert profile.template_contract_sha256 == contract["sha256"]


def test_checked_resolution_requires_asset_and_contract(monkeypatch) -> None:
    module = _canonical_module()
    monkeypatch.setattr(Path, "is_file", lambda _path: False)

    with pytest.raises(FileNotFoundError, match="sunon_cdmx_v1c"):
        module.resolve_template_profile("sunon_cdmx_v1c")


@pytest.mark.parametrize(
    "untrusted_value",
    [
        "desconocida",
        "otra-plantilla.xlsx",
        "../Formato Cotizacion 2026 Oficial.xlsx",
        r"..\Formato Cotizacion 2026 Oficial.xlsx",
        r"C:\temp\plantilla.xlsx",
        "/tmp/plantilla.xlsx",
        "worker/templates/Formato Cotizacion 2026 Oficial.xlsx",
    ],
)
def test_closed_resolver_rejects_unknown_ids_and_paths(untrusted_value) -> None:
    module = _canonical_module()

    with pytest.raises(ValueError, match="Plantilla no permitida"):
        module.resolve_template_profile(untrusted_value, require_files=False)


def test_template_profile_is_immutable() -> None:
    module = _canonical_module()
    profile = module.resolve_template_profile(
        "sunon_cdmx_v1c",
        require_files=False,
    )

    with pytest.raises(FrozenInstanceError):
        profile.id = "otro"


def test_available_profiles_exposes_only_canonical_allowlist() -> None:
    module = _canonical_module()

    profiles = module.available_template_profiles()

    assert isinstance(profiles, tuple)
    assert [profile.id for profile in profiles] == [
        "official_2026_gdl",
        "sunon_cdmx_v1c",
    ]


def test_canonical_and_web_mirrors_are_semantically_equivalent() -> None:
    canonical = _canonical_module()
    web = _web_module()

    assert canonical.DEFAULT_TEMPLATE_PROFILE_ID == web.DEFAULT_TEMPLATE_PROFILE_ID
    assert [
        _profile_signature(profile)
        for profile in canonical.available_template_profiles()
    ] == [
        _profile_signature(profile)
        for profile in web.available_template_profiles()
    ]

    for value in (
        None,
        "Formato Cotizacion 2026 GDL (1).xlsx",
        "sunon_cdmx_v1c",
    ):
        canonical_profile = canonical.resolve_template_profile(
            value,
            require_files=False,
        )
        web_profile = web.resolve_template_profile(
            value,
            require_files=False,
        )
        assert _profile_signature(canonical_profile) == _profile_signature(
            web_profile
        )
