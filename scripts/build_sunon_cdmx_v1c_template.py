"""Construye localmente la plantilla Sunon CDMX V1C sin tocar sus fuentes."""

from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OFFICIAL = (
    ROOT
    / "mobiliti_saas"
    / "worker"
    / "templates"
    / "Formato Cotizacion 2026 Oficial.xlsx"
)
DEFAULT_VISUAL_SOURCE = (
    Path.home() / "Downloads" / "Formato-Cotizacion-Unico - Sunon-Cdmx-V1C.xlsx"
)
DEFAULT_OUTPUT = (
    ROOT
    / "mobiliti_saas"
    / "worker"
    / "templates"
    / "Formato Cotizacion Sunon CDMX V1C.xlsx"
)
DEFAULT_CONTRACT = (
    ROOT
    / "mobiliti_saas"
    / "worker"
    / "templates"
    / "formato-cotizacion-sunon-cdmx-v1c.contract.json"
)
OFFICIAL_CONTRACT = (
    ROOT
    / "mobiliti_saas"
    / "worker"
    / "templates"
    / "formato-cotizacion-2026-oficial.contract.json"
)
OFFICIAL_SHA256 = "25f79e3ae533aa8f560be3e80586c19993ea65c0a07c500eb458738f9915b251"
XL_LINK_TYPE_EXCEL = 1
XL_PASTE_FORMATS = -4122


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _same_path(left: Path, right: Path) -> bool:
    left_normalized = os.path.normcase(str(left.resolve(strict=False)))
    right_normalized = os.path.normcase(str(right.resolve(strict=False)))
    if left_normalized == right_normalized:
        return True
    if left.exists() and right.exists():
        try:
            return os.path.samefile(left, right)
        except OSError:
            pass
    return False


def _fsync_file(path: Path) -> None:
    with path.open("r+b") as stream:
        os.fsync(stream.fileno())


def _backup_name(path: Path) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    candidate = path.with_name(f"{path.name}.backup-{timestamp}")
    suffix = 1
    while candidate.exists():
        candidate = path.with_name(f"{path.name}.backup-{timestamp}-{suffix}")
        suffix += 1
    return candidate


def _prepare_destination_backups(
    output: Path,
    contract: Path,
    rebuild: bool,
) -> dict[Path, Path]:
    existing = [path for path in (output, contract) if path.exists()]
    if existing and not rebuild:
        raise FileExistsError(
            "El destino ya existe; use --rebuild para conservarlo como respaldo: "
            + ", ".join(str(path) for path in existing)
        )
    backups: dict[Path, Path] = {}
    for path in existing:
        backup = _backup_name(path)
        shutil.copy2(path, backup)
        backups[path] = backup
    return backups


def _preserve_failed_file(path: Path, label: str) -> Path | None:
    if not path.exists():
        return None
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    preserved = path.with_name(f"{path.name}.{label}-{timestamp}-{uuid4().hex}")
    path.replace(preserved)
    return preserved


def _restore_published_pair(
    destinations: tuple[Path, Path],
    backups: dict[Path, Path],
) -> list[Path]:
    """Revierte una publicación parcial sin eliminar evidencia del fallo."""

    preserved: list[Path] = []
    for destination in destinations:
        if destination.exists():
            failed = _preserve_failed_file(destination, "failed-publication")
            if failed is not None:
                preserved.append(failed)
        backup = backups.get(destination)
        if backup is not None and backup.exists():
            shutil.copy2(backup, destination)
    return preserved


def _copy_formats(
    source_sheet,
    target_sheet,
    source_address: str,
    target_address: str,
) -> None:
    source_sheet.Range(source_address).Copy()
    target_sheet.Range(target_address).PasteSpecial(Paste=XL_PASTE_FORMATS)


def _copy_all(
    source_sheet,
    target_sheet,
    source_address: str,
    target_address: str,
) -> None:
    """Copia contenido y presentación con Excel nativo.

    ``Range.Copy`` conserva fórmulas, valores, rich text, hipervínculos y
    estilos que no pueden reproducirse fielmente asignando ``Value``.
    """

    source_sheet.Range(source_address).Copy(
        Destination=target_sheet.Range(target_address)
    )


