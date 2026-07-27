"""Python-only quote generation engine for Mobiliti SaaS."""

from .classification import classify_product_name, load_category_dictionary
from .descriptions import build_product_description, normalize_description_language
from .engine import generate_quote
from .parser import QuoteItem, detect_columns, read_items
from .project_model import (
    CATALOG_LINE_FIELDS,
    COMMON_LINE_FIELDS,
    COMPLEMENT_QUANTITY_MODES,
    IMPORTED_LINE_FIELDS,
    PROJECT_CURRENCIES,
    PROJECT_ROLES,
    PROJECT_SCHEMA_VERSION,
    normalize_project_payload,
    normalized_match_key,
    project_physical_line_count,
    project_summary,
)
from .project_quote import (
    ProjectComponent,
    ProjectComposition,
    ProjectPriceTerm,
    ProjectQuoteProjection,
    project_context,
    project_quote_projection,
)

__all__ = [
    "QuoteItem",
    "CATALOG_LINE_FIELDS",
    "COMMON_LINE_FIELDS",
    "COMPLEMENT_QUANTITY_MODES",
    "IMPORTED_LINE_FIELDS",
    "PROJECT_CURRENCIES",
    "PROJECT_ROLES",
    "PROJECT_SCHEMA_VERSION",
    "ProjectComponent",
    "ProjectComposition",
    "ProjectPriceTerm",
    "ProjectQuoteProjection",
    "build_product_description",
    "classify_product_name",
    "detect_columns",
    "generate_quote",
    "load_category_dictionary",
    "normalize_description_language",
    "normalize_project_payload",
    "normalized_match_key",
    "project_physical_line_count",
    "project_context",
    "project_quote_projection",
    "project_summary",
    "read_items",
]
