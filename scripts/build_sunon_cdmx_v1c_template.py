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
import time
from uuid import uuid4
from xml.parsers import expat


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
OFFICIAL_SHA256 = "39f5cebd3cbe3e7356f4d4174161e8599bf7158e7b495a789c9fc04850928ee4"
XL_LINK_TYPE_EXCEL = 1
XL_PASTE_FORMATS = -4122
RPC_E_CALL_REJECTED = -2147418111
XLSX_MAX_ROW = 1_048_576
CDMX_PRINT_END_ROW = 76
WORKBOOK_PART = "xl/workbook.xml"
MC_NAMESPACE = "http://schemas.openxmlformats.org/markup-compatibility/2006"
X15AC_NAMESPACE = (
    "http://schemas.microsoft.com/office/spreadsheetml/2010/11/ac"
)
MC_ALTERNATE_CONTENT = f"{MC_NAMESPACE}}}AlternateContent"
X15AC_ABS_PATH = f"{X15AC_NAMESPACE}}}absPath"
LOCAL_USER_PATH_MARKER = b"c:\\users\\"


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
    retry_com,
) -> None:
    retry_com(lambda: source_sheet.Range(source_address).Copy())
    retry_com(
        lambda: target_sheet.Range(target_address).PasteSpecial(
            Paste=XL_PASTE_FORMATS
        )
    )


def _copy_all(
    source_sheet,
    target_sheet,
    source_address: str,
    target_address: str,
    retry_com,
) -> None:
    """Copia contenido y presentación con Excel nativo.

    ``Range.Copy`` conserva fórmulas, valores, rich text, hipervínculos y
    estilos que no pueden reproducirse fielmente asignando ``Value``.
    """

    retry_com(
        lambda: source_sheet.Range(source_address).Copy(
            Destination=target_sheet.Range(target_address)
        )
    )


def _copy_formula_with_retry(
    source_sheet,
    target_sheet,
    source_address: str,
    target_address: str,
    retry_com,
) -> None:
    formula = retry_com(lambda: source_sheet.Range(source_address).Formula)
    retry_com(
        lambda: setattr(
            target_sheet.Range(target_address),
            "Formula",
            formula,
        )
    )


def _copy_row_heights(
    source_sheet,
    target_sheet,
    source_start: int,
    target_start: int,
    count: int,
    retry_com,
) -> None:
    for offset in range(count):
        source_row = source_start + offset
        target_row = target_start + offset
        row_height = retry_com(
            lambda: source_sheet.Rows(source_row).RowHeight
        )
        retry_com(
            lambda: setattr(
                target_sheet.Rows(target_row),
                "RowHeight",
                row_height,
            )
        )


def _clear_cotizacion_tail(
    target_sheet,
    retry_com,
    *,
    print_end: int = CDMX_PRINT_END_ROW,
) -> None:
    """Vacía sólo A:J fuera del layout CDMX y conserva sidecars K+."""

    used_range = retry_com(lambda: target_sheet.UsedRange)
    first_used_row = retry_com(lambda: used_range.Row)
    used_row_count = retry_com(lambda: used_range.Rows.Count)
    if (
        type(first_used_row) is not int
        or type(used_row_count) is not int
        or first_used_row < 1
        or used_row_count < 1
    ):
        raise ValueError("UsedRange de Cotizacion inválido")
    last_used_row = first_used_row + used_row_count - 1
    if last_used_row > XLSX_MAX_ROW:
        raise ValueError("UsedRange de Cotizacion excede XLSX")
    if last_used_row <= print_end:
        return

    tail = retry_com(
        lambda: target_sheet.Range(f"A{print_end + 1}:J{last_used_row}")
    )
    # Son mutaciones sobre el candidato: si Excel las rechaza, el build falla
    # y se rehace completo; nunca se reejecutan sobre estado parcial.
    tail.UnMerge()
    tail.ClearContents()


def _clear_cotizacion_residue(target_sheet, retry_com) -> None:
    """Vacía payload oficial A19:J27 sin tocar estilos, J18 ni sidecars K+."""

    residue = retry_com(lambda: target_sheet.Range("A19:J27"))
    # Mutación no idempotente frente a una respuesta COM ambigua: si falla,
    # se descarta el candidato completo en vez de repetirla.
    residue.ClearContents()


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


