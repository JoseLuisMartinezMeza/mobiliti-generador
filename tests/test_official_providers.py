from __future__ import annotations

import json
from pathlib import Path
import subprocess

from mobiliti_saas.quote_engine.quotation_sheets import (
    OFFICIAL_MOBILITI_PROVIDERS,
    official_provider_name,
)
# Se reutiliza la configuracion de la API de ese modulo: importarla antes con otro entorno la deja mal para todos.
from test_quote_jobs_api import _auth_headers, _client, _mock_user

ROOT = Path(__file__).resolve().parents[1]
MODULO = ROOT / "mobiliti_saas" / "web" / "src" / "officialProviders.js"


def _catalogo(monkeypatch) -> dict:
    _mock_user(monkeypatch)
    response = _client().get("/proveedores-oficiales", headers=_auth_headers())
    assert response.status_code == 200
    return response.json()


def test_la_api_entrega_la_lista_de_proveedores_de_la_plantilla(monkeypatch) -> None:
    catalogo = _catalogo(monkeypatch)

    assert catalogo["providers"] == list(OFFICIAL_MOBILITI_PROVIDERS)
    assert {"Sunon Inc", "Jome", "Proveedor Externo"} <= set(catalogo["providers"])
    assert catalogo["aliases"]["sunon technology co ltd"] == "Sunon Inc"


def test_la_web_sugiere_el_mismo_proveedor_oficial_que_el_motor(monkeypatch) -> None:
    catalogo = _catalogo(monkeypatch)
    detectados = ["SUNON TECHNOLOGY CO.,LTD.", "sunon inc", "Yabo - Hotelería", "yabo hoteleria", "JOME", "Alma"]
    script = (
        f"import {{ officialProviderName }} from {json.dumps(MODULO.resolve().as_uri())};\n"
        f"const catalogo = {json.dumps(catalogo)};\n"
        f"console.log(JSON.stringify({json.dumps(detectados + ['gaby  sunon   oct', ''])}"
        ".map((texto) => officialProviderName(texto, catalogo))));"
    )
    completed = subprocess.run(
        ["node", "--input-type=module"], input=script, text=True, encoding="utf-8", capture_output=True, check=False
    )
    assert completed.returncode == 0, completed.stderr
    sugeridos = json.loads(completed.stdout)

    assert sugeridos[: len(detectados)] == [official_provider_name(texto) for texto in detectados]
    # Lo que no se reconoce queda sin elegir: el usuario decide, no se asume "Proveedor Externo".
    assert sugeridos[len(detectados):] == ["", ""]