def _copy_row_heights(
    source_sheet,
    target_sheet,
    source_start: int,
    target_start: int,
    count: int,
) -> None:
    for offset in range(count):
        target_sheet.Rows(target_start + offset).RowHeight = source_sheet.Rows(
            source_start + offset
        ).RowHeight


def _copy_column_widths(source_sheet, target_sheet) -> None:
    for column in range(1, 11):
        target_sheet.Columns(column).ColumnWidth = source_sheet.Columns(
            column
        ).ColumnWidth


def _copy_page_presentation(source_sheet, target_sheet, print_area: str) -> None:
    source = source_sheet.PageSetup
    target = target_sheet.PageSetup
    for name in (
        "Orientation",
        "PaperSize",
        "LeftMargin",
        "RightMargin",
        "TopMargin",
        "BottomMargin",
        "HeaderMargin",
        "FooterMargin",
        "CenterHorizontally",
        "CenterVertically",
        "PrintGridlines",
        "PrintHeadings",
        "Order",
    ):
        try:
            setattr(target, name, getattr(source, name))
        except Exception:
            # Algunas propiedades dependen de la impresora activa. El formato
            # esencial ya se copia por rangos; una impresora ausente no debe
            # volver imposible construir el activo local.
            continue
    try:
        target.Zoom = False
        target.FitToPagesWide = 1
        target.FitToPagesTall = False
    except Exception:
        pass
    target.PrintArea = print_area


def _replace_merge(sheet, address: str) -> None:
    """Aplica un merge visual sin dejar merges parciales superpuestos."""

    try:
        sheet.Range(address).UnMerge()
    except Exception:
        pass
    sheet.Range(address).Merge()


def _copy_single_logo(source_sheet, target_sheet) -> None:
    """Sustituye los dibujos canónicos por el único logo de la fuente CDMX."""

    pictures = [
        source_sheet.Shapes.Item(index)
        for index in range(1, source_sheet.Shapes.Count + 1)
        if source_sheet.Shapes.Item(index).Type == 13
    ]
    if len(pictures) != 1:
        raise ValueError(
            "La referencia CDMX debe contener exactamente un logo en Cotizacion"
        )
    for index in range(target_sheet.Shapes.Count, 0, -1):
        target_sheet.Shapes.Item(index).Delete()

    source_logo = pictures[0]
    source_sheet.Activate()
    source_logo.Copy()
    target_sheet.Parent.Activate()
    target_sheet.Activate()
    target_sheet.Paste()
    target_logo = target_sheet.Shapes.Item(target_sheet.Shapes.Count)
    for attribute in ("Left", "Top", "Width", "Height", "Placement"):
        try:
            setattr(target_logo, attribute, getattr(source_logo, attribute))
        except Exception:
            continue


