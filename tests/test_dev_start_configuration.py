from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEV_START_SCRIPT = PROJECT_ROOT / "scripts" / "dev-start.ps1"
ALL_GENERIC_SUPPLIERS = "cr-global,sonara,sunon,alma,lumbro,jome,lauco,idelika,conceptos,labenze,requiez"


def test_dev_start_enables_every_generic_catalog_for_api_and_worker():
    script = DEV_START_SCRIPT.read_text(encoding="utf-8")

    assignment = f'CATALOG_ENABLED_SUPPLIERS = "{ALL_GENERIC_SUPPLIERS}"'
    assert script.count(assignment) == 2


def test_dev_start_points_catalog_assets_at_local_vite_server():
    script = DEV_START_SCRIPT.read_text(encoding="utf-8")

    assert 'MOBILITI_DEV_WEB_BASE_URL = "http://127.0.0.1:$WebPort"' in script


def test_dev_start_reloads_canonical_api_when_quote_sources_change():
    script = DEV_START_SCRIPT.read_text(encoding="utf-8")
    api_arguments = next(
        line for line in script.splitlines()
        if '-Arguments "-m uvicorn index:app' in line
    )

    assert "--reload" in api_arguments
    assert r"--app-dir mobiliti_saas\api" in api_arguments
    assert r"--reload-dir mobiliti_saas\api" in api_arguments
    assert r"--app-dir vercel_deploy\api" not in api_arguments
    assert r"--reload-dir mobiliti_saas\quote_engine" in api_arguments


def test_dev_start_stops_the_previous_uvicorn_process_tree():
    script = DEV_START_SCRIPT.read_text(encoding="utf-8")

    assert "function Stop-ProcessTree" in script
    assert 'CommandLine -match "uvicorn index:app"' in script
    assert 'CommandLine -match "--port $ApiPort"' in script
    assert "Stop-ProcessTree -ProcessId $_.ProcessId" in script
