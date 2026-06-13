from __future__ import annotations

from pathlib import Path
import unicodedata
import os
import tempfile
import zipfile
import xml.etree.ElementTree as ET

from openpyxl.drawing.spreadsheet_drawing import AnchorMarker, OneCellAnchor
from openpyxl.drawing.image import Image as XlsxImage
from openpyxl.drawing.xdr import XDRPositiveSize2D
from openpyxl.utils.units import pixels_to_EMU


NS = {
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "xdr": "http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "rels": "http://schemas.openxmlformats.org/package/2006/relationships",
}


def extract_images(source_path: str | Path) -> tuple[dict[int, str], str]:
    temp_dir = tempfile.mkdtemp(prefix="mobiliti_images_")
    image_map: dict[int, str] = {}

    with zipfile.ZipFile(source_path, "r") as zf:
        for name in zf.namelist():
            if name.startswith("xl/media/"):
                dest = os.path.join(temp_dir, os.path.basename(name))
                with open(dest, "wb") as fh:
                    fh.write(zf.read(name))

        rels_name = "xl/worksheets/_rels/sheet1.xml.rels"
        if rels_name not in zf.namelist():
            return image_map, temp_dir
        rels_root = ET.fromstring(zf.read(rels_name).decode("utf-8"))
        drawing_path = None
        for rel in rels_root.findall(".//rels:Relationship", NS):
            target = rel.get("Target") or ""
            if "drawing" in target.lower() and "vml" not in target.lower():
                drawing_path = target.replace("../", "xl/")
                break
        if not drawing_path or drawing_path not in zf.namelist():
            return image_map, temp_dir

        drawing_rels = f"xl/drawings/_rels/{os.path.basename(drawing_path)}.rels"
        rel_to_file: dict[str, str] = {}
        if drawing_rels in zf.namelist():
            drawing_rels_root = ET.fromstring(zf.read(drawing_rels).decode("utf-8"))
            for rel in drawing_rels_root.findall(".//rels:Relationship", NS):
                if rel.get("TargetMode") == "External":
                    continue
                rel_to_file[rel.get("Id") or ""] = os.path.basename(rel.get("Target") or "")

        drawing_root = ET.fromstring(zf.read(drawing_path).decode("utf-8"))
        for anchor in drawing_root.findall(".//xdr:twoCellAnchor", NS):
            blip = anchor.find(".//a:blip", NS)
            row_node = anchor.find("xdr:from/xdr:row", NS)
            if blip is None or row_node is None:
                continue
            rid = blip.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed")
            filename = rel_to_file.get(rid or "")
            if not filename:
                continue
            path = os.path.join(temp_dir, filename)
            if os.path.exists(path):
                image_map[int(row_node.text or "0") + 1] = path

    return image_map, temp_dir


def image_scale_for_category(category: str | None) -> float:
    text = _normalize_category(category)
    if text in {"mesas de juntas", "escritorios workstation"}:
        return 0.9
    if text in {"silla", "sillas", "sofa", "sofas", "sillon", "sillones", "mesas de apoyo", "banco", "bancos"}:
        return 0.8
    return 0.7


def fit_image_to_cell(path: str, max_width: float, max_height: float, scale: float = 1.0) -> XlsxImage:
    img = XlsxImage(path)
    if not img.width or not img.height:
        return img
    fit_scale = min((max_width * scale) / img.width, (max_height * scale) / img.height)
    img.width = int(img.width * fit_scale)
    img.height = int(img.height * fit_scale)
    return img


def center_image_in_cell(
    img: XlsxImage,
    *,
    row: int,
    column: int,
    cell_width: float,
    cell_height: float,
) -> XlsxImage:
    col_offset = max(0, (cell_width - img.width) / 2)
    row_offset = max(0, (cell_height - img.height) / 2)
    img.anchor = OneCellAnchor(
        _from=AnchorMarker(
            col=column - 1,
            row=row - 1,
            colOff=pixels_to_EMU(col_offset),
            rowOff=pixels_to_EMU(row_offset),
        ),
        ext=XDRPositiveSize2D(pixels_to_EMU(img.width), pixels_to_EMU(img.height)),
    )
    return img


def _normalize_category(category: str | None) -> str:
    text = str(category or "").strip().lower()
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return " ".join(text.replace("-", " ").split())
