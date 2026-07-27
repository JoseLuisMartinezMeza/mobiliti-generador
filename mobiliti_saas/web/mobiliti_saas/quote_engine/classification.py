from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import json
import re
import unicodedata

try:
    from rapidfuzz import fuzz, process

    RAPIDFUZZ_AVAILABLE = True
except ImportError:
    RAPIDFUZZ_AVAILABLE = False


BASE_DICTIONARY_PATH = Path(__file__).resolve().parent / "diccionario_categorias.json"


def cargar_diccionario(path: str | Path) -> dict:
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _remove_accents(text: str) -> str:
    return "".join(
        char for char in unicodedata.normalize("NFD", text)
        if unicodedata.category(char) != "Mn"
    )


def normalizar_texto(text: str, quitar_acentos: bool = True) -> str:
    if not isinstance(text, str):
        text = str(text) if text is not None else ""
    text = re.sub(r"\s+", " ", text.lower().strip().replace("\n", " ").replace("\r", " "))
    return _remove_accents(text) if quitar_acentos else text


def _flat_terms(dictionary: dict) -> list[tuple[str, str]]:
    terms: list[tuple[str, str]] = []
    for category, data in dictionary.get("categorias", {}).items():
        for term in data.get("terminos", []):
            terms.append((normalizar_texto(term), category))
    return terms


def clasificar_producto(product_name: str, dictionary: dict) -> str:
    if not product_name:
        return dictionary.get("config", {}).get("default_category", "OTRO")

    config = dictionary.get("config", {})
    threshold = config.get("umbral_fuzzy", 75)
    normalize_accents = config.get("normalizar_acentos", True)
    default = config.get("default_category", "OTRO")
    product_norm = normalizar_texto(product_name, quitar_acentos=normalize_accents)

    ordered_terms: list[tuple[int, str, str]] = []
    for category, data in dictionary.get("categorias", {}).items():
        for term in data.get("terminos", []):
            term_norm = normalizar_texto(term, quitar_acentos=normalize_accents)
            ordered_terms.append((len(term_norm), term_norm, category))
    ordered_terms.sort(reverse=True)

    for _, term_norm, category in ordered_terms:
        if term_norm and term_norm in product_norm:
            return category

    if RAPIDFUZZ_AVAILABLE:
        flat_terms = _flat_terms(dictionary)
        result = process.extractOne(product_norm, [term for term, _ in flat_terms], scorer=fuzz.WRatio)
        if result is not None:
            _, score, index = result
            if score >= threshold:
                return flat_terms[index][1]
    return default


def load_category_dictionary(product_names: list[str] | None = None) -> dict:
    dictionary = cargar_diccionario(BASE_DICTIONARY_PATH)
    if not product_names:
        return dictionary
    return enrich_dictionary_with_aliases(dictionary, product_names)


def classify_product_name(product_name: str, dictionary: dict) -> str:
    return clasificar_producto(product_name, dictionary)


def enrich_dictionary_with_aliases(dictionary: dict, product_names: list[str]) -> dict:
    enriched = deepcopy(dictionary)
    for product_name in product_names:
        _learn_category_alias(enriched, product_name)
    return enriched


def _learn_category_alias(dictionary: dict, product_name: str) -> None:
    if not product_name:
        return

    category = clasificar_producto(product_name, dictionary)
    default_category = dictionary.get("config", {}).get("default_category", "OTRO")
    if category == default_category:
        return

    alias = _extract_category_alias(product_name, category, dictionary)
    if not alias:
        return

    terms = dictionary.setdefault("categorias", {}).setdefault(category, {}).setdefault("terminos", [])
    normalized_terms = {normalizar_texto(term) for term in terms}
    if alias not in normalized_terms:
        terms.append(alias)


def _extract_category_alias(product_name: str, category: str, dictionary: dict) -> str | None:
    product_norm = normalizar_texto(product_name)
    category_terms = dictionary.get("categorias", {}).get(category, {}).get("terminos", [])
    normalized_terms = sorted(
        {normalizar_texto(term) for term in category_terms},
        key=len,
        reverse=True,
    )

    for term in normalized_terms:
        if not term or term not in product_norm:
            continue
        alias = product_norm.split(term, 1)[0].strip(" -_/.,")
        alias = re.sub(r"\s+", " ", alias).strip()
        if _is_useful_alias(alias):
            return alias
    return None


def _is_useful_alias(alias: str) -> bool:
    if len(alias) < 6:
        return False
    if len(alias.split()) >= 2:
        return True
    return bool(re.search(r"[a-z]+\d|\d+[a-z]", alias))