def _copy_cotizacion_presentation(source_sheet, target_sheet) -> None:
    # La fuente CDMX usa otro layout lógico. Se traslada su presentación a las
    # filas canónicas que el motor ya conoce y se conservan íntegros los bloques
    # propios de CDMX: subtotal por área y condiciones comerciales.
    _copy_formats(source_sheet, target_sheet, "A2:J2", "A3:J3")
    _copy_formats(source_sheet, target_sheet, "A3:J3", "A4:J4")
    _copy_formats(source_sheet, target_sheet, "A4:J4", "A7:J7")
    _copy_formats(source_sheet, target_sheet, "A8:J8", "A8:J9")
    _copy_formats(source_sheet, target_sheet, "A7:J7", "A10:J10")
    _copy_formats(source_sheet, target_sheet, "A6:J6", "A11:J11")
    _copy_formats(source_sheet, target_sheet, "A5:J5", "A12:J12")
    _copy_formats(source_sheet, target_sheet, "A9:J9", "A14:J14")

    _copy_formats(source_sheet, target_sheet, "A12:J12", "A15:J15")
    _copy_formats(source_sheet, target_sheet, "A11:J11", "A16:J16")
    _copy_formats(source_sheet, target_sheet, "A13:J13", "A17:J17")
    _copy_all(source_sheet, target_sheet, "A16:J16", "A18:J18")
    # Excel traduce referencias relativas al copiar hacia otra fila. Este
    # renglón es un prototipo firmado, por lo que debe conservar literalmente
    # la fórmula oficial; el compositor la sustituye por el rango real.
    target_sheet.Range("J18").Formula = source_sheet.Range("J16").Formula
    _copy_formats(source_sheet, target_sheet, "A27:J31", "A20:J24")
    _copy_all(source_sheet, target_sheet, "A35:J83", "A28:J76")

    _copy_row_heights(source_sheet, target_sheet, 12, 15, 1)
    _copy_row_heights(source_sheet, target_sheet, 11, 16, 1)
    _copy_row_heights(source_sheet, target_sheet, 13, 17, 1)
    _copy_row_heights(source_sheet, target_sheet, 16, 18, 1)
    _copy_row_heights(source_sheet, target_sheet, 27, 20, 5)
    _copy_row_heights(source_sheet, target_sheet, 35, 28, 49)
    _copy_column_widths(source_sheet, target_sheet)
    _copy_page_presentation(source_sheet, target_sheet, "$A$1:$J$76")

    _replace_merge(target_sheet, "A14:J14")
    _replace_merge(target_sheet, "A16:J16")
    _replace_merge(target_sheet, "B11:G11")
    _replace_merge(target_sheet, "B12:G12")
    _copy_single_logo(source_sheet, target_sheet)

    target_sheet.Activate()
    target_sheet.Application.ActiveWindow.Zoom = 40


def _copy_lumbro_surfaces(source_workbook, target_workbook) -> None:
    after = target_workbook.Worksheets(target_workbook.Worksheets.Count)
    source_workbook.Worksheets("Cantidades Lumbro ").Copy(None, after)

    sheet = target_workbook.Worksheets("Cantidades Lumbro ")
    sheet.UsedRange.ClearContents()
    for index in range(sheet.Shapes.Count, 0, -1):
        sheet.Shapes.Item(index).Delete()


def _populate_live_lumbro_quantities(workbook) -> None:
    """Restaura la superficie visual con referencias vivas a ``Mobiliti``.

    El formato recibido contenía datos de muestra y dividía entre una tasa
    fija. Se sustituye ese contenido por matrices dinámicas que enumeran todos
    los renglones Lumbro disponibles, sin truncarlos a doce espacios.
    """

    sheet = workbook.Worksheets("Cantidades Lumbro ")
    sheet.Range("H2:P40").ClearContents()
    sheet.Range("H4:P4").Copy()
    sheet.Range("H4:P28").PasteSpecial(Paste=XL_PASTE_FORMATS)
    workbook.Application.CutCopyMode = False
    sheet.Rows("4:28").RowHeight = 18
    sheet.PageSetup.PrintArea = "$H$1:$P$28"
    sheet.Range("M2").Value = 0.40
    sheet.Range("P2").Value = "TOTAL GENERAL"
    sheet.Range("I3").Value = "# ESTACIONES"
    sheet.Range("J3").Value = "QTY x est"
    sheet.Range("K3").Value = "Total"
    sheet.Range("L3").Value = "Costo unitario"
    sheet.Range("M3").Formula = '="PRECIO +"&TEXT($M$2,"0%")'
    sheet.Range("N3").Value = "Total"
    sheet.Range("P3").Value = "TOTALES VENTA"

    lumbro_predicate = (
        'ISNUMBER(SEARCH("Lumbro",Mobiliti!$F$14:$F$5000))'
    )
    sheet.Range("H4").Formula2 = (
        "=FILTER(Mobiliti!$D$14:$D$5000,"
        f'{lumbro_predicate},"")'
    )
    sheet.Range("I4").Formula2 = (
        "=FILTER(Mobiliti!$H$14:$H$5000,"
        f'{lumbro_predicate},"")'
    )
    sheet.Range("J4").Formula2 = '=IF(H4#<>"",1,"")'
    sheet.Range("K4").Formula2 = '=IF(H4#="","",I4#*J4#)'
    sheet.Range("L4").Formula2 = (
        "=FILTER(Mobiliti!$J$14:$J$5000,"
        f'{lumbro_predicate},"")'
    )
    sheet.Range("M4").Formula2 = '=IF(L4#="","",L4#/(1-$M$2))'
    sheet.Range("N4").Formula2 = '=IF(H4#="","",M4#*K4#)'
    sheet.Range("P4").Formula2 = '=SUM(N4#)'
    sheet.Range("P4").Copy()
    sheet.Range("P4:P28").PasteSpecial(Paste=XL_PASTE_FORMATS)
    workbook.Application.CutCopyMode = False
    sheet.Columns("O").Hidden = True


