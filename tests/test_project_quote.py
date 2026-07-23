from copy import deepcopy
from dataclasses import FrozenInstanceError
from decimal import Decimal
import hashlib
import json

import pytest

from project_fixtures import valid_project_payload
from mobiliti_saas.quote_engine.project_quote import (
    ProjectComponent,
    project_context,
    project_quote_projection,
)


def test_project_projection_keeps_physical_rows_and_exact_price_ratios():
    payload = valid_project_payload()
    principal = payload["lines"][0]
    principal["quantity"] = "10"
    per_unit = payload["lines"][1]
    per_unit["quantity"] = "2"
    payload["lines"].append({
        **deepcopy(per_unit),
        "line_id": "44444444-4444-4444-8444-444444444444",
        "position": 1,
        "quantity_mode": "fixed_project",
        "quantity": "3",
        "official_code": "FIXED-1",
    })

    projection = project_quote_projection(payload)

    assert [item.physical_quantity for item in projection.components] == [
        Decimal("10"),
        Decimal("20"),
        Decimal("3"),
    ]
    composition = projection.compositions[0]
    assert [
        (term.line_id, term.numerator, term.denominator)
        for term in composition.price_terms
    ] == [
        (principal["line_id"], Decimal("1"), Decimal("1")),
        (per_unit["line_id"], Decimal("2"), Decimal("1")),
        (
            "44444444-4444-4444-8444-444444444444",
            Decimal("3"),
            Decimal("10"),
        ),
    ]


def test_projection_orders_sections_principals_and_children_by_saved_position():
    payload = valid_project_payload()
    first_principal, child = payload["lines"]
    payload["sections"] = [
        {"section_id": "section-later", "concept": "Privados", "position": 1},
        {"section_id": "section-first", "concept": "Recepcion", "position": 0},
    ]
    first_principal["section_id"] = "section-later"
    later_child = {
        **deepcopy(child),
        "line_id": "55555555-5555-4555-8555-555555555555",
        "position": 1,
    }
    first_child = {
        **deepcopy(child),
        "line_id": "66666666-6666-4666-8666-666666666666",
        "position": 0,
    }
    second_principal = {
        **deepcopy(first_principal),
        "line_id": "77777777-7777-4777-8777-777777777777",
        "section_id": "section-first",
    }
    child["parent_line_id"] = first_principal["line_id"]
    first_child["parent_line_id"] = first_principal["line_id"]
    later_child["parent_line_id"] = first_principal["line_id"]
    payload["lines"] = [later_child, first_principal, second_principal, first_child]

    projection = project_quote_projection(payload)

    assert [item.principal_line_id for item in projection.compositions] == [
        second_principal["line_id"],
        first_principal["line_id"],
    ]
    assert projection.compositions[1].component_line_ids == (
        first_principal["line_id"],
        first_child["line_id"],
        later_child["line_id"],
    )


def test_project_context_is_canonical_and_does_not_mutate_the_source_payload():
    payload = valid_project_payload()
    payload["lines"][0]["quantity_rules_cache"] = {
        "bounds": {"minimum": "1"},
    }
    original = deepcopy(payload)

    context = project_context(payload, "project-7", 3)

    normalized = context["normalized_project_payload"]
    canonical = json.dumps(
        normalized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    assert context["project_payload_hash"] == hashlib.sha256(canonical).hexdigest()
    assert context["project_id"] == "project-7"
    assert context["project_revision"] == 3
    assert payload == original
    normalized["quote_fields"]["cliente"] = "Otro"
    assert payload == original
    payload["lines"][0]["quantity_rules_cache"]["bounds"]["minimum"] = "2"
    assert (
        normalized["lines"][0]["quantity_rules_cache"]["bounds"]["minimum"]
        == "1"
    )


def test_projection_values_are_frozen():
    component = project_quote_projection(valid_project_payload()).components[0]

    with pytest.raises(FrozenInstanceError):
        component.role = "complement"
    assert isinstance(component, ProjectComponent)
