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

    exact_category = _classify_exact(product_norm, dictionary, normalize_accents)
    if exact_category:
        return exact_category

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


def classify_product_name(
    product_name: str,
    dictionary: dict,
    *,
    description: object = "",
    source_category: object = "",
    supplier: object = "",
) -> str:
    supplier_text = normalizar_texto(supplier)
    if re.search(r"(?:^|[^a-z0-9])lumbro(?:[^a-z0-9]|$)", supplier_text):
        return "Multicontactos"
    if re.search(r"(?:^|[^a-z0-9])tarkett(?:[^a-z0-9]|$)", supplier_text):
        tarkett_text = normalizar_texto(
            " ".join(str(value or "") for value in (product_name, description, source_category))
        )
        if re.search(
            r"(?:^|[^a-z0-9])(?:aislante|desso|alfombra|loseta|piso|ultrabond|ambienta|aurea|ecomute)(?:[^a-z0-9]|$)",
            tarkett_text,
        ):
            return "Terminados"

    normalize_accents = dictionary.get("config", {}).get("normalizar_acentos", True)
    default = dictionary.get("config", {}).get("default_category", "OTRO")
    matched_default = False
    for value in (product_name, description, source_category):
        text = normalizar_texto(value, quitar_acentos=normalize_accents)
        category = _classify_exact(
            text,
            dictionary,
            normalize_accents,
            excluded_category=default,
        )
        if category:
            return category
        matched_default = matched_default or (
            _classify_exact(text, dictionary, normalize_accents) == default
        )

    if matched_default:
        return default
    return default


def _classify_exact(
    text: str,
    dictionary: dict,
    normalize_accents: bool,
    *,
    excluded_category: str | None = None,
) -> str | None:
    ordered_terms: list[tuple[int, str, str]] = []
    for category, data in dictionary.get("categorias", {}).items():
        for term in data.get("terminos", []):
            term_norm = normalizar_texto(term, quitar_acentos=normalize_accents)
            ordered_terms.append((len(term_norm), term_norm, category))
    ordered_terms.sort(reverse=True)

    for _, term_norm, category in ordered_terms:
        if (
            category != excluded_category
            and term_norm
            and re.search(
                rf"(?<![a-z0-9]){re.escape(term_norm)}(?![a-z0-9])",
                text,
            )
        ):
            return category
    return None


def enrich_dictionary_with_aliases(dictionary: dict, product_names: list[str]) -> dict:
    enriched = deepcopy(dictionary)
    for product_name in product_names:
        _learn_category_alias(
            enriched,
            product_name,
            classification_dictionary=dictionary,
        )
    return enriched


def _learn_category_alias(
    dictionary: dict,
    product_name: str,
    *,
    classification_dictionary: dict | None = None,
) -> None:
    if not product_name:
        return

    base_dictionary = classification_dictionary or dictionary
    normalize_accents = base_dictionary.get("config", {}).get("normalizar_acentos", True)
    category = _classify_exact(
        normalizar_texto(product_name, quitar_acentos=normalize_accents),
        base_dictionary,
        normalize_accents,
    )
    default_category = base_dictionary.get("config", {}).get("default_category", "OTRO")
    if category is None or category == default_category:
        return

    alias = _extract_category_alias(product_name, category, base_dictionary)
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