def _break_only_visual_source_links(workbook, visual_source: Path) -> None:
    links = workbook.LinkSources(XL_LINK_TYPE_EXCEL)
    if not links:
        return
    source_name = visual_source.name.casefold()
    source_path = os.path.normcase(str(visual_source.resolve()))
    for link in tuple(links):
        link_text = str(link)
        normalized = os.path.normcase(os.path.abspath(link_text))
        if Path(link_text).name.casefold() == source_name or normalized == source_path:
            workbook.BreakLink(Name=link_text, Type=XL_LINK_TYPE_EXCEL)


def _build_with_excel(
    official: Path,
    visual_source: Path,
    candidate: Path,
) -> None:
    try:
        import pythoncom
        import win32com.client
    except ImportError as error:
        raise RuntimeError("Microsoft Excel y pywin32 son obligatorios") from error

    shutil.copyfile(official, candidate)
    pythoncom.CoInitialize()
    application = None
    workbooks = []
    try:
        application = win32com.client.DispatchEx("Excel.Application")
        application.Visible = False
        application.DisplayAlerts = False
        application.EnableEvents = False
        application.AskToUpdateLinks = False
        try:
            application.AutomationSecurity = 3
        except Exception:
            pass

        target = application.Workbooks.Open(
            str(candidate),
            0,
            False,
            None,
            None,
            None,
            True,
            None,
            None,
            None,
            False,
            None,
            False,
        )
        workbooks.append(target)
        source = application.Workbooks.Open(
            str(visual_source),
            0,
            True,
            None,
            None,
            None,
            True,
            None,
            None,
            None,
            False,
            None,
            False,
        )
        workbooks.append(source)

        _copy_cotizacion_presentation(
            source.Worksheets("Cotizacion"),
            target.Worksheets("Cotizacion"),
        )
        _copy_lumbro_surfaces(source, target)
        _populate_live_lumbro_quantities(target)
        _break_only_visual_source_links(target, visual_source)
        application.CutCopyMode = False
        target.Save()
    finally:
        for workbook in reversed(workbooks):
            try:
                workbook.Close(SaveChanges=False)
            except Exception:
                pass
        if application is not None:
            try:
                application.Quit()
            except Exception:
                pass
        pythoncom.CoUninitialize()


def _contract_payload(output: Path) -> dict[str, object]:
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from mobiliti_saas.quote_engine.official_template import (
        inspect_template,
        load_template_contract,
    )

    inspection = inspect_template(output)
    official = load_template_contract(OFFICIAL_CONTRACT)
    return {
        "sha256": inspection.sha256,
        "defined_name_count": inspection.defined_name_count,
        "external_link_parts": inspection.external_link_parts,
        "spec_formula_count": inspection.spec_formula_count,
        "sheet_states": inspection.sheet_states,
        "mutable_sheets": ["Mobiliti", "Cotizacion", "Cantidades Lumbro "],
        "mutable_cells": {
            name: list(cells) for name, cells in official.mutable_cells.items()
        },
        "mutable_drawing_regions": {
            name: list(regions)
            for name, regions in official.mutable_drawing_regions.items()
        },
        "addable_sheets": list(official.addable_sheets),
        "protected_prefixes": list(official.protected_prefixes),
        "translated_parts": list(official.translated_parts),
    }


