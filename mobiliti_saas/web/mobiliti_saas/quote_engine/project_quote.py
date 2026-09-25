"""Proyección pura de un Proyecto persistido hacia sus líneas de cotización."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from decimal import Decimal
import hashlib
import json
from typing import Mapping

from .project_model import normalize_project_payload


@dataclass(frozen=True)
class ProjectPriceTerm:
    """Factor exacto con el que participa un componente en el precio visible."""

    line_id: str
    numerator: Decimal
    denominator: Decimal


@dataclass(frozen=True)
class ProjectComponent:
    """Una fila física que debe conservarse separada en ``Mobiliti``."""

    line_id: str
    principal_line_id: str
    section_id: str
    physical_quantity: Decimal
    role: str


@dataclass(frozen=True)
class ProjectComposition:
    """La línea principal visible y sus componentes comerciales ordenados."""

    principal_line_id: str
    section_id: str
    component_line_ids: tuple[str, ...]
    price_terms: tuple[ProjectPriceTerm, ...]


@dataclass(frozen=True)
class ProjectQuoteProjection:
    """Las dos vistas inmutables necesarias para generar el libro oficial."""

    components: tuple[ProjectComponent, ...]
    compositions: tuple[ProjectComposition, ...]


def project_quote_projection(
    payload: Mapping[str, object],
) -> ProjectQuoteProjection:
    """Calcula cantidades físicas y factores comerciales sin redondear.

    El validador persistente sigue siendo la autoridad del contrato. La salida se
    ordena por posición guardada de sección, principal y complemento, sin depender
    del orden accidental de las listas de entrada.
    """

    checked = normalize_project_payload(deepcopy(dict(payload)))
    section_positions = {
        section["section_id"]: section["position"]
        for section in checked["sections"]
    }
    by_parent: dict[str, list[dict]] = {}
    for line in checked["lines"]:
        if line["role"] == "complement":
            by_parent.setdefault(line["parent_line_id"], []).append(line)

    components: list[ProjectComponent] = []
    compositions: list[ProjectComposition] = []
    principals = sorted(
        (line for line in checked["lines"] if line["role"] == "principal"),
        key=lambda line: (
            section_positions[line["section_id"]],
            line["position"],
        ),
    )
    for principal in principals:
        principal_quantity = Decimal(principal["quantity"])
        children = sorted(
            by_parent.get(principal["line_id"], ()),
            key=lambda line: line["position"],
        )
        ordered_ids = [principal["line_id"]]
        terms = [
            ProjectPriceTerm(
                principal["line_id"],
                Decimal("1"),
                Decimal("1"),
            )
        ]
        components.append(ProjectComponent(
            line_id=principal["line_id"],
            principal_line_id=principal["line_id"],
            section_id=principal["section_id"],
            physical_quantity=principal_quantity,
            role="principal",
        ))

        for child in children:
            child_quantity = Decimal(child["quantity"])
            if child["quantity_mode"] == "per_parent_unit":
                physical_quantity = principal_quantity * child_quantity
                numerator = child_quantity
                denominator = Decimal("1")
            else:
                physical_quantity = child_quantity
                numerator = child_quantity
                denominator = principal_quantity

            ordered_ids.append(child["line_id"])
            terms.append(ProjectPriceTerm(
                child["line_id"],
                numerator,
                denominator,
            ))
            components.append(ProjectComponent(
                line_id=child["line_id"],
                principal_line_id=principal["line_id"],
                section_id=principal["section_id"],
                physical_quantity=physical_quantity,
                role="complement",
            ))

        compositions.append(ProjectComposition(
            principal_line_id=principal["line_id"],
            section_id=principal["section_id"],
            component_line_ids=tuple(ordered_ids),
            price_terms=tuple(terms),
        ))

    return ProjectQuoteProjection(
        components=tuple(components),
        compositions=tuple(compositions),
    )


def project_context(
    payload: Mapping[str, object],
    project_id: str,
    project_revision: int,
) -> dict:
    """Congela el contexto normalizado y su hash canónico para el job."""

    checked = normalize_project_payload(deepcopy(dict(payload)))
    projection = project_quote_projection(checked)
    canonical = json.dumps(
        checked,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return {
        "project_id": project_id,
        "project_revision": project_revision,
        "project_payload_hash": hashlib.sha256(canonical).hexdigest(),
        "normalized_project_payload": checked,
        "compositions": [
            {
                "principal_line_id": composition.principal_line_id,
                "section_id": composition.section_id,
                "component_line_ids": list(composition.component_line_ids),
                "price_terms": [
                    {
                        "line_id": term.line_id,
                        "numerator": format(term.numerator, "f"),
                        "denominator": format(term.denominator, "f"),
                    }
                    for term in composition.price_terms
                ],
            }
            for composition in projection.compositions
        ],
    }
