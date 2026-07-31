"""Registro cerrado de perfiles de plantilla para el motor de cotizaciones."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Final


OFFICIAL_TEMPLATE_PROFILE_ID: Final = "official_2026_gdl"
SUNON_CDMX_TEMPLATE_PROFILE_ID: Final = "sunon_cdmx_v1c"
DEFAULT_TEMPLATE_PROFILE_ID: Final = OFFICIAL_TEMPLATE_PROFILE_ID
OFFICIAL_TEMPLATE_CONTRACT_SHA256: Final = (
    "25f79e3ae533aa8f560be3e80586c19993ea65c0a07c500eb458738f9915b251"
)
SUNON_CDMX_TEMPLATE_CONTRACT_SHA256: Final = (
    "4c150193ce0c59de68f1091741d82b0ddf3fc439c1966ca31f35d92a336a0247"
)


@dataclass(frozen=True, slots=True)
class TemplateProfile:
    """Describe un activo, contrato y compositor permitidos como una unidad."""

    id: str
    display_name: str
    template_path: Path
    contract_path: Path
    template_contract_sha256: str
    composer_variant: str
    aliases: tuple[str, ...] = ()


def _mobiliti_package_root() -> Path:
    """Ubica el paquete raíz tanto en el árbol canónico como en el espejo web."""

    module_path = Path(__file__).resolve()
    fallback: Path | None = None
    for parent in module_path.parents:
        if parent.name != "mobiliti_saas":
            continue
        fallback = fallback or parent
        if parent.parent.name != "web":
            return parent
    if fallback is not None:
        return fallback
    raise RuntimeError("No fue posible ubicar el paquete mobiliti_saas")


_TEMPLATES_DIR: Final = _mobiliti_package_root() / "worker" / "templates"

_OFFICIAL_PROFILE: Final = TemplateProfile(
    id=OFFICIAL_TEMPLATE_PROFILE_ID,
    display_name="Formato Cotización 2026 GDL (1)",
    template_path=_TEMPLATES_DIR / "Formato Cotizacion 2026 Oficial.xlsx",
    contract_path=(
        _TEMPLATES_DIR / "formato-cotizacion-2026-oficial.contract.json"
    ),
    template_contract_sha256=OFFICIAL_TEMPLATE_CONTRACT_SHA256,
    composer_variant="official",
    aliases=(
        "Formato Cotizacion 2026 GDL (1).xlsx",
        "Formato Cotización 2026 GDL (1)",
        "Formato Cotizacion 2026 Oficial.xlsx",
        "Plantilla Corporativa Mobiliti 2025",
    ),
)

_SUNON_CDMX_PROFILE: Final = TemplateProfile(
    id=SUNON_CDMX_TEMPLATE_PROFILE_ID,
    display_name="Formato Cotización Único - Sunon CDMX V1C",
    template_path=_TEMPLATES_DIR / "Formato Cotizacion Sunon CDMX V1C.xlsx",
    contract_path=(
        _TEMPLATES_DIR
        / "formato-cotizacion-sunon-cdmx-v1c.contract.json"
    ),
    template_contract_sha256=SUNON_CDMX_TEMPLATE_CONTRACT_SHA256,
    composer_variant="sunon_cdmx_v1c",
)

_PROFILES: Final = (_OFFICIAL_PROFILE, _SUNON_CDMX_PROFILE)
_PROFILES_BY_ID: Final = {profile.id: profile for profile in _PROFILES}
_ALIASES_TO_ID: Final = {
    alias.strip().casefold(): profile.id
    for profile in _PROFILES
    for alias in (profile.id, *profile.aliases)
}


def available_template_profiles() -> tuple[TemplateProfile, ...]:
    """Devuelve la allowlist inmutable en orden de presentación."""

    return _PROFILES


def lookup_template_profile(value: object | None) -> TemplateProfile:
    """Resuelve solo IDs y aliases conocidos, sin inspeccionar el filesystem."""

    if value is None:
        return _OFFICIAL_PROFILE
    if not isinstance(value, str):
        raise ValueError(f"Plantilla no permitida: {value!r}")

    candidate = value.strip()
    if not candidate:
        return _OFFICIAL_PROFILE
    if ".." in candidate or "/" in candidate or "\\" in candidate:
        raise ValueError(f"Plantilla no permitida: {value!r}")

    profile_id = _ALIASES_TO_ID.get(candidate.casefold())
    if profile_id is None:
        raise ValueError(f"Plantilla no permitida: {value!r}")
    return _PROFILES_BY_ID[profile_id]


def resolve_template_profile(
    value: object | None,
    *,
    require_files: bool = True,
) -> TemplateProfile:
    """Resuelve un perfil permitido y, por defecto, valida ambos archivos."""

    profile = lookup_template_profile(value)
    if not require_files:
        return profile

    missing = [
        path
        for path in (profile.template_path, profile.contract_path)
        if not path.is_file()
    ]
    if missing:
        missing_text = ", ".join(str(path) for path in missing)
        raise FileNotFoundError(
            f"Perfil de plantilla {profile.id!r} incompleto: {missing_text}"
        )
    return profile


__all__ = [
    "DEFAULT_TEMPLATE_PROFILE_ID",
    "OFFICIAL_TEMPLATE_PROFILE_ID",
    "OFFICIAL_TEMPLATE_CONTRACT_SHA256",
    "SUNON_CDMX_TEMPLATE_PROFILE_ID",
    "SUNON_CDMX_TEMPLATE_CONTRACT_SHA256",
    "TemplateProfile",
    "available_template_profiles",
    "lookup_template_profile",
    "resolve_template_profile",
]
