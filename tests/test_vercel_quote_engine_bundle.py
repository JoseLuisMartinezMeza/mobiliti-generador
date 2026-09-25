from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE_ENGINE = ROOT / "mobiliti_saas" / "quote_engine"
VERCEL_ENGINE = ROOT / "mobiliti_saas" / "web" / "mobiliti_saas" / "quote_engine"
SOURCE_CONTRACT = (
    ROOT
    / "mobiliti_saas"
    / "worker"
    / "templates"
    / "formato-cotizacion-2026-oficial.contract.json"
)
VERCEL_CONTRACT = (
    ROOT
    / "mobiliti_saas"
    / "web"
    / "mobiliti_saas"
    / "worker"
    / "templates"
    / "formato-cotizacion-2026-oficial.contract.json"
)


def test_vercel_quote_engine_bundle_matches_production_source():
    source_files = {
        path.relative_to(SOURCE_ENGINE)
        for path in SOURCE_ENGINE.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
    }

    assert source_files
    for relative_path in sorted(source_files):
        bundled_path = VERCEL_ENGINE / relative_path
        assert bundled_path.is_file(), f"Falta en Vercel: {relative_path}"
        assert bundled_path.read_bytes() == (SOURCE_ENGINE / relative_path).read_bytes()


def test_vercel_bundle_includes_official_template_contract():
    assert VERCEL_CONTRACT.read_bytes() == SOURCE_CONTRACT.read_bytes()
