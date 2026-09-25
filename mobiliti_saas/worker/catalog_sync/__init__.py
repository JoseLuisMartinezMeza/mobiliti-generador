import json
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath


_ADAPTERS = {
    "cr-global": "cr_global",
    "sonara": "sonara",
    "sunon": "sunon",
    "alma": "alma",
    "lumbro": "lumbro",
    "jome": "jome",
    "lauco": "lauco",
    "idelika": "idelika",
    "conceptos": "conceptos",
    "labenze": "labenze",
    "requiez": "requiez",
}
_MIME_TYPES = {
    ".pdf": "application/pdf",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".xlsb": "application/vnd.ms-excel.sheet.binary.macroEnabled.12",
}
_EXTENSIONS = set(_MIME_TYPES)
_KINDS = {"catalog", "inventory", "price_list", "spec_guide"}
_ROOT_PATH = "PROYECTOS CET - 2026/LISTAS DE PRECIOS PROVEEDORES"
_GRAPH_ITEM_ID_RE = re.compile(r"^[A-Za-z0-9]{34}$")
_MIME_REQUIRED_SUPPLIERS = {"idelika", "conceptos", "labenze", "requiez"}
_HASH_REQUIRED_SUPPLIERS = {"labenze", "requiez"}
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class SupplierFileConfig:
    path: str
    kind: str
    brand: str | None = None
    drive_item_id: str | None = None
    mime_type: str | None = None
    sha256: str | None = None

    @property
    def name(self) -> str:
        return PurePosixPath(self.path).name

    @property
    def extension(self) -> str:
        return PurePosixPath(self.path).suffix.lower()


@dataclass(frozen=True)
class SupplierSourceConfig:
    supplier: str
    label: str
    adapter: str
    root_path: str
    files: tuple[SupplierFileConfig, ...]