def _copy_single_logo(source_sheet, target_sheet, retry_com) -> None:
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
    retry_com(source_logo.Copy)
    target_sheet.Parent.Activate()
    target_sheet.Activate()
    target_sheet.Paste()
    target_logo = target_sheet.Shapes.Item(target_sheet.Shapes.Count)
    for attribute in ("Left", "Top", "Width", "Height", "Placement"):
        try:
            setattr(target_logo, attribute, getattr(source_logo, attribute))
        except Exception:
            continue


def _copy_cotizacion_presentation(
    source_sheet,
    target_sheet,
    retry_com,
) -> None:
    # La fuente CDMX usa otro layout lógico. Se traslada su presentación a las
    # filas canónicas que el motor ya conoce y se conservan íntegros los bloques
    # propios de CDMX: subtotal por área y condiciones comerciales.
    _copy_formats(source_sheet, target_sheet, "A2:J2", "A3:J3", retry_com)
    _copy_formats(source_sheet, target_sheet, "A3:J3", "A4:J4", retry_com)
    _copy_formats(source_sheet, target_sheet, "A4:J4", "A7:J7", retry_com)
    _copy_formats(source_sheet, target_sheet, "A8:J8", "A8:J9", retry_com)
    _copy_formats(source_sheet, target_sheet, "A7:J7", "A10:J10", retry_com)
    _copy_formats(source_sheet, target_sheet, "A6:J6", "A11:J11", retry_com)
    _copy_formats(source_sheet, target_sheet, "A5:J5", "A12:J12", retry_com)
    _copy_formats(source_sheet, target_sheet, "A9:J9", "A14:J14", retry_com)

    _copy_formats(source_sheet, target_sheet, "A12:J12", "A15:J15", retry_com)
    _copy_formats(source_sheet, target_sheet, "A11:J11", "A16:J16", retry_com)
    _copy_formats(source_sheet, target_sheet, "A13:J13", "A17:J17", retry_com)
    _copy_all(source_sheet, target_sheet, "A16:J16", "A18:J18", retry_com)
    # Excel traduce referencias relativas al copiar hacia otra fila. Este
    # renglón es un prototipo firmado, por lo que debe conservar literalmente
    # la fórmula oficial; el compositor la sustituye por el rango real.
    _copy_formula_with_retry(
        source_sheet,
        target_sheet,
        "J16",
        "J18",
        retry_com,
    )
    _copy_formats(source_sheet, target_sheet, "A27:J31", "A20:J24", retry_com)
    _clear_cotizacion_residue(target_sheet, retry_com)
    _copy_all(source_sheet, target_sheet, "A35:J83", "A28:J76", retry_com)
    _clear_cotizacion_tail(target_sheet, retry_com)

    _copy_row_heights(source_sheet, target_sheet, 12, 15, 1, retry_com)
    _copy_row_heights(source_sheet, target_sheet, 11, 16, 1, retry_com)
    _copy_row_heights(source_sheet, target_sheet, 13, 17, 1, retry_com)
    _copy_row_heights(source_sheet, target_sheet, 16, 18, 1, retry_com)
    _copy_row_heights(source_sheet, target_sheet, 27, 20, 5, retry_com)
    _copy_row_heights(source_sheet, target_sheet, 35, 28, 49, retry_com)
    _copy_column_widths(source_sheet, target_sheet)
    _copy_page_presentation(source_sheet, target_sheet, "$A$1:$J$76")

    _replace_merge(target_sheet, "A14:J14")
    _replace_merge(target_sheet, "A16:J16")
    _replace_merge(target_sheet, "B11:G11")
    _replace_merge(target_sheet, "B12:G12")
    _copy_single_logo(source_sheet, target_sheet, retry_com)

    target_sheet.Activate()
    target_sheet.Application.ActiveWindow.Zoom = 40


