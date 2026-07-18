from __future__ import annotations

from urllib.parse import urlsplit


CR_GLOBAL_SPEC_SHA256 = "25b2f1984b2666d0fa004527a271f097cba56683f233d6b905e09fcb0716ff9b"
_OFFICIAL_HOST = "www.crglobal.mx"
_EXACT_PRODUCT_LINKS = {
    "CABLE CHAIN CHEAPER": "https://www.crglobal.mx/product-page/vertebra-cheaper",
    "CR-AB16060": "https://www.crglobal.mx/product-page/estructura-abatible-1",
    "CR-CA16050": "https://www.crglobal.mx/product-page/mesa-de-capacitaci%C3%B3n-con-fald%C3%B3n-1",
    "CR-MA01N": "https://www.crglobal.mx/product-page/mesa-auxiliar-m%C3%B3vil",
    "CR-QM34140": "https://www.crglobal.mx/product-page/mesa-abatible-1-40",
    "CR-QM34160": "https://www.crglobal.mx/product-page/mesa-abatible-1-60",
    "CR-STVM806": "https://www.crglobal.mx/product-page/soporte-movil-grande-806",
    "CR-STVP": "https://www.crglobal.mx/product-page/soporte-movil-peque%C3%B1o",
    "CR33-2A2": "https://www.crglobal.mx/product-page/estructura-heavy-duty",
    "CR33-2AF3-KZB": "https://www.crglobal.mx/product-page/estructura-armado-rapido-motor-dual",
    "CR33-2S2": "https://www.crglobal.mx/product-page/estructura-de-un-motor-1",
    "CR33-2S2G": "https://www.crglobal.mx/product-page/estructura-de-un-motor-robusta",
    "CR33-2SBF2": "https://www.crglobal.mx/product-page/estructura-de-un-motor-pata-cuadrada",
    "CR33-2SBF2G": "https://www.crglobal.mx/product-page/escritorio-gamer-1",
    "CR33-3AF3EX": "https://www.crglobal.mx/product-page/estructura-l-fija-modular",
    "CR33-4AF365": "https://www.crglobal.mx/product-page/estructura-bench-doble-trevsa%C3%B1o-65-cm",
    "CR33-4AF385": "https://www.crglobal.mx/product-page/estructura-bench-doble-trevsa%C3%B1o-85-cm",
    "CR33-E2": "https://www.crglobal.mx/product-page/estructura-pedestal",
    "CR33-E3": "https://www.crglobal.mx/product-page/estructura-pedestal-cuadrada",
    "CR33-E4A": "https://www.crglobal.mx/product-page/escritorio-inteligente-con-cubierta-de-cristal",
    "CR33-S2-3": "https://www.crglobal.mx/product-page/escritorio-infantil",
    "CR33-T3": "https://www.crglobal.mx/product-page/escritorio-tipo-restirador",
    "CR33-TV-TROLLEY": "https://www.crglobal.mx/product-page/soporte-elevable-m%C3%B3vil-para-tv",
    "CR33-W4": "https://www.crglobal.mx/product-page/escritorio-inteligente-con-cubierta-tipo-madera",
    "CR33-Z4": "https://www.crglobal.mx/product-page/escritorio-inteligente-con-cubierta-laqueada",
    "CR33HK": "https://www.crglobal.mx/product-page/controlador",
    "MONITOR ARM-301": "https://www.crglobal.mx/product-page/brazo-triple",
    "MONITOR ARM-D": "https://www.crglobal.mx/product-page/brazo-doble",
    "MONITOR ARM-F160": "https://www.crglobal.mx/product-page/brazo-doble-f160",
    "MONITOR ARM-F80": "https://www.crglobal.mx/product-page/brazo-sencillo-f80",
    "MONITOR ARM-PS80": "https://www.crglobal.mx/product-page/brazo-sencillo-ps80",
    "MONITOR ARM-S": "https://www.crglobal.mx/product-page/brazo-sencillo",
    "SNAKE CABLE": "https://www.crglobal.mx/product-page/vertebra-snake",
    "WHEELS": "https://www.crglobal.mx/product-page/rueda",
}


def resolve_cr_global_link(code: str, source_sha256: str) -> dict:
    normalized_code = " ".join(str(code or "").upper().split())
    if source_sha256 != CR_GLOBAL_SPEC_SHA256:
        return {"url": "", "status": "source_hash_mismatch", "matched_code": ""}
    url = _EXACT_PRODUCT_LINKS.get(normalized_code, "")
    parsed = urlsplit(url)
    if not url or parsed.scheme != "https" or parsed.hostname != _OFFICIAL_HOST:
        return {"url": "", "status": "not_found", "matched_code": ""}
    return {
        "url": url,
        "status": "exact_code",
        "lookup_code": normalized_code,
        "matched_code": normalized_code,
        "source": "crglobal.mx",
    }


__all__ = ("CR_GLOBAL_SPEC_SHA256", "resolve_cr_global_link")
