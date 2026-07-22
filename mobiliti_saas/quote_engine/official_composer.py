"""Composición OOXML allowlist sobre la plantilla oficial de Mobiliti.

El módulo nunca abre ni guarda el libro completo con OpenPyXL.  Parte de los
bytes oficiales, reemplaza únicamente las superficies declaradas por el
contrato y publica el ZIP sólo después de auditar el archivo candidato.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from decimal import Decimal
import os
from pathlib import Path
import posixpath
import re
from types import MappingProxyType
from typing import Mapping, Sequence
from xml.etree import ElementTree as ET

from openpyxl.utils.cell import column_index_from_string, get_column_letter

from .mobiliti_layout import MobilitiRowMap
from .ooxml_formula import translate_calc_chain, translate_formula
from .ooxml_package import (
    OFFICE_DOCUMENT_RELATIONSHIPS,
    PACKAGE_RELATIONSHIPS,
    PackageAudit,
    PackageMutation,
    XlsxPackage,
    assert_package_preserved,
    relationship_part_name,
    relationship_type_uris,
    resolve_internal_target,
    validate_part_name,
)
from .ooxml_worksheet import MobilitiSheetMutation
from .official_template import TemplateContract, verify_official_template
from .quotation_sheets import SheetAddition


MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL = OFFICE_DOCUMENT_RELATIONSHIPS
PKG_REL = PACKAGE_RELATIONSHIPS
CONTENT_TYPES = "http://schemas.openxmlformats.org/package/2006/content-types"
XDR = "http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing"
DRAWING = "http://schemas.openxmlformats.org/drawingml/2006/main"
XML = "http://www.w3.org/XML/1998/namespace"
WORKSHEET_CONTENT_TYPE = (
    "application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"
)
PNG_CONTENT_TYPE = "image/png"
JPEG_CONTENT_TYPE = "image/jpeg"
MAX_CELL_TEXT = 32_767
MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_IMAGE_PIXELS = 40_000_000
XLSX_MAX_ROWS = 1_048_576
CANONICAL_COTIZACION_FIRST_DYNAMIC_ROW = 16
CANONICAL_COTIZACION_FIRST_PRODUCT_ROW = 17
CANONICAL_COTIZACION_TOTAL_START = 19
CANONICAL_COTIZACION_TOTAL_ROW = 24
CANONICAL_COTIZACION_TERMS_START = 28
CANONICAL_COTIZACION_PRINT_END = 76
CANONICAL_MOBILITI_TOTAL_ROW = 573
CANONICAL_MOBILITI_AUX_START = 574
CANONICAL_MOBILITI_AUX_END = 610
CANONICAL_MOBILITI_SECTION_COUNT = 16
CANONICAL_MOBILITI_BLOCK_HEIGHT = 35
CANONICAL_MOBILITI_FIRST_SECTION_ROW = 13
CANONICAL_MOBILITI_PRODUCT_CAPACITY = 33
_CELL = re.compile(r"(?P<column>[A-Z]{1,3})(?P<row>[1-9][0-9]*)\Z")
_SAFE_IMAGE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_. -]{0,180}\Z")

for prefix, namespace in (
    ("", MAIN),
    ("r", REL),
    ("xdr", XDR),
    ("a", DRAWING),
):
    ET.register_namespace(prefix, namespace)


@dataclass(frozen=True)
class CotizacionMetadata:
    """Valores visibles permitidos en el encabezado oficial."""

    quotation_number: str = ""
    project: str = ""
    client: str = ""
    email: str = ""
    phone: str = ""
    address: str = ""
    business_name: str = ""

    def __post_init__(self) -> None:
        for value in (
            self.quotation_number,
            self.project,
            self.client,
            self.email,
            self.phone,
            self.address,
            self.business_name,
        ):
            _validate_text(value, allow_empty=True)


@dataclass(frozen=True)
class CotizacionProduct:
    """Una línea visible de ``Cotizacion`` enlazada a una fila Mobiliti."""

    item_key: str
    name: str
    description: str
    dimensions: str
    quantity: Decimal
    mobiliti_row: int
    discount: Decimal = Decimal("0")
    image_path: Path | None = None

    def __post_init__(self) -> None:
        _validate_text(self.item_key)
        _validate_text(self.name)
        _validate_text(self.description, allow_empty=True)
        _validate_text(self.dimensions, allow_empty=True)
        quantity = _decimal(self.quantity, "cantidad")
        discount = _decimal(self.discount, "descuento")
        if quantity <= 0:
            raise ValueError("La cantidad de Cotizacion debe ser positiva")
        if discount < 0 or discount > 1:
            raise ValueError("El descuento de Cotizacion debe estar entre 0 y 1")
        if type(self.mobiliti_row) is not int or not 1 <= self.mobiliti_row <= XLSX_MAX_ROWS:
            raise ValueError("Fila Mobiliti de Cotizacion inválida")
        object.__setattr__(self, "quantity", quantity)
        object.__setattr__(self, "discount", discount)
        if self.image_path is not None:
            object.__setattr__(self, "image_path", Path(self.image_path))


@dataclass(frozen=True)
class CotizacionSection:
    """Categoría de presentación y sus productos, en orden autoritativo."""

    title: str
    products: tuple[CotizacionProduct, ...]

    def __post_init__(self) -> None:
        _validate_text(self.title)
        if not isinstance(self.products, tuple) or not all(
            isinstance(item, CotizacionProduct) for item in self.products
        ):
            raise TypeError("Productos de sección Cotizacion inválidos")


@dataclass(frozen=True)
class CotizacionProductImage:
    path: Path
    target_row: int


@dataclass(frozen=True)
class CotizacionSheetMutation:
    """Mutación de hoja y partes de dibujo explícitamente relacionadas."""

    xml: bytes
    related_parts: Mapping[str, bytes] = field(default_factory=dict)
    total_row: int = CANONICAL_COTIZACION_TOTAL_ROW
    related_additions: Mapping[str, bytes] = field(default_factory=dict)
    related_content_types: Mapping[str, str] = field(default_factory=dict)
    images: tuple[CotizacionProductImage, ...] = ()
    terms_row_delta: int = 0
    product_rows: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.xml, bytes):
            raise TypeError("XML de Cotizacion inválido")
        if type(self.total_row) is not int or self.total_row < 1:
            raise ValueError("Fila total de Cotizacion inválida")
        if type(self.terms_row_delta) is not int:
            raise TypeError("Desplazamiento de términos inválido")
        if not isinstance(self.images, tuple) or not all(
            isinstance(item, CotizacionProductImage) for item in self.images
        ):
            raise TypeError("Imágenes de Cotizacion inválidas")
        if not isinstance(self.product_rows, tuple) or not all(
            type(item) is int and item >= 1 for item in self.product_rows
        ):
            raise TypeError("Filas de producto Cotizacion inválidas")
        object.__setattr__(
            self,
            "related_parts",
            _immutable_bytes_mapping(self.related_parts, "Partes relacionadas"),
        )
        object.__setattr__(
            self,
            "related_additions",
            _immutable_bytes_mapping(self.related_additions, "Adiciones relacionadas"),
        )
        object.__setattr__(
            self,
            "related_content_types",
            MappingProxyType(dict(self.related_content_types)),
        )


@dataclass(frozen=True)
class ComposeRequest:
    template: Path
    output: Path
    mobiliti: MobilitiSheetMutation
    cotizacion: CotizacionSheetMutation
    quotation: SheetAddition | None
    quotation_data: SheetAddition
    contract: TemplateContract

    def __post_init__(self) -> None:
        object.__setattr__(self, "template", Path(self.template))
        object.__setattr__(self, "output", Path(self.output))
        if not isinstance(self.mobiliti, MobilitiSheetMutation):
            raise TypeError("Mutación Mobiliti inválida")
        if not isinstance(self.cotizacion, CotizacionSheetMutation):
            raise TypeError("Mutación Cotizacion inválida")
        if self.quotation is not None and not isinstance(self.quotation, SheetAddition):
            raise TypeError("Adición Quotation inválida")
        if not isinstance(self.quotation_data, SheetAddition):
            raise TypeError("Adición Quotation_Data inválida")
        if self.quotation_data.name != "Quotation_Data":
            raise ValueError("La adición obligatoria debe ser Quotation_Data")
        if not isinstance(self.contract, TemplateContract):
            raise TypeError("Contrato oficial inválido")

    def with_output(self, output: Path) -> "ComposeRequest":
        return ComposeRequest(
            template=self.template,
            output=Path(output),
            mobiliti=self.mobiliti,
            cotizacion=self.cotizacion,
            quotation=self.quotation,
            quotation_data=self.quotation_data,
            contract=self.contract,
        )


class CotizacionSheetEditor:
    """Editor estrecho que clona los bloques visibles del XML oficial."""

    def __init__(self, root: ET.Element):
        if root.tag != f"{{{MAIN}}}worksheet":
            raise ValueError("La parte indicada no es Cotizacion SpreadsheetML")
        sheet_data = root.find(f"{{{MAIN}}}sheetData")
        if sheet_data is None:
            raise ValueError("Cotizacion no contiene sheetData")
        self.root = root
        self.sheet_data = sheet_data

    @classmethod
    def from_xml(cls, payload: bytes) -> "CotizacionSheetEditor":
        try:
            root = ET.fromstring(payload)
        except (ET.ParseError, TypeError) as error:
            raise ValueError("El XML de Cotizacion no es válido") from error
        return cls(root)

    def compose(
        self,
        *,
        metadata: CotizacionMetadata,
        sections: Sequence[CotizacionSection],
    ) -> CotizacionSheetMutation:
        """Reemplaza únicamente encabezado y bloques dinámicos A:J."""

        if not isinstance(metadata, CotizacionMetadata):
            raise TypeError("Metadata de Cotizacion inválida")
        if isinstance(sections, (str, bytes)) or not isinstance(sections, Sequence):
            raise TypeError("Secciones Cotizacion inválidas")
        frozen_sections = tuple(sections)
        if not all(isinstance(section, CotizacionSection) for section in frozen_sections):
            raise TypeError("Secciones Cotizacion inválidas")
        products = tuple(
            product for section in frozen_sections for product in section.products
        )
        if not products:
            raise ValueError("Cotizacion requiere al menos un producto")
        keys = tuple(product.item_key for product in products)
        if len(keys) != len(set(keys)):
            raise ValueError("item_key duplicado en Cotizacion")

        rows = {int(row.attrib["r"]): row for row in self.sheet_data.findall(f"{{{MAIN}}}row")}
        try:
            category_template = rows[16]
            product_template = rows[17]
            total_templates = tuple(rows[number] for number in range(19, 25))
            gap_templates = tuple(rows[number] for number in range(25, 28))
        except KeyError as error:
            raise ValueError("Bloques oficiales de Cotizacion incompletos") from error

        self._write_metadata(metadata)
        preserved_headers = [row for number, row in sorted(rows.items()) if number < 16]
        dynamic_rows: list[ET.Element] = []
        dynamic_merges: list[str] = []
        product_rows: list[int] = []
        images: list[CotizacionProductImage] = []
        cursor = CANONICAL_COTIZACION_FIRST_DYNAMIC_ROW
        first_discount_row: int | None = None

        for section in frozen_sections:
            if not section.products:
                continue
            header = _clone_row_region(category_template, 16, cursor, last_column=10)
            _set_inline_string(header, f"A{cursor}", section.title)
            dynamic_rows.append(header)
            dynamic_merges.append(f"A{cursor}:J{cursor}")
            cursor += 1
            for product in section.products:
                target_row = cursor
                if first_discount_row is None:
                    first_discount_row = target_row
                row = _clone_row_region(product_template, 17, target_row, last_column=10)
                _set_inline_string(row, f"A{target_row}", product.name)
                _clear_cell_value(_require_cell(row, f"B{target_row}"))
                _set_inline_string(row, f"C{target_row}", product.description)
                _set_inline_string(row, f"D{target_row}", product.dimensions)
                _set_number(row, f"E{target_row}", product.quantity)
                _set_formula(row, f"F{target_row}", f"=Mobiliti!X{product.mobiliti_row}")
                if target_row == first_discount_row:
                    _set_number(row, f"G{target_row}", product.discount)
                else:
                    _set_formula(
                        row,
                        f"G{target_row}",
                        f"=$G${first_discount_row}",
                    )
                _set_formula(
                    row,
                    f"H{target_row}",
                    translate_formula(
                        "=F17*G17",
                        origin="H17",
                        target=f"H{target_row}",
                        sheet="Cotizacion",
                    ),
                )
                _set_formula(row, f"I{target_row}", f"=F{target_row}-H{target_row}")
                _set_formula(
                    row,
                    f"J{target_row}",
                    translate_formula(
                        "=E17*I17",
                        origin="J17",
                        target=f"J{target_row}",
                        sheet="Cotizacion",
                    ),
                )
                dynamic_rows.append(row)
                product_rows.append(target_row)
                if product.image_path is not None:
                    images.append(CotizacionProductImage(product.image_path, target_row))
                cursor += 1

        total_start = cursor
        total_delta = total_start - CANONICAL_COTIZACION_TOTAL_START
        totals = [
            _clone_row_region(row, source, source + total_delta, last_column=10)
            for source, row in zip(range(19, 25), total_templates, strict=True)
        ]
        subtotal_row = total_start
        total_row = CANONICAL_COTIZACION_TOTAL_ROW + total_delta
        if total_row + (CANONICAL_COTIZACION_PRINT_END - CANONICAL_COTIZACION_TOTAL_ROW) > XLSX_MAX_ROWS:
            raise ValueError("Cotizacion excede la capacidad física de XLSX")
        _set_formula(
            totals[0],
            f"H{subtotal_row}",
            f"=SUM(IFERROR(J{product_rows[0]}:J{product_rows[-1]},0))",
            attributes={"t": "array", "ref": f"H{subtotal_row}"},
        )
        _set_number(totals[2], f"H{subtotal_row + 2}", Decimal("0"))
        for offset, row in enumerate(totals):
            target = total_start + offset
            if offset not in {0, 2}:
                source = CANONICAL_COTIZACION_TOTAL_START + offset
                source_formula = _formula_text(total_templates[offset], f"H{source}")
                if source_formula is not None:
                    _set_formula(
                        row,
                        f"H{target}",
                        translate_formula(
                            source_formula,
                            origin=f"H{source}",
                            target=f"H{target}",
                            sheet="Cotizacion",
                        ),
                    )
        dynamic_rows.extend(totals)
        dynamic_rows.extend(
            _clone_row_region(row, source, source + total_delta, last_column=10)
            for source, row in zip(range(25, 28), gap_templates, strict=True)
        )

        terms_rows = [
            _clone_row_region(row, number, number + total_delta, last_column=10)
            for number, row in sorted(rows.items())
            if number >= CANONICAL_COTIZACION_TERMS_START
        ]
        sidecars = _sidecar_cells(rows, first_row=16, first_sidecar_column=11)
        rebuilt = preserved_headers + dynamic_rows + terms_rows
        _apply_sidecars(rebuilt, sidecars)
        rebuilt.sort(key=lambda row: int(row.attrib["r"]))
        self.sheet_data[:] = rebuilt

        _rebuild_cotizacion_merges(
            self.root,
            dynamic_merges=dynamic_merges,
            total_delta=total_delta,
        )
        _update_dimension(self.root, total_delta)
        return CotizacionSheetMutation(
            xml=ET.tostring(self.root, encoding="utf-8", xml_declaration=True),
            total_row=total_row,
            images=tuple(images),
            terms_row_delta=total_delta,
            product_rows=tuple(product_rows),
        )

    def _write_metadata(self, metadata: CotizacionMetadata) -> None:
        rows = {
            int(row.attrib["r"]): row
            for row in self.sheet_data.findall(f"{{{MAIN}}}row")
        }
        values = {
            "B3": metadata.quotation_number,
            "B7": metadata.project,
            "B8": metadata.client,
            "B9": metadata.email,
            "B10": metadata.phone,
            "B11": metadata.address,
            "B12": metadata.business_name,
        }
        for coordinate, value in values.items():
            row_number = int(_CELL.fullmatch(coordinate).group("row"))
            if row_number not in rows:
                raise ValueError(f"Encabezado oficial ausente: {coordinate}")
            _set_inline_string(rows[row_number], coordinate, value)


def compose_official_quote(request: ComposeRequest) -> PackageAudit:
    """Compone, audita y publica una cotización oficial de forma atómica."""

    if not isinstance(request, ComposeRequest):
        raise TypeError("Solicitud de composición inválida")
    template, output = _validate_compose_paths(request.template, request.output)
    verify_official_template(template, request.contract)
    base = XlsxPackage.read(template)
    mutation = build_allowlisted_mutation(base, request)
    candidate = _candidate_path(output)
    base.write_new(candidate, mutation)
    audit = assert_package_preserved(template, candidate, mutation.allowed_parts)
    verify_output_contract(
        candidate,
        request.contract,
        request.mobiliti.row_map,
        cotizacion_total_row=request.cotizacion.total_row,
    )
    if output.exists():
        raise FileExistsError(f"La salida ya existe: {output}")
    os.rename(candidate, output)
    return audit


def build_allowlisted_mutation(
    base: XlsxPackage,
    request: ComposeRequest,
) -> PackageMutation:
    """Materializa la mutación concreta y rechaza superficies no declaradas."""

    if not isinstance(base, XlsxPackage) or not isinstance(request, ComposeRequest):
        raise TypeError("Entrada del compositor inválida")
    if set(request.contract.mutable_sheets) != {"Mobiliti", "Cotizacion"}:
        raise ValueError("Contrato de hojas mutables incompatible")
    _validate_declared_sheet_surfaces(base, request)

    cotizacion = merge_cotizacion_product_images(base, request.cotizacion)
    replacements: dict[str, bytes] = {
        base.sheet_part("Mobiliti"): request.mobiliti.xml,
        base.sheet_part("Cotizacion"): cotizacion.xml,
    }
    additions: dict[str, bytes] = {}
    _merge_disjoint(replacements, cotizacion.related_parts, "partes Cotizacion")
    _merge_disjoint(additions, cotizacion.related_additions, "adiciones Cotizacion")

    fletes_part = base.sheet_part("Fletes")
    estrategia_part = base.sheet_part("Estrategia Comercial ")
    replacements[fletes_part] = _translate_fletes(
        base.parts[fletes_part], request.mobiliti.row_map
    )
    replacements[estrategia_part] = _translate_estrategia(
        base.parts[estrategia_part],
        request.mobiliti.row_map,
        request.cotizacion.total_row,
    )

    sheet_additions = tuple(
        item
        for item in (request.quotation, request.quotation_data)
        if item is not None
    )
    workbook_xml, workbook_rels, content_types, added_parts, extra_replacements = (
        _add_workbook_sheets(
            base,
            sheet_additions,
            cotizacion_terms_delta=request.cotizacion.terms_row_delta,
            extra_additions=cotizacion.related_additions,
            extra_content_types=cotizacion.related_content_types,
        )
    )
    replacements["xl/workbook.xml"] = workbook_xml
    replacements["xl/_rels/workbook.xml.rels"] = workbook_rels
    replacements["[Content_Types].xml"] = content_types
    _merge_replacements(replacements, extra_replacements)
    _merge_disjoint(additions, added_parts, "hojas agregadas")

    calc_chain_part = _calc_chain_part(base)
    if calc_chain_part is not None:
        replacements[calc_chain_part] = _translate_official_calc_chain(
            base,
            request.mobiliti,
            request.cotizacion,
        )

    protected = tuple(request.contract.protected_prefixes)
    illegal = {
        name
        for name in (*replacements, *additions)
        if name.startswith(protected)
    }
    if illegal:
        raise ValueError(f"Mutación de parte protegida: {sorted(illegal)}")
    return PackageMutation(replacements=replacements, additions=additions)


def verify_output_contract(
    output: Path,
    contract: TemplateContract,
    row_map: MobilitiRowMap,
    *,
    cotizacion_total_row: int,
) -> None:
    """Verifica invariantes oficiales antes de publicar el candidato."""

    package = XlsxPackage.read(Path(output))
    rows = package._sheet_rows()
    names = [name for name, _state, _index, _part in rows]
    if len(names) != len({name.casefold() for name in names}):
        raise ValueError("La salida contiene hojas duplicadas")
    if "sheep" in {name.casefold() for name in names}:
        raise ValueError("La salida contiene una hoja residual sheep")
    states = {name: state for name, state, _index, _part in rows}
    for name, state in contract.sheet_states.items():
        if states.get(name) != state:
            raise ValueError(f"Estado oficial de hoja alterado: {name}")
    if states.get("Quotation_Data") != "veryHidden":
        raise ValueError("Quotation_Data no quedó veryHidden")

    external_parts = sum(name.startswith("xl/externalLinks/") for name in package.parts)
    if external_parts != contract.external_link_parts:
        raise ValueError("Cantidad de externalLinks alterada")
    spec_formulas = 0
    for name, _state, _index, part in rows:
        if name.strip().casefold().startswith("spec"):
            spec_formulas += len(
                ET.fromstring(package.parts[part]).findall(f".//{{{MAIN}}}f")
            )
    if spec_formulas != contract.spec_formula_count:
        raise ValueError("Fórmulas SPEC alteradas")

    workbook = ET.fromstring(package.parts["xl/workbook.xml"])
    defined_names = workbook.findall(f"{{{MAIN}}}definedNames/{{{MAIN}}}definedName")
    if len(defined_names) < contract.defined_name_count:
        raise ValueError("Nombres definidos oficiales ausentes")
    if _formula_at(package, "Fletes", "D19") != f"Mobiliti!H{row_map.total_row}":
        raise ValueError("Referencia Fletes!D19 desactualizada")
    if _formula_at(package, "Estrategia Comercial ", "D59") != (
        f"Cotizacion!H{cotizacion_total_row}"
    ):
        raise ValueError("Referencia Estrategia!D59 desactualizada")
    expected_subtotal = cotizacion_total_row - 5
    for coordinate in ("B63", "B64", "B68"):
        formula = _formula_at(package, "Estrategia Comercial ", coordinate)
        if formula is None or f"Cotizacion!H{expected_subtotal}" not in formula:
            raise ValueError(f"Referencia Estrategia!{coordinate} desactualizada")
    estrategia = ET.fromstring(package.parts[package.sheet_part("Estrategia Comercial ")])
    for row in range(7, 39):
        for column in ("B", "C"):
            formula = _formula_in_root(estrategia, f"{column}{row}")
            if formula is None or f"${row_map.last_product_row}" not in formula:
                raise ValueError("Rangos de Estrategia Comercial desactualizados")

    mobiliti = ET.fromstring(package.parts[package.sheet_part("Mobiliti")])
    if _formula_in_root(mobiliti, "K6") is None:
        raise ValueError("La fórmula oficial Mobiliti!K6 fue eliminada")
    for target_row in row_map.item_rows:
        for column in ("W", "X"):
            if _formula_in_root(mobiliti, f"{column}{target_row}") is None:
                raise ValueError(f"Fórmula oficial Mobiliti!{column}{target_row} ausente")
        price = _cell_in_root(mobiliti, f"J{target_row}")
        if price is None or price.find(f"{{{MAIN}}}f") is not None:
            raise ValueError(f"Mobiliti!J{target_row} no es un costo congelado numérico")


def merge_cotizacion_product_images(
    base: XlsxPackage,
    mutation: CotizacionSheetMutation,
) -> CotizacionSheetMutation:
    """Preserva dibujo estático, limpia anclas contaminadas y agrega imágenes."""

    if mutation.related_parts or mutation.related_additions:
        raise ValueError("Cotizacion ya contiene partes relacionadas no verificadas")
    sheet_part = base.sheet_part("Cotizacion")
    sheet_root = ET.fromstring(base.parts[sheet_part])
    drawing_nodes = sheet_root.findall(f"{{{MAIN}}}drawing")
    if len(drawing_nodes) != 1:
        if mutation.images:
            raise ValueError("Cotizacion oficial no contiene un dibujo único")
        return mutation
    relationship_id = drawing_nodes[0].attrib.get(f"{{{REL}}}id")
    sheet_rels_part = relationship_part_name(sheet_part)
    rels = ET.fromstring(base.parts[sheet_rels_part])
    relationship = next(
        (
            item
            for item in rels.findall(f"{{{PKG_REL}}}Relationship")
            if item.attrib.get("Id") == relationship_id
            and item.attrib.get("Type") in relationship_type_uris("drawing")
        ),
        None,
    )
    if relationship is None:
        raise ValueError("Relación de dibujo Cotizacion inválida")
    drawing_part = resolve_internal_target(sheet_part, relationship.attrib["Target"])
    drawing_rels_part = relationship_part_name(drawing_part)
    drawing_root = ET.fromstring(base.parts[drawing_part])
    drawing_rels = ET.fromstring(base.parts[drawing_rels_part])

    for anchor in list(drawing_root):
        marker = anchor.find(f"{{{XDR}}}from")
        if marker is None:
            continue
        row_node = marker.find(f"{{{XDR}}}row")
        column_node = marker.find(f"{{{XDR}}}col")
        if row_node is None or column_node is None or row_node.text is None or column_node.text is None:
            raise ValueError("Ancla de dibujo Cotizacion inválida")
        worksheet_row = int(row_node.text) + 1
        worksheet_column = int(column_node.text) + 1
        if 16 <= worksheet_row <= 18 and 1 <= worksheet_column <= 10:
            drawing_root.remove(anchor)
            continue
        if worksheet_row >= CANONICAL_COTIZACION_TERMS_START:
            _shift_anchor_rows(anchor, mutation.terms_row_delta)

    related_additions: dict[str, bytes] = {}
    content_types: dict[str, str] = {}
    next_picture_id = _next_picture_id(drawing_root)
    used_relationship_ids = {
        item.attrib.get("Id", "")
        for item in drawing_rels.findall(f"{{{PKG_REL}}}Relationship")
    }
    for sequence, image in enumerate(mutation.images, start=1):
        content, extension, content_type, width, height = _read_product_image(image.path)
        media_part = _allocate_media_part(base, related_additions, sequence, extension)
        rel_id = _next_relationship_id(used_relationship_ids)
        used_relationship_ids.add(rel_id)
        ET.SubElement(
            drawing_rels,
            f"{{{PKG_REL}}}Relationship",
            {
                "Id": rel_id,
                "Type": next(iter(relationship_type_uris("image"))),
                "Target": posixpath.relpath(media_part, posixpath.dirname(drawing_part)),
            },
        )
        drawing_root.append(
            _product_image_anchor(
                relationship_id=rel_id,
                picture_id=next_picture_id,
                target_row=image.target_row,
                width=width,
                height=height,
                name=image.path.name,
            )
        )
        next_picture_id += 1
        related_additions[media_part] = content
        content_types[media_part] = content_type

    related_parts = {
        drawing_part: ET.tostring(drawing_root, encoding="utf-8", xml_declaration=True),
        drawing_rels_part: ET.tostring(
            drawing_rels, encoding="utf-8", xml_declaration=True
        ),
    }
    return CotizacionSheetMutation(
        xml=mutation.xml,
        related_parts=related_parts,
        total_row=mutation.total_row,
        related_additions=related_additions,
        related_content_types=content_types,
        images=mutation.images,
        terms_row_delta=mutation.terms_row_delta,
        product_rows=mutation.product_rows,
    )


def _translate_fletes(payload: bytes, row_map: MobilitiRowMap) -> bytes:
    root = _worksheet_root(payload, "Fletes")
    _replace_formula(root, "D19", f"=Mobiliti!H{row_map.total_row}")
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def _validate_declared_sheet_surfaces(
    base: XlsxPackage,
    request: ComposeRequest,
) -> None:
    """Impide que una mutación tipada esconda escrituras estáticas arbitrarias."""

    official_mobiliti = _worksheet_root(
        base.parts[base.sheet_part("Mobiliti")], "Mobiliti"
    )
    candidate_mobiliti = _worksheet_root(request.mobiliti.xml, "Mobiliti")
    translated_static = {"E4", "E8", "P9", "AU11"}
    mutable_mobiliti = {"K4", "K8", *translated_static}
    _assert_static_cells_unchanged(
        official_mobiliti,
        candidate_mobiliti,
        mutable=lambda coordinate, row, _column: (
            coordinate in mutable_mobiliti or row >= 13
        ),
        sheet="Mobiliti",
    )
    _validate_translated_static_mobiliti_formulas(
        official_mobiliti,
        candidate_mobiliti,
        request.mobiliti.row_map,
    )

    official_cotizacion = _worksheet_root(
        base.parts[base.sheet_part("Cotizacion")], "Cotizacion"
    )
    candidate_cotizacion = _worksheet_root(request.cotizacion.xml, "Cotizacion")
    mutable_header = {"B3", "B7", "B8", "B9", "B10", "B11", "B12"}
    _assert_static_cells_unchanged(
        official_cotizacion,
        candidate_cotizacion,
        mutable=lambda coordinate, row, column: (
            coordinate in mutable_header or (row >= 16 and column <= 10)
        ),
        sheet="Cotizacion",
    )


def _assert_static_cells_unchanged(
    official: ET.Element,
    candidate: ET.Element,
    *,
    mutable,
    sheet: str,
) -> None:
    official_cells = _cell_payloads(official)
    candidate_cells = _cell_payloads(candidate)
    for coordinate in set(official_cells) | set(candidate_cells):
        match = _CELL.fullmatch(coordinate)
        if match is None:
            raise ValueError(f"Coordenada inválida en {sheet}")
        row = int(match.group("row"))
        column = column_index_from_string(match.group("column"))
        if mutable(coordinate, row, column):
            continue
        if official_cells.get(coordinate) != candidate_cells.get(coordinate):
            raise ValueError(
                f"Escritura fuera de la superficie mutable: {sheet}!{coordinate}"
            )


def _cell_payloads(root: ET.Element) -> dict[str, bytes]:
    result: dict[str, bytes] = {}
    for cell in root.findall(f".//{{{MAIN}}}c"):
        coordinate = cell.attrib.get("r", "")
        if coordinate in result:
            raise ValueError(f"Celda OOXML duplicada: {coordinate}")
        result[coordinate] = ET.tostring(cell, encoding="utf-8")
    return result


def _validate_translated_static_mobiliti_formulas(
    official: ET.Element,
    candidate: ET.Element,
    row_map: MobilitiRowMap,
) -> None:
    total_row = row_map.total_row
    expected = {
        "E4": f"AD{total_row}",
        "E8": f"(AD{total_row}-M{total_row})/AD{total_row}",
        "P9": f"P8/H{total_row}",
    }
    official_au11 = _formula_in_root(official, "AU11")
    if official_au11 is None:
        raise ValueError("Fórmula oficial Mobiliti!AU11 ausente")
    auxiliary_delta = total_row - CANONICAL_MOBILITI_TOTAL_ROW
    expected["AU11"] = re.sub(
        r"(?P<prefix>\$A[MN]\$)(?P<row>57[7-9]|58[0-9]|59[0-8])\b",
        lambda match: (
            match.group("prefix")
            + str(int(match.group("row")) + auxiliary_delta)
        ),
        official_au11,
    )
    for coordinate, formula in expected.items():
        cell = _cell_in_root(candidate, coordinate)
        official_cell = _cell_in_root(official, coordinate)
        if cell is None or official_cell is None:
            raise ValueError(f"Celda Mobiliti estática ausente: {coordinate}")
        if _formula_in_root(candidate, coordinate) != formula:
            raise ValueError(
                f"Escritura fuera de la superficie mutable: Mobiliti!{coordinate}"
            )
        official_attributes = dict(official_cell.attrib)
        candidate_attributes = dict(cell.attrib)
        if candidate_attributes != official_attributes:
            raise ValueError(
                f"Metadatos estáticos Mobiliti alterados: {coordinate}"
            )
        cached = cell.find(f"{{{MAIN}}}v")
        if auxiliary_delta == 0:
            if ET.tostring(cell, encoding="utf-8") != ET.tostring(
                official_cell,
                encoding="utf-8",
            ):
                raise ValueError(
                    f"Celda estática Mobiliti alterada sin reubicación: {coordinate}"
                )
        elif cached is not None:
            raise ValueError(f"Cache estática Mobiliti no fue invalidada: {coordinate}")


def _translate_estrategia(
    payload: bytes,
    row_map: MobilitiRowMap,
    cotizacion_total_row: int,
) -> bytes:
    root = _worksheet_root(payload, "Estrategia Comercial ")
    range_pattern = re.compile(
        r"(Mobiliti!\$?[A-Z]{1,3}\$14:\$?[A-Z]{1,3}\$)571\b"
    )
    for row in range(7, 39):
        for column in ("B", "C"):
            coordinate = f"{column}{row}"
            formula = _formula_in_root(root, coordinate)
            if formula is None:
                raise ValueError(f"Fórmula oficial ausente: Estrategia!{coordinate}")
            translated, count = range_pattern.subn(
                rf"\g<1>{row_map.last_product_row}", formula
            )
            if count != 2:
                raise ValueError(f"Rangos oficiales inesperados: Estrategia!{coordinate}")
            _replace_formula(root, coordinate, "=" + translated)
    _replace_formula(root, "D59", f"=Cotizacion!H{cotizacion_total_row}")
    subtotal_row = cotizacion_total_row - 5
    for coordinate in ("B63", "B64", "B68"):
        formula = _formula_in_root(root, coordinate)
        if formula is None or "Cotizacion!H19" not in formula:
            raise ValueError(f"Referencia oficial inesperada: Estrategia!{coordinate}")
        _replace_formula(
            root,
            coordinate,
            "=" + formula.replace("Cotizacion!H19", f"Cotizacion!H{subtotal_row}"),
        )
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def _add_workbook_sheets(
    base: XlsxPackage,
    sheet_additions: Sequence[SheetAddition],
    *,
    cotizacion_terms_delta: int,
    extra_additions: Mapping[str, bytes],
    extra_content_types: Mapping[str, str],
) -> tuple[bytes, bytes, bytes, dict[str, bytes], dict[str, bytes]]:
    workbook = ET.fromstring(base.parts["xl/workbook.xml"])
    workbook_rels = ET.fromstring(base.parts["xl/_rels/workbook.xml.rels"])
    content_types = ET.fromstring(base.parts["[Content_Types].xml"])
    sheets = workbook.find(f"{{{MAIN}}}sheets")
    if sheets is None:
        raise ValueError("Workbook oficial sin sheets")
    existing_names = {item.attrib["name"].casefold() for item in sheets}
    used_ids = {int(item.attrib["sheetId"]) for item in sheets}
    used_rel_ids = {
        item.attrib.get("Id", "")
        for item in workbook_rels.findall(f"{{{PKG_REL}}}Relationship")
    }
    occupied = set(base.parts) | set(extra_additions)
    added_parts: dict[str, bytes] = {}
    replacements: dict[str, bytes] = {}
    additions_with_parts: list[tuple[SheetAddition, str, int]] = []

    for addition in sheet_additions:
        if addition.name.casefold() in existing_names:
            raise ValueError(f"La hoja ya existe: {addition.name}")
        if addition.name not in {"Quotation", "Quotation_Data"}:
            raise ValueError("Hoja no permitida por el compositor")
        part = addition.sheet_part
        if part is None:
            part = _allocate_worksheet_part(occupied | set(added_parts), addition.name)
            parts = {part: addition.xml}
            part_content_types = {part: WORKSHEET_CONTENT_TYPE}
        else:
            parts = dict(addition.parts)
            part_content_types = dict(addition.content_types)
            if parts.get(part) != addition.xml:
                raise ValueError("Parte principal de hoja agregada inconsistente")
        overlap = set(parts) & (occupied | set(added_parts))
        if overlap:
            raise ValueError(f"Colisión de partes de hoja: {sorted(overlap)}")
        _merge_disjoint(added_parts, parts, f"partes {addition.name}")
        occupied.update(parts)
        _merge_replacements(replacements, addition.replacements)

        relationship_id = _next_relationship_id(used_rel_ids)
        used_rel_ids.add(relationship_id)
        sheet_id = max(used_ids, default=0) + 1
        used_ids.add(sheet_id)
        attributes = {
            "name": addition.name,
            "sheetId": str(sheet_id),
            f"{{{REL}}}id": relationship_id,
        }
        if addition.state != "visible":
            attributes["state"] = addition.state
        ET.SubElement(sheets, f"{{{MAIN}}}sheet", attributes)
        ET.SubElement(
            workbook_rels,
            f"{{{PKG_REL}}}Relationship",
            {
                "Id": relationship_id,
                "Type": addition.relationship_type,
                "Target": posixpath.relpath(part, "xl"),
            },
        )
        additions_with_parts.append((addition, part, len(sheets) - 1))
        existing_names.add(addition.name.casefold())
        for name, content_type in part_content_types.items():
            _ensure_content_type(content_types, name, content_type)
        for name, content_type in addition.replacement_content_types.items():
            actual = base.content_types_for({name})[name]
            if actual != content_type:
                raise ValueError(f"Content type de reemplazo inconsistente: {name}")

    for name, content_type in extra_content_types.items():
        _ensure_content_type(content_types, name, content_type)

    defined_names = workbook.find(f"{{{MAIN}}}definedNames")
    if defined_names is None:
        defined_names = ET.Element(f"{{{MAIN}}}definedNames")
        insertion = list(workbook).index(sheets) + 1
        workbook.insert(insertion, defined_names)
    print_area = next(
        (
            item
            for item in defined_names.findall(f"{{{MAIN}}}definedName")
            if item.attrib.get("name") == "_xlnm.Print_Area"
            and item.attrib.get("localSheetId") == "0"
        ),
        None,
    )
    if print_area is None or print_area.text != "Cotizacion!$A$1:$J$76":
        raise ValueError("Print_Area oficial de Cotizacion inesperada")
    print_area.text = (
        f"Cotizacion!$A$1:$J${CANONICAL_COTIZACION_PRINT_END + cotizacion_terms_delta}"
    )
    for addition, _part, sheet_index in additions_with_parts:
        for defined_name in addition.defined_names:
            defined_names.append(ET.fromstring(defined_name.xml_for_sheet_index(sheet_index)))

    return (
        ET.tostring(workbook, encoding="utf-8", xml_declaration=True),
        ET.tostring(workbook_rels, encoding="utf-8", xml_declaration=True),
        ET.tostring(content_types, encoding="utf-8", xml_declaration=True),
        added_parts,
        replacements,
    )


def _translate_official_calc_chain(
    base: XlsxPackage,
    mobiliti: MobilitiSheetMutation,
    cotizacion: CotizacionSheetMutation,
) -> bytes:
    calc_part = _calc_chain_part(base)
    if calc_part is None:
        raise ValueError("calcChain oficial ausente")
    content = base.parts[calc_part]
    mobiliti_map = _mobiliti_calc_map(
        base.parts[base.sheet_part("Mobiliti")], mobiliti.row_map
    )
    content = translate_calc_chain(
        content,
        sheet_id=_workbook_sheet_id(base, "Mobiliti"),
        coordinate_map=mobiliti_map,
    )
    cotizacion_map = _cotizacion_calc_map(
        base.parts[base.sheet_part("Cotizacion")], cotizacion
    )
    return translate_calc_chain(
        content,
        sheet_id=_workbook_sheet_id(base, "Cotizacion"),
        coordinate_map=cotizacion_map,
    )


def _mobiliti_calc_map(payload: bytes, row_map: MobilitiRowMap) -> dict[str, tuple[str, ...]]:
    root = _worksheet_root(payload, "Mobiliti")
    formulas_by_row = _formula_coordinates_by_row(root)
    result: dict[str, list[str]] = {}

    def add(source_row: int, target_rows: Sequence[int]) -> None:
        for source in formulas_by_row.get(source_row, ()):
            column = _CELL.fullmatch(source).group("column")
            destinations = result.setdefault(source, [])
            destinations.extend(f"{column}{target}" for target in target_rows)

    for index in range(CANONICAL_MOBILITI_SECTION_COUNT):
        source_header = CANONICAL_MOBILITI_FIRST_SECTION_ROW + index * CANONICAL_MOBILITI_BLOCK_HEIGHT
        source_product = source_header + 1
        source_subtotal = source_header + 34
        section = row_map.sections[index]
        add(source_header, (section.section_row,))
        for offset in range(CANONICAL_MOBILITI_PRODUCT_CAPACITY):
            add(source_product + offset, (section.product_start + offset,))
        add(source_subtotal, (section.subtotal_row,))
        if section.capacity > CANONICAL_MOBILITI_PRODUCT_CAPACITY:
            add(
                source_product,
                tuple(
                    range(
                        section.product_start + CANONICAL_MOBILITI_PRODUCT_CAPACITY,
                        section.product_start + section.capacity,
                    )
                ),
            )

    for section in row_map.sections[CANONICAL_MOBILITI_SECTION_COUNT:]:
        add(48, (section.section_row,))
        add(49, tuple(range(section.product_start, section.product_start + section.capacity)))
        add(82, (section.subtotal_row,))
    add(CANONICAL_MOBILITI_TOTAL_ROW, (row_map.total_row,))
    auxiliary_delta = row_map.total_row - CANONICAL_MOBILITI_TOTAL_ROW
    for source_row in range(CANONICAL_MOBILITI_AUX_START, CANONICAL_MOBILITI_AUX_END + 1):
        add(source_row, (source_row + auxiliary_delta,))
    return {source: tuple(dict.fromkeys(destinations)) for source, destinations in result.items()}


def _cotizacion_calc_map(
    payload: bytes,
    mutation: CotizacionSheetMutation,
) -> dict[str, tuple[str, ...]]:
    root = _worksheet_root(payload, "Cotizacion")
    formulas_by_row = _formula_coordinates_by_row(root)
    result: dict[str, tuple[str, ...]] = {}
    for source in formulas_by_row.get(17, ()):
        column = _CELL.fullmatch(source).group("column")
        result[source] = tuple(f"{column}{row}" for row in mutation.product_rows)
    total_delta = mutation.total_row - CANONICAL_COTIZACION_TOTAL_ROW
    for source_row in range(19, 25):
        for source in formulas_by_row.get(source_row, ()):
            column = _CELL.fullmatch(source).group("column")
            result[source] = (f"{column}{source_row + total_delta}",)
    for source_row, coordinates in formulas_by_row.items():
        if source_row < CANONICAL_COTIZACION_TERMS_START:
            continue
        for source in coordinates:
            column = _CELL.fullmatch(source).group("column")
            result[source] = (f"{column}{source_row + mutation.terms_row_delta}",)
    return result


def _validate_compose_paths(template: Path, output: Path) -> tuple[Path, Path]:
    for label, path in (("plantilla", template), ("salida", output)):
        if not path.is_absolute():
            raise ValueError(f"La ruta de {label} debe ser absoluta")
        if any(parent.exists() and parent.is_symlink() for parent in (path, *path.parents)):
            raise ValueError(f"La ruta de {label} no puede atravesar symlinks")
    if not template.exists() or not template.is_file():
        raise FileNotFoundError(f"Plantilla inexistente: {template}")
    if output.exists():
        raise FileExistsError(f"La salida ya existe: {output}")
    if not output.parent.exists() or not output.parent.is_dir():
        raise FileNotFoundError(f"Directorio de salida inexistente: {output.parent}")
    if template.resolve(strict=True) == output.resolve(strict=False):
        raise ValueError("La salida no puede reemplazar la plantilla")
    if output.suffix.casefold() != ".xlsx":
        raise ValueError("La salida debe ser un archivo .xlsx")
    return template.resolve(strict=True), output.resolve(strict=False)


def _candidate_path(output: Path) -> Path:
    for index in range(1, 10_001):
        candidate = output.with_name(f".{output.name}.compose-{index}.tmp")
        if not candidate.exists():
            return candidate
    raise FileExistsError("No se pudo reservar un candidato OOXML")


def _worksheet_root(payload: bytes, name: str) -> ET.Element:
    try:
        root = ET.fromstring(payload)
    except (ET.ParseError, TypeError) as error:
        raise ValueError(f"XML de {name} inválido") from error
    if root.tag != f"{{{MAIN}}}worksheet":
        raise ValueError(f"Parte de {name} no es worksheet")
    return root


def _replace_formula(root: ET.Element, coordinate: str, formula: str) -> None:
    cell = _cell_in_root(root, coordinate)
    if cell is None:
        raise ValueError(f"Celda oficial ausente: {coordinate}")
    _set_formula_element(cell, formula)


def _formula_at(package: XlsxPackage, sheet: str, coordinate: str) -> str | None:
    root = ET.fromstring(package.parts[package.sheet_part(sheet)])
    return _formula_in_root(root, coordinate)


def _formula_in_root(root: ET.Element, coordinate: str) -> str | None:
    cell = _cell_in_root(root, coordinate)
    if cell is None:
        return None
    formula = cell.find(f"{{{MAIN}}}f")
    return None if formula is None else formula.text or ""


def _cell_in_root(root: ET.Element, coordinate: str) -> ET.Element | None:
    return root.find(f".//{{{MAIN}}}c[@r='{coordinate}']")


def _clone_row_region(
    source: ET.Element,
    source_row: int,
    target_row: int,
    *,
    last_column: int,
) -> ET.Element:
    clone = deepcopy(source)
    clone.attrib["r"] = str(target_row)
    for cell in list(clone.findall(f"{{{MAIN}}}c")):
        match = _CELL.fullmatch(cell.attrib.get("r", ""))
        if match is None:
            raise ValueError("Coordenada inválida en Cotizacion oficial")
        column = match.group("column")
        if column_index_from_string(column) > last_column:
            clone.remove(cell)
            continue
        cell.attrib["r"] = f"{column}{target_row}"
    return clone


def _require_cell(row: ET.Element, coordinate: str) -> ET.Element:
    cell = next(
        (item for item in row.findall(f"{{{MAIN}}}c") if item.attrib.get("r") == coordinate),
        None,
    )
    if cell is None:
        match = _CELL.fullmatch(coordinate)
        if match is None or int(match.group("row")) != int(row.attrib["r"]):
            raise ValueError(f"Coordenada Cotizacion inválida: {coordinate}")
        cell = ET.Element(f"{{{MAIN}}}c", {"r": coordinate})
        row.append(cell)
        _sort_cells(row)
    return cell


def _set_inline_string(row: ET.Element, coordinate: str, value: str) -> None:
    _validate_text(value, allow_empty=True)
    cell = _require_cell(row, coordinate)
    _clear_cell_value(cell)
    cell.attrib["t"] = "inlineStr"
    inline = ET.SubElement(cell, f"{{{MAIN}}}is")
    text = ET.SubElement(inline, f"{{{MAIN}}}t")
    if value != value.strip():
        text.attrib[f"{{{XML}}}space"] = "preserve"
    text.text = value


def _set_number(row: ET.Element, coordinate: str, value: Decimal) -> None:
    number = _decimal(value, coordinate)
    cell = _require_cell(row, coordinate)
    _clear_cell_value(cell)
    cell.attrib.pop("t", None)
    ET.SubElement(cell, f"{{{MAIN}}}v").text = format(number, "f")


def _set_formula(
    row: ET.Element,
    coordinate: str,
    formula: str,
    *,
    attributes: Mapping[str, str] | None = None,
) -> None:
    cell = _require_cell(row, coordinate)
    _set_formula_element(cell, formula, attributes=attributes)


def _set_formula_element(
    cell: ET.Element,
    formula: str,
    *,
    attributes: Mapping[str, str] | None = None,
) -> None:
    if not isinstance(formula, str) or not formula.startswith("="):
        raise ValueError("Fórmula Cotizacion inválida")
    _clear_cell_value(cell)
    cell.attrib.pop("t", None)
    formula_node = ET.SubElement(cell, f"{{{MAIN}}}f", dict(attributes or {}))
    formula_node.text = formula[1:]


def _clear_cell_value(cell: ET.Element) -> None:
    for child in list(cell):
        if child.tag in {f"{{{MAIN}}}f", f"{{{MAIN}}}v", f"{{{MAIN}}}is"}:
            cell.remove(child)
    cell.attrib.pop("t", None)
    cell.attrib.pop("cm", None)
    cell.attrib.pop("vm", None)


def _formula_text(row: ET.Element, coordinate: str) -> str | None:
    cell = next(
        (item for item in row.findall(f"{{{MAIN}}}c") if item.attrib.get("r") == coordinate),
        None,
    )
    if cell is None:
        return None
    formula = cell.find(f"{{{MAIN}}}f")
    return None if formula is None else "=" + (formula.text or "")


def _sidecar_cells(
    rows: Mapping[int, ET.Element],
    *,
    first_row: int,
    first_sidecar_column: int,
) -> dict[int, tuple[ET.Element, ...]]:
    result: dict[int, tuple[ET.Element, ...]] = {}
    for number, row in rows.items():
        if number < first_row:
            continue
        cells = tuple(
            deepcopy(cell)
            for cell in row.findall(f"{{{MAIN}}}c")
            if column_index_from_string(_CELL.fullmatch(cell.attrib["r"]).group("column"))
            >= first_sidecar_column
        )
        if cells:
            result[number] = cells
    return result


def _apply_sidecars(
    rows: list[ET.Element],
    sidecars: Mapping[int, tuple[ET.Element, ...]],
) -> None:
    by_number = {int(row.attrib["r"]): row for row in rows}
    for number, cells in sidecars.items():
        row = by_number.get(number)
        if row is None:
            row = ET.Element(f"{{{MAIN}}}row", {"r": str(number)})
            rows.append(row)
            by_number[number] = row
        existing = {cell.attrib["r"] for cell in row.findall(f"{{{MAIN}}}c")}
        for cell in cells:
            if cell.attrib["r"] not in existing:
                row.append(deepcopy(cell))
        _sort_cells(row)


def _rebuild_cotizacion_merges(
    root: ET.Element,
    *,
    dynamic_merges: Sequence[str],
    total_delta: int,
) -> None:
    merges = root.find(f"{{{MAIN}}}mergeCells")
    if merges is None:
        raise ValueError("Cotizacion oficial no contiene mergeCells")
    preserved: list[ET.Element] = []
    for merge in merges.findall(f"{{{MAIN}}}mergeCell"):
        bounds = _range_bounds(merge.attrib.get("ref", ""))
        if bounds is None:
            raise ValueError("Merge Cotizacion inválido")
        min_col, min_row, max_col, max_row = bounds
        if min_col > 10:
            preserved.append(deepcopy(merge))
        elif max_col <= 10 and max_row < 16:
            preserved.append(deepcopy(merge))
        elif max_col <= 10 and 19 <= min_row <= 24:
            preserved.append(_shift_merge(merge, total_delta))
        elif max_col <= 10 and min_row >= 28:
            preserved.append(_shift_merge(merge, total_delta))
        elif max_col > 10:
            preserved.append(deepcopy(merge))
    preserved.extend(
        ET.Element(f"{{{MAIN}}}mergeCell", {"ref": reference})
        for reference in dynamic_merges
    )
    preserved.sort(key=lambda item: _range_bounds(item.attrib["ref"]) or (0, 0, 0, 0))
    merges[:] = preserved
    merges.attrib["count"] = str(len(preserved))


def _range_bounds(reference: str) -> tuple[int, int, int, int] | None:
    parts = reference.split(":")
    if len(parts) not in {1, 2}:
        return None
    first = _CELL.fullmatch(parts[0].replace("$", ""))
    last = _CELL.fullmatch(parts[-1].replace("$", ""))
    if first is None or last is None:
        return None
    return (
        column_index_from_string(first.group("column")),
        int(first.group("row")),
        column_index_from_string(last.group("column")),
        int(last.group("row")),
    )


def _shift_merge(merge: ET.Element, delta: int) -> ET.Element:
    clone = deepcopy(merge)
    bounds = _range_bounds(clone.attrib["ref"])
    if bounds is None:
        raise ValueError("Merge Cotizacion inválido")
    min_col, min_row, max_col, max_row = bounds
    clone.attrib["ref"] = (
        f"{get_column_letter(min_col)}{min_row + delta}:"
        f"{get_column_letter(max_col)}{max_row + delta}"
    )
    return clone


def _update_dimension(root: ET.Element, delta: int) -> None:
    dimension = root.find(f"{{{MAIN}}}dimension")
    if dimension is None:
        return
    reference = dimension.attrib.get("ref", "")
    if ":" not in reference:
        return
    first, last = reference.split(":", 1)
    match = _CELL.fullmatch(last.replace("$", ""))
    if match is None:
        raise ValueError("Dimensión oficial Cotizacion inválida")
    dimension.attrib["ref"] = (
        f"{first}:{match.group('column')}{int(match.group('row')) + delta}"
    )


def _sort_cells(row: ET.Element) -> None:
    cells = list(row.findall(f"{{{MAIN}}}c"))
    cells.sort(
        key=lambda cell: column_index_from_string(
            _CELL.fullmatch(cell.attrib["r"]).group("column")
        )
    )
    row[:] = cells


def _shift_anchor_rows(anchor: ET.Element, delta: int) -> None:
    if delta == 0:
        return
    for marker_name in ("from", "to"):
        marker = anchor.find(f"{{{XDR}}}{marker_name}")
        if marker is None:
            continue
        row = marker.find(f"{{{XDR}}}row")
        if row is not None and row.text is not None:
            target = int(row.text) + delta
            if target < 0:
                raise ValueError("Ancla Cotizacion fuera de rango")
            row.text = str(target)


def _next_picture_id(root: ET.Element) -> int:
    values = []
    for node in root.findall(f".//{{{XDR}}}cNvPr"):
        raw = node.attrib.get("id", "")
        if raw.isdigit():
            values.append(int(raw))
    return max(values, default=0) + 1


def _read_product_image(path: Path) -> tuple[bytes, str, str, int, int]:
    path = Path(path)
    if not path.is_absolute():
        raise ValueError("La imagen de producto debe usar ruta absoluta")
    if any(parent.exists() and parent.is_symlink() for parent in (path, *path.parents)):
        raise ValueError("La imagen de producto no puede atravesar symlinks")
    if not path.exists() or not path.is_file():
        raise FileNotFoundError(f"Imagen de producto inexistente: {path}")
    if not _SAFE_IMAGE_NAME.fullmatch(path.name):
        raise ValueError("Nombre de imagen de producto inválido")
    size = path.stat().st_size
    if size <= 0 or size > MAX_IMAGE_BYTES:
        raise ValueError("Tamaño de imagen de producto inválido")
    content = path.read_bytes()
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        extension, content_type = ".png", PNG_CONTENT_TYPE
    elif content.startswith(b"\xff\xd8\xff"):
        extension, content_type = ".jpeg", JPEG_CONTENT_TYPE
    else:
        raise ValueError("Formato de imagen de producto no permitido")
    try:
        from PIL import Image

        with Image.open(path) as image:
            width, height = image.size
            image.verify()
    except Exception as error:
        raise ValueError("Imagen de producto inválida") from error
    if width <= 0 or height <= 0 or width * height > MAX_IMAGE_PIXELS:
        raise ValueError("Dimensiones de imagen de producto inválidas")
    return content, extension, content_type, width, height


def _allocate_media_part(
    base: XlsxPackage,
    additions: Mapping[str, bytes],
    sequence: int,
    extension: str,
) -> str:
    suffix = sequence
    while True:
        candidate = f"xl/media/quote_product_{suffix:04d}{extension}"
        if candidate not in base.parts and candidate not in additions:
            return candidate
        suffix += 1


def _product_image_anchor(
    *,
    relationship_id: str,
    picture_id: int,
    target_row: int,
    width: int,
    height: int,
    name: str,
) -> ET.Element:
    max_width = 3_200_000
    max_height = 3_000_000
    scale = min(max_width / width, max_height / height)
    cx = max(1, int(width * scale))
    cy = max(1, int(height * scale))
    anchor = ET.Element(f"{{{XDR}}}oneCellAnchor")
    marker = ET.SubElement(anchor, f"{{{XDR}}}from")
    for tag, text in (("col", "1"), ("colOff", "0"), ("row", str(target_row - 1)), ("rowOff", "0")):
        ET.SubElement(marker, f"{{{XDR}}}{tag}").text = text
    ET.SubElement(anchor, f"{{{XDR}}}ext", {"cx": str(cx), "cy": str(cy)})
    picture = ET.SubElement(anchor, f"{{{XDR}}}pic")
    non_visual = ET.SubElement(picture, f"{{{XDR}}}nvPicPr")
    ET.SubElement(
        non_visual,
        f"{{{XDR}}}cNvPr",
        {"id": str(picture_id), "name": name},
    )
    ET.SubElement(non_visual, f"{{{XDR}}}cNvPicPr")
    fill = ET.SubElement(picture, f"{{{XDR}}}blipFill")
    ET.SubElement(fill, f"{{{DRAWING}}}blip", {f"{{{REL}}}embed": relationship_id})
    stretch = ET.SubElement(fill, f"{{{DRAWING}}}stretch")
    ET.SubElement(stretch, f"{{{DRAWING}}}fillRect")
    shape = ET.SubElement(picture, f"{{{XDR}}}spPr")
    transform = ET.SubElement(shape, f"{{{DRAWING}}}xfrm")
    ET.SubElement(transform, f"{{{DRAWING}}}off", {"x": "0", "y": "0"})
    ET.SubElement(transform, f"{{{DRAWING}}}ext", {"cx": str(cx), "cy": str(cy)})
    geometry = ET.SubElement(shape, f"{{{DRAWING}}}prstGeom", {"prst": "rect"})
    ET.SubElement(geometry, f"{{{DRAWING}}}avLst")
    ET.SubElement(anchor, f"{{{XDR}}}clientData")
    return anchor


def _allocate_worksheet_part(occupied: set[str], name: str) -> str:
    stem = "quotation_data" if name == "Quotation_Data" else "quotation"
    for index in range(1, 1_000_001):
        candidate = f"xl/worksheets/{stem}{index}.xml"
        if candidate not in occupied:
            return candidate
    raise ValueError("No se pudo asignar una parte worksheet")


def _ensure_content_type(root: ET.Element, part: str, content_type: str) -> None:
    validate_part_name(part)
    if not isinstance(content_type, str) or not content_type:
        raise ValueError("Content type inválido")
    part_name = "/" + part
    existing = next(
        (
            item
            for item in root.findall(f"{{{CONTENT_TYPES}}}Override")
            if item.attrib.get("PartName") == part_name
        ),
        None,
    )
    if existing is not None:
        if existing.attrib.get("ContentType") != content_type:
            raise ValueError(f"Content type en conflicto: {part}")
        return
    ET.SubElement(
        root,
        f"{{{CONTENT_TYPES}}}Override",
        {"PartName": part_name, "ContentType": content_type},
    )


def _next_relationship_id(used: set[str]) -> str:
    for index in range(1, 1_000_001):
        candidate = f"rId{index}"
        if candidate not in used:
            return candidate
    raise ValueError("No se pudo asignar un relationship Id")


def _merge_disjoint(target: dict[str, bytes], source: Mapping[str, bytes], label: str) -> None:
    overlap = set(target) & set(source)
    if overlap:
        raise ValueError(f"Colisión de {label}: {sorted(overlap)}")
    target.update(source)


def _merge_replacements(target: dict[str, bytes], source: Mapping[str, bytes]) -> None:
    for name, payload in source.items():
        if name in target and target[name] != payload:
            raise ValueError(f"Reemplazos incompatibles: {name}")
        target[name] = payload


def _immutable_bytes_mapping(value: Mapping[str, bytes], label: str) -> Mapping[str, bytes]:
    copied: dict[str, bytes] = {}
    for name, payload in value.items():
        validate_part_name(name)
        if not isinstance(payload, bytes):
            raise TypeError(f"{label} contiene bytes inválidos")
        copied[name] = payload
    return MappingProxyType(copied)


def _calc_chain_part(base: XlsxPackage) -> str | None:
    rels = ET.fromstring(base.parts["xl/_rels/workbook.xml.rels"])
    matches = [
        item
        for item in rels.findall(f"{{{PKG_REL}}}Relationship")
        if item.attrib.get("Type") in relationship_type_uris("calcChain")
    ]
    if not matches:
        return None
    if len(matches) != 1 or matches[0].attrib.get("TargetMode", "").casefold() == "external":
        raise ValueError("Relación calcChain inválida")
    return resolve_internal_target("xl/workbook.xml", matches[0].attrib["Target"])


def _workbook_sheet_id(base: XlsxPackage, name: str) -> int:
    workbook = ET.fromstring(base.parts["xl/workbook.xml"])
    matches = [
        item
        for item in workbook.findall(f"{{{MAIN}}}sheets/{{{MAIN}}}sheet")
        if item.attrib.get("name") == name
    ]
    if len(matches) != 1:
        raise ValueError(f"Hoja oficial inválida: {name}")
    return int(matches[0].attrib["sheetId"])


def _formula_coordinates_by_row(root: ET.Element) -> dict[int, tuple[str, ...]]:
    result: dict[int, list[str]] = {}
    for cell in root.findall(f".//{{{MAIN}}}c"):
        if cell.find(f"{{{MAIN}}}f") is None:
            continue
        coordinate = cell.attrib.get("r", "")
        match = _CELL.fullmatch(coordinate)
        if match is None:
            raise ValueError("Coordenada de fórmula oficial inválida")
        result.setdefault(int(match.group("row")), []).append(coordinate)
    return {row: tuple(coordinates) for row, coordinates in result.items()}


def _validate_text(value: object, *, allow_empty: bool = False) -> None:
    if not isinstance(value, str):
        raise TypeError("Texto de Cotizacion inválido")
    if not allow_empty and not value:
        raise ValueError("Texto de Cotizacion vacío")
    if len(value) > MAX_CELL_TEXT or any(not _is_xml_character(ord(char)) for char in value):
        raise ValueError("Texto de Cotizacion no representable en XLSX")


def _decimal(value: object, field_name: str) -> Decimal:
    if isinstance(value, bool):
        raise TypeError(f"{field_name} inválido")
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except Exception as error:
        raise TypeError(f"{field_name} inválido") from error
    if not result.is_finite():
        raise ValueError(f"{field_name} no finito")
    return result


def _is_xml_character(codepoint: int) -> bool:
    return (
        codepoint in {0x9, 0xA, 0xD}
        or 0x20 <= codepoint <= 0xD7FF
        or 0xE000 <= codepoint <= 0xFFFD
        or 0x10000 <= codepoint <= 0x10FFFF
    )