def _copy_cotizacion_from_workbooks(
    source_workbook,
    target_workbook,
    retry_com,
) -> None:
    source_sheet = retry_com(
        lambda: source_workbook.Worksheets("Cotizacion")
    )
    target_sheet = retry_com(
        lambda: target_workbook.Worksheets("Cotizacion")
    )
    _copy_cotizacion_presentation(source_sheet, target_sheet, retry_com)


def _copy_lumbro_surfaces(
    source_workbook,
    target_workbook,
    retry_com,
) -> None:
    target_count = retry_com(lambda: target_workbook.Worksheets.Count)
    after = retry_com(lambda: target_workbook.Worksheets(target_count))
    source_sheet = retry_com(
        lambda: source_workbook.Worksheets("Cantidades Lumbro ")
    )
    source_sheet.Copy(None, after)

    sheet = retry_com(
        lambda: target_workbook.Worksheets("Cantidades Lumbro ")
    )
    sheet.UsedRange.ClearContents()
    shape_count = retry_com(lambda: sheet.Shapes.Count)
    for index in range(shape_count, 0, -1):
        shape = retry_com(lambda index=index: sheet.Shapes.Item(index))
        shape.Delete()


def _populate_live_lumbro_quantities(workbook, retry_com) -> None:
    """Restaura la superficie visual con referencias vivas a ``Mobiliti``.

    El formato recibido contenía datos de muestra y dividía entre una tasa
    fija. Se sustituye ese contenido por matrices dinámicas que enumeran todos
    los renglones Lumbro disponibles, sin truncarlos a doce espacios.
    """

    sheet = retry_com(lambda: workbook.Worksheets("Cantidades Lumbro "))
    sheet.Range("H2:P40").ClearContents()
    _copy_formats(sheet, sheet, "H4:P4", "H4:P28", retry_com)
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
    _copy_formats(sheet, sheet, "P4", "P4:P28", retry_com)
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


def _retry_rejected_com(
    action,
    *,
    com_error_type,
    max_attempts: int = 5,
    sleep=time.sleep,
    pump_waiting_messages=None,
):
    """Reintenta sólo rechazos transitorios de llamadas COM idempotentes."""

    for attempt in range(max_attempts):
        try:
            return action()
        except com_error_type as error:
            if (
                error.hresult != RPC_E_CALL_REJECTED
                or attempt == max_attempts - 1
            ):
                raise
            if pump_waiting_messages is not None:
                try:
                    pump_waiting_messages()
                except Exception as pump_error:
                    raise error from pump_error
            sleep(0.25)


def _open_workbook_with_retry(
    workbooks,
    *open_args,
    com_error_type,
    max_attempts: int = 5,
    sleep=time.sleep,
    pump_waiting_messages=None,
):
    """Abre un workbook usando el retry COM acotado."""

    return _retry_rejected_com(
        lambda: workbooks.Open(*open_args),
        com_error_type=com_error_type,
        max_attempts=max_attempts,
        sleep=sleep,
        pump_waiting_messages=pump_waiting_messages,
    )


def _build_with_excel(
    official: Path,
    visual_source: Path,
    candidate: Path,
) -> None:
    try:
        import pythoncom
        import pywintypes
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

        def retry_com(action):
            return _retry_rejected_com(
                action,
                com_error_type=pywintypes.com_error,
                pump_waiting_messages=getattr(
                    pythoncom,
                    "PumpWaitingMessages",
                    None,
                ),
            )

        target = _open_workbook_with_retry(
            application.Workbooks,
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
            com_error_type=pywintypes.com_error,
            pump_waiting_messages=getattr(
                pythoncom,
                "PumpWaitingMessages",
                None,
            ),
        )
        workbooks.append(target)
        source = _open_workbook_with_retry(
            application.Workbooks,
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
            com_error_type=pywintypes.com_error,
            pump_waiting_messages=getattr(
                pythoncom,
                "PumpWaitingMessages",
                None,
            ),
        )
        workbooks.append(source)

        _copy_cotizacion_from_workbooks(source, target, retry_com)
        _copy_lumbro_surfaces(source, target, retry_com)
        _populate_live_lumbro_quantities(target, retry_com)
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