_FIRST_WAVE_ALLOWLIST = (
    SupplierSourceConfig(
        supplier="cr-global",
        label="CR Global",
        adapter="cr_global",
        root_path=_ROOT_PATH,
        files=(
            SupplierFileConfig("CR GLOBAL/CRG_FT_General_Dist_2026-04.pdf", "catalog"),
            SupplierFileConfig("CR GLOBAL/CRG_LP_General_Dist_2026-04.pdf", "price_list"),
            SupplierFileConfig("SPEC GUIDES 2026/CR Global/Spec guide-CR Global-2026.xlsx", "spec_guide"),
        ),
    ),
    SupplierSourceConfig(
        supplier="sonara",
        label="Sonara",
        adapter="sonara",
        root_path=_ROOT_PATH,
        files=(
            SupplierFileConfig("SONARA/Catalogo-Sonara.pdf", "catalog"),
            SupplierFileConfig("SONARA/Lista de precios Sonara 2026.pdf", "price_list"),
        ),
    ),
    SupplierSourceConfig(
        supplier="sunon",
        label="Sunon",
        adapter="sunon",
        root_path=_ROOT_PATH,
        files=(
            SupplierFileConfig("SPEC GUIDES 2026/SUNON MTY/Spec guide-Sunon MTY-2026.xlsx", "spec_guide"),
            SupplierFileConfig("SUNON MTY/2026 updated price-Chairs _ Mexico Stock Reserves \uff084-6 weeks).xlsx", "inventory"),
            SupplierFileConfig("SUNON MTY/2026 updated price-Fast inventory(1-2 Weeks) 02-09.xlsx", "inventory"),
            SupplierFileConfig("SUNON MTY/2026 updated price-Raw material preparation \u2605 Mexican inventory list \uff084-6 weeks).xlsx", "inventory"),
            SupplierFileConfig("SUNON MTY/INVENTORY MALL 1 \uff084-6weeks).xlsx", "inventory"),
        ),
    ),
    SupplierSourceConfig(
        supplier="alma",
        label="ALMA",
        adapter="alma",
        root_path=_ROOT_PATH,
        files=(
            SupplierFileConfig("SPEC Guide-Alma-KUN.xlsx", "spec_guide", "KUN"),
            SupplierFileConfig("SPEC GUIDES 2026/ALMA/Spec guide-Alma-KUN Design.xlsx", "spec_guide", "KUN"),
            SupplierFileConfig("SPEC Guide-Alma-Mondecasa.xlsx", "spec_guide", "Mondecasa"),
        ),
    ),
    SupplierSourceConfig(
        supplier="lumbro",
        label="Lumbro",
        adapter="lumbro",
        root_path=_ROOT_PATH,
        files=(
            SupplierFileConfig(
                "LUMBRO/LP/LISTA DE PRECIOS MULTICONTACTOS 2026.pdf",
                "price_list",
                drive_item_id="01DHXXN73PQIV3NEC74BFIAXGF7HN3S3NE",
            ),
            SupplierFileConfig(
                "LUMBRO/LP/LISTA DE PRECIOS NUEVOS PRODUCTOS LUMBRO 2025.pdf",
                "price_list",
                drive_item_id="01DHXXN72MMCJPX2ENKRCLIVOLPBNYLFX7",
            ),
            SupplierFileConfig(
                "LUMBRO/LP/Precios Interconexión Sunón act.xlsx",
                "price_list",
                drive_item_id="01DHXXN7Y4QLJBB6BVO5CLJR5WQHD6ETGY",
            ),
            SupplierFileConfig(
                "SPEC GUIDES 2026/LUMBRO/Spec guide-Lumbro-2026.xlsx",
                "spec_guide",
                drive_item_id="01DHXXN726RRTWDBVGDZH3DHSR4XUGGYNG",
            ),
            SupplierFileConfig(
                "LUMBRO/CATALOGO/CATALOGO LUMBRO 2024 DIGITAL (1).pdf",
                "catalog",
                drive_item_id="01DHXXN7YFOCIP7S2WR5F3AFZF3Z5ITB3J",
            ),
        ),
    ),
    SupplierSourceConfig(
        supplier="jome",
        label="JOME",
        adapter="jome",
        root_path=_ROOT_PATH,
        files=(
            SupplierFileConfig(
                "SPEC GUIDES 2026/JOME/Spec guide-Estructuras Jome-2026.xlsx",
                "spec_guide",
                "estructuras",
                drive_item_id="01DHXXN73FNX632SXL3JBZ5O6FNNULR67U",
            ),
            SupplierFileConfig(
                "SPEC GUIDES 2026/JOME/Spec guide-Laminado-2026.xlsx",
                "spec_guide",
                "laminado",
                drive_item_id="01DHXXN72IXFY22JUPD5GJT5B6PPGWE7ZX",
            ),
        ),
    ),
    SupplierSourceConfig(
        supplier="lauco",
        label="Lauco",
        adapter="lauco",
        root_path=_ROOT_PATH,
        files=(
            SupplierFileConfig(
                "SPEC GUIDES 2026/LAUCO/Spec Guide Lauco-2026.xlsb",
                "spec_guide",
                "Lauco",
                drive_item_id="01DHXXN73QZOUEEWNH4BE2NO5YPBUJ5HNK",
            ),
        ),
    ),
    SupplierSourceConfig(
        supplier="idelika",
        label="IDÉLIKA",
        adapter="idelika",
        root_path=_ROOT_PATH,
        files=(
            SupplierFileConfig(
                "IDELIKA/1 CATALOGO FABRICACION 2026B.pdf",
                "catalog",
                drive_item_id="01DHXXN7YJMCJUVPBWNJEJPJIH7B4OTAUR",
                mime_type="application/pdf",
            ),
            SupplierFileConfig(
                "IDELIKA/2 CATALOGO STOCK 2026.pdf",
                "inventory",
                drive_item_id="01DHXXN7YASXKBZPOLSBHIX2N2T3PB4G2R",
                mime_type="application/pdf",
            ),
            SupplierFileConfig(
                "IDELIKA/4 SCHOOL SERIES 2026.pdf",
                "catalog",
                drive_item_id="01DHXXN7YTQLPUZXRUN5E3J62UE2JQUWNC",
                mime_type="application/pdf",
            ),
        ),
    ),
    SupplierSourceConfig(
        supplier="conceptos",
        label="Conceptos",
        adapter="conceptos",
        root_path=_ROOT_PATH,
        files=(
            SupplierFileConfig(
                "SPEC GUIDES 2026/CONCEPTOS/Spec guide - Conceptos - Sofas - CdMx - Gdl - Qro - 2021.xlsx",
                "spec_guide",
                drive_item_id="01DHXXN76XWGQOWSKX2RDL5YG6GTS355BO",
                mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            ),
        ),
    ),
    SupplierSourceConfig(
        supplier="labenze",
        label="Labenze",
        adapter="labenze",
        root_path=_ROOT_PATH,
        files=(
            SupplierFileConfig(
                "LABENZE/LP Labenze B26.pdf",
                "price_list",
                drive_item_id="01DHXXN77SAPUFK56QHVBLKXH7BBV7DOL7",
                mime_type="application/pdf",
                sha256="c4fc2d2152b5e854f7c36c9106c71cd21853abb50efcde96ba2566cb72f1d6f3",
            ),
        ),
    ),
    SupplierSourceConfig(
        supplier="requiez",
        label="Requiez",
        adapter="requiez",
        root_path=_ROOT_PATH,
        files=(
            SupplierFileConfig(
                "REQUIEZ/Lista de precios A-26.pdf",
                "price_list",
                drive_item_id="01DHXXN74NDZ6P4EL3B5CI2G2HFZ47ISNT",
                mime_type="application/pdf",
                sha256="7f3281d1965c67a234bac55112800067019ad471f835de59ff758e759eca56ba",
            ),
        ),
    ),
)