def build(
    official: Path,
    visual_source: Path,
    output: Path,
    contract: Path,
    *,
    rebuild: bool = False,
) -> dict[str, object]:
    official = official.resolve()
    visual_source = visual_source.resolve()
    output = output.resolve()
    contract = contract.resolve()

    if not official.is_file():
        raise FileNotFoundError(f"Plantilla oficial ausente: {official}")
    if not visual_source.is_file():
        raise FileNotFoundError(f"Referencia CDMX ausente: {visual_source}")
    if _same_path(output, contract):
        raise ValueError("El XLSX y su contrato requieren destinos distintos")
    if output.suffix.casefold() != ".xlsx":
        raise ValueError("La plantilla de salida debe usar extensión .xlsx")
    if contract.suffix.casefold() != ".json":
        raise ValueError("El contrato de salida debe usar extensión .json")
    if any(
        _same_path(destination, source)
        for destination in (output, contract)
        for source in (official, visual_source)
    ):
        raise ValueError("Los destinos no pueden reemplazar las fuentes")
    if _sha256(official) != OFFICIAL_SHA256:
        raise ValueError("La plantilla oficial no coincide con el hash congelado")
    if not rebuild:
        existing = [path for path in (output, contract) if path.exists()]
        if existing:
            raise FileExistsError(
                "El destino ya existe; use --rebuild para conservarlo como respaldo: "
                + ", ".join(str(path) for path in existing)
            )

    output.parent.mkdir(parents=True, exist_ok=True)
    contract.parent.mkdir(parents=True, exist_ok=True)
    candidate = output.with_name(f".{output.name}.building-{uuid4().hex}.xlsx")
    contract_candidate = contract.with_name(
        f".{contract.name}.building-{uuid4().hex}.json"
    )
    backups: dict[Path, Path] = {}
    publication_started = False
    try:
        _build_with_excel(official, visual_source, candidate)
        payload = _contract_payload(candidate)
        contract_candidate.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        parsed_contract = json.loads(contract_candidate.read_text(encoding="utf-8"))
        if parsed_contract.get("sha256") != _sha256(candidate):
            raise RuntimeError("El contrato candidato no coincide con su plantilla")
        if _sha256(official) != OFFICIAL_SHA256:
            raise RuntimeError("La plantilla oficial cambió durante la construcción")
        _fsync_file(candidate)
        _fsync_file(contract_candidate)

        backups = _prepare_destination_backups(output, contract, rebuild)
        publication_started = True
        os.replace(candidate, output)
        os.replace(contract_candidate, contract)

        published_contract = json.loads(contract.read_text(encoding="utf-8"))
        if published_contract.get("sha256") != _sha256(output):
            raise RuntimeError("El par publicado no coincide en SHA-256")
        if _sha256(official) != OFFICIAL_SHA256:
            raise RuntimeError("La plantilla oficial cambió durante la construcción")
        return payload
    except Exception:
        if publication_started:
            _restore_published_pair((output, contract), backups)
        _preserve_failed_file(candidate, "failed-build")
        _preserve_failed_file(contract_candidate, "failed-build")
        raise


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--official", type=Path, default=DEFAULT_OFFICIAL)
    parser.add_argument("--visual-source", type=Path, default=DEFAULT_VISUAL_SOURCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    parser.add_argument(
        "--rebuild",
        action="store_true",
        help="Conserva destinos existentes como respaldos antes de reconstruir.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    payload = build(
        arguments.official,
        arguments.visual_source,
        arguments.output,
        arguments.contract,
        rebuild=arguments.rebuild,
    )
    print(f"Plantilla CDMX creada: {arguments.output}")
    print(f"Contrato CDMX creado: {arguments.contract}")
    print(f"SHA-256: {payload['sha256']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