def _private_alternate_content_spans(
    workbook_xml: bytes,
) -> tuple[tuple[int, int], ...]:
    """Localiza bloques privados por nombre expandido sin reserializar XML."""

    if not isinstance(workbook_xml, bytes):
        raise TypeError("workbook.xml debe recibirse como bytes")
    parser = expat.ParserCreate(namespace_separator="}")
    stack: list[dict[str, object]] = []
    spans: list[tuple[int, int]] = []
    unscoped_abs_path = False

    def start_element(name: str, _attributes: dict[str, str]) -> None:
        nonlocal unscoped_abs_path
        frame: dict[str, object] = {
            "name": name,
            "start": parser.CurrentByteIndex,
            "private": False,
        }
        stack.append(frame)
        if name != X15AC_ABS_PATH:
            return
        for ancestor in reversed(stack[:-1]):
            if ancestor["name"] == MC_ALTERNATE_CONTENT:
                ancestor["private"] = True
                break
        else:
            unscoped_abs_path = True

    def end_element(name: str) -> None:
        frame = stack.pop()
        if frame["name"] != name:
            raise ValueError("workbook.xml tiene una estructura inconsistente")
        if name != MC_ALTERNATE_CONTENT or not frame["private"]:
            return
        closing_end = workbook_xml.find(b">", parser.CurrentByteIndex)
        if closing_end < 0:
            raise ValueError("No se pudo delimitar mc:AlternateContent")
        spans.append((int(frame["start"]), closing_end + 1))

    parser.StartElementHandler = start_element
    parser.EndElementHandler = end_element
    try:
        parser.Parse(workbook_xml, True)
    except (expat.ExpatError, IndexError) as error:
        raise ValueError("workbook.xml no es XML válido") from error
    if unscoped_abs_path:
        raise ValueError(
            "x15ac:absPath privado está fuera de mc:AlternateContent"
        )
    return tuple(spans)


def _sanitize_workbook_xml(workbook_xml: bytes) -> bytes:
    """Elimina sólo AlternateContent con x15ac:absPath y conserva los demás bytes."""

    spans = _private_alternate_content_spans(workbook_xml)
    sanitized = workbook_xml
    for start, end in sorted(spans, reverse=True):
        sanitized = sanitized[:start] + sanitized[end:]

    if _private_alternate_content_spans(sanitized):
        raise ValueError("No se pudo eliminar x15ac:absPath de workbook.xml")
    lowered = sanitized.lower()
    if b"abspath" in lowered or LOCAL_USER_PATH_MARKER in lowered:
        raise ValueError("workbook.xml conserva una ruta local privada")
    return sanitized


def _sanitize_candidate_privacy(candidate: Path) -> None:
    """Sanea workbook.xml mediante un candidato OOXML auditado y reemplazo atómico."""

    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from mobiliti_saas.quote_engine.ooxml_package import (
        PackageMutation,
        XlsxPackage,
        assert_packages_preserved,
    )

    before = XlsxPackage.read(candidate)
    try:
        workbook_xml = before.parts[WORKBOOK_PART]
    except KeyError as error:
        raise ValueError("El candidato no contiene xl/workbook.xml") from error
    sanitized = _sanitize_workbook_xml(workbook_xml)
    if sanitized == workbook_xml:
        return

    sanitized_candidate = candidate.with_name(
        f".{candidate.name}.sanitizing-{uuid4().hex}.xlsx"
    )
    try:
        before.write_new(
            sanitized_candidate,
            PackageMutation(replacements={WORKBOOK_PART: sanitized}),
        )
        after = XlsxPackage.read(sanitized_candidate)
        audit = assert_packages_preserved(before, after, {WORKBOOK_PART})
        if audit.changed_parts != frozenset({WORKBOOK_PART}):
            raise RuntimeError("La sanitización no cambió únicamente workbook.xml")
        if _sanitize_workbook_xml(after.parts[WORKBOOK_PART]) != sanitized:
            raise RuntimeError("La sanitización de workbook.xml no es estable")
        os.replace(sanitized_candidate, candidate)
    except Exception:
        _preserve_failed_file(sanitized_candidate, "failed-sanitization")
        raise


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
        _sanitize_candidate_privacy(candidate)
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