def _string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"Invalid {field}")
    return value


def _file_config(
    raw: object,
    *,
    require_mime_type: bool = False,
    require_sha256: bool = False,
) -> SupplierFileConfig:
    if (
        not isinstance(raw, dict)
        or set(raw) - {"path", "kind", "brand", "drive_item_id", "mime_type", "sha256"}
        or {"path", "kind"} - set(raw)
    ):
        raise ValueError("Invalid source file")
    path = _string(raw["path"], "source path")
    windows_path = PureWindowsPath(path)
    if (
        PurePosixPath(path).is_absolute()
        or windows_path.drive
        or windows_path.root
        or any(part in {"", ".", ".."} for part in path.replace("\\", "/").split("/"))
    ):
        raise ValueError("Source path must be relative")
    kind = _string(raw["kind"], "source kind")
    if kind not in _KINDS:
        raise ValueError("Unknown source kind")
    brand = raw.get("brand")
    if brand is not None:
        brand = _string(brand, "source brand")
    drive_item_id = raw.get("drive_item_id")
    if drive_item_id is not None and (
        not isinstance(drive_item_id, str) or not _GRAPH_ITEM_ID_RE.fullmatch(drive_item_id)
    ):
        raise ValueError("Invalid Graph item ID")
    mime_type = raw.get("mime_type")
    if mime_type is not None:
        mime_type = _string(mime_type, "source MIME type")
    sha256 = raw.get("sha256")
    if sha256 is not None and (
        not isinstance(sha256, str) or _SHA256_RE.fullmatch(sha256) is None
    ):
        raise ValueError("Invalid source SHA-256")
    file = SupplierFileConfig(
        path=path,
        kind=kind,
        brand=brand,
        drive_item_id=drive_item_id,
        mime_type=mime_type,
        sha256=sha256,
    )
    if file.extension not in _EXTENSIONS:
        raise ValueError("Unsupported source extension")
    expected_mime_type = _MIME_TYPES[file.extension]
    if (require_mime_type and file.mime_type is None) or (
        file.mime_type is not None and file.mime_type != expected_mime_type
    ):
        raise ValueError("Invalid source MIME type")
    if require_sha256 and file.sha256 is None:
        raise ValueError("Source SHA-256 is required")
    return file


def load_source_config(path: Path) -> tuple[SupplierSourceConfig, ...]:
    try:
        raw_rows = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("Invalid source config") from error
    if not isinstance(raw_rows, list):
        raise ValueError("Invalid source config")

    suppliers: set[str] = set()
    files: set[str] = set()
    rows = []
    for raw in raw_rows:
        if not isinstance(raw, dict) or set(raw) != {"supplier", "label", "adapter", "root_path", "files"}:
            raise ValueError("Invalid supplier source")
        supplier = _string(raw["supplier"], "supplier")
        if supplier in suppliers or supplier not in _ADAPTERS:
            raise ValueError("Unknown or duplicate supplier")
        adapter = _string(raw["adapter"], "adapter")
        if adapter != _ADAPTERS[supplier]:
            raise ValueError("Unknown adapter")
        raw_files = raw["files"]
        if not isinstance(raw_files, list) or not raw_files:
            raise ValueError("Supplier files are required")
        source_files = tuple(
            _file_config(
                file,
                require_mime_type=supplier in _MIME_REQUIRED_SUPPLIERS,
                require_sha256=supplier in _HASH_REQUIRED_SUPPLIERS,
            )
            for file in raw_files
        )
        if len({file.path for file in source_files}) != len(source_files) or any(
            file.path in files for file in source_files
        ):
            raise ValueError("Duplicate source file")
        suppliers.add(supplier)
        files.update(file.path for file in source_files)
        rows.append(
            SupplierSourceConfig(
                supplier=supplier,
                label=_string(raw["label"], "supplier label"),
                adapter=adapter,
                root_path=_string(raw["root_path"], "root path"),
                files=source_files,
            )
        )
    config = tuple(rows)
    if config != _FIRST_WAVE_ALLOWLIST:
        raise ValueError("Source config does not match the first-wave allowlist")
    return config
