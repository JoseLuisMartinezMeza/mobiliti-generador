from pathlib import Path


def test_quote_form_recommends_dezgo_by_default():
    source = Path("mobiliti_saas/web/src/main.jsx").read_text(encoding="utf-8")

    assert 'image_provider: "dezgo"' in source
    assert 'descuento: "40"' in source
    assert 'label="Numero de cotizacion"' in source
    assert 'readOnly' in source
    assert 'placeholder="Automatico por usuario"' in source
    assert 'label="Descuento (%)"' in source
    assert 'max="100"' in source
    assert 'image_prompt: DEFAULT_IMAGE_PROMPT' in source
    assert 'const DEFAULT_IMAGE_PROMPT = "Mejora la calidad de imagen y que este en fondo blanco";' in source
    assert "IA Dezgo recomendado - genera faltantes realistas" in source
    assert "Local sin IA - no inventa imagenes faltantes" in source
    assert "Prompt para imagenes" in source
    assert "MAX_QUOTE_INPUT_MB = 25" in source
    assert "El archivo supera el limite" in source


def test_download_and_generation_timers_are_visible():
    source = Path("mobiliti_saas/web/src/main.jsx").read_text(encoding="utf-8")
    styles = Path("mobiliti_saas/web/src/styles.css").read_text(encoding="utf-8")

    assert "function JobDuration" in source
    assert "function DownloadButton" in source
    assert "function DownloadStatusLine" in source
    assert "Preparando descarga..." in source
    assert "Descargando ${elapsed}" in source
    assert "Estimado aprox." in source
    assert "Faltan aprox." in source
    assert "Tardo ${formatDuration(elapsed)}" in source
    assert ".job-duration" in styles
    assert ".download-downloading" in styles
    assert ".download-line.active" in styles


def test_expired_session_is_cleared_instead_of_showing_raw_token_error():
    source = Path("mobiliti_saas/web/src/main.jsx").read_text(encoding="utf-8")
    styles = Path("mobiliti_saas/web/src/styles.css").read_text(encoding="utf-8")
    api = Path("mobiliti_saas/web/api/index.py").read_text(encoding="utf-8")

    assert 'const AUTH_EXPIRED_EVENT = "mobiliti:auth-expired";' in source
    assert "Tu sesion expiro. Vuelve a iniciar sesion" in source
    assert "window.dispatchEvent(new CustomEvent(AUTH_EXPIRED_EVENT))" in source
    assert "localStorage.removeItem(\"mobiliti_session\")" in source
    assert "notice-line" in source
    assert ".notice-line" in styles
    assert 'os.environ.get("ACCESS_TOKEN_EXPIRE_MINUTES", "720")' in api


def test_history_exposes_delete_quote_action():
    source = Path("mobiliti_saas/web/src/main.jsx").read_text(encoding="utf-8")
    styles = Path("mobiliti_saas/web/src/styles.css").read_text(encoding="utf-8")

    assert "Trash2" in source
    assert "async function deleteJob" in source
    assert 'method: "DELETE"' in source
    assert "Eliminar" in source
    assert ".danger-action" in styles


def test_download_does_not_mutate_signed_url_query():
    source = Path("mobiliti_saas/web/src/main.jsx").read_text(encoding="utf-8")

    assert "withDownloadFilename" not in source
    assert "searchParams.set(\"download\"" not in source
    assert "download=${encodeURIComponent" not in source
    assert "const filename = data.filename || quoteDownloadFallbackName(job);" in source
    assert "link.href = signedUrl;" in source
