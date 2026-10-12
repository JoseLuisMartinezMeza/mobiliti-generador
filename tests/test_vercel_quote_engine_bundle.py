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


def test_vercel_bundle_includes_the_quotation_importer():
    # La API convierte y combina Quotations al importar a un Proyecto; Vercel sólo sube mobiliti_saas/web.
    fuente = ROOT / "pdf_quotation_import"
    modulos = sorted(path.name for path in fuente.glob("*.py"))

    assert {"combinar.py", "converter.py", "llm_fallback.py"} <= set(modulos)
    for nombre in modulos:
        copia = ROOT / "mobiliti_saas" / "web" / "pdf_quotation_import" / nombre
        assert copia.is_file(), f"Falta en Vercel: pdf_quotation_import/{nombre}"
        assert copia.read_bytes() == (fuente / nombre).read_bytes()

    requisitos = (ROOT / "mobiliti_saas" / "web" / "requirements.txt").read_text(encoding="utf-8").splitlines()
    assert "PyMuPDF>=1.27.0" in requisitos
    assert "anthropic>=0.100,<1" in requisitos
