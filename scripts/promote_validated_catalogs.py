from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mobiliti_saas.quote_engine.supplier_catalog import load_supplier_catalog_data
from mobiliti_saas.worker.catalog_sync.repository import (
    CatalogRepository,
    CatalogRepositoryError,
)


SUPPLIERS = (
    "cr-global",
    "sonara",
    "sunon",
    "alma",
    "lumbro",
    "jome",
    "lauco",
    "idelika",
    "conceptos",
    "labenze",
    "requiez",
)
ASSET_NAME = re.compile(r"^[0-9a-f]{64}[.](?:png|jpg|jpeg|webp)$")
CONTENT_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}


def _timestamp(value: str) -> datetime:
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        raise ValueError("generated_at debe incluir zona horaria")
    return parsed


def _load_validated_catalogs(
    database_path: Path,
    assets_dir: Path,
    suppliers: tuple[str, ...],
) -> tuple[dict[str, dict], dict[str, Path]]:
    data = json.loads(database_path.read_text(encoding="utf-8"))
    snapshots = data.get("catalog_published_snapshots")
    if not isinstance(snapshots, dict):
        raise ValueError("La base local no contiene catalog_published_snapshots")

    payloads: dict[str, dict] = {}
    assets: dict[str, Path] = {}
    for supplier in suppliers:
        record = snapshots.get(supplier)
        if not isinstance(record, dict):
            raise ValueError(f"Falta el snapshot local de {supplier}")
        payload = record.get("payload", record)
        normalized = load_supplier_catalog_data(payload, expected_supplier=supplier)
        payloads[supplier] = payload

        for item in normalized["items"]:
            approved = (item.get("attributes") or {}).get("approved_asset") or {}
            object_name = approved.get("path")
            if not object_name:
                continue
            if approved.get("bucket") != "catalog-assets":
                raise ValueError(f"Bucket de activo invalido en {supplier}")
            if not isinstance(object_name, str) or ASSET_NAME.fullmatch(object_name) is None:
                raise ValueError(f"Nombre de activo invalido en {supplier}")
            local_path = assets_dir / object_name
            if not local_path.is_file():
                raise ValueError(f"Falta el activo {object_name}")
            digest = hashlib.sha256(local_path.read_bytes()).hexdigest()
            if digest != Path(object_name).stem:
                raise ValueError(f"Hash de activo invalido: {object_name}")
            assets[object_name] = local_path
    return payloads, assets


def _upload_asset(object_name: str, local_path: Path) -> str:
    content = local_path.read_bytes()
    base_url = os.environ["SUPABASE_URL"].rstrip("/")
    api_key = os.environ.get("SUPABASE_SERVICE_KEY") or os.environ["SUPABASE_ANON_KEY"]
    content_type = CONTENT_TYPES[local_path.suffix.lower()]
    encoded_name = quote(object_name, safe="")
    headers = {
        "Authorization": f"Bearer {api_key}",
        "apikey": api_key,
        "Content-Type": content_type,
        "x-upsert": "false",
    }
    if rest_secret := os.environ.get("MOBILITI_REST_SECRET"):
        headers["x-mobiliti-rest-secret"] = rest_secret
    request = Request(
        f"{base_url}/storage/v1/object/catalog-assets/{encoded_name}",
        data=content,
        headers=headers,
        method="POST",
    )
    try:
        with urlopen(request, timeout=30) as response:
            if response.status not in {200, 201}:
                raise RuntimeError(f"No se pudo subir {object_name}")
            response.read(64 * 1024)
    except HTTPError as error:
        if error.code != 409:
            raise RuntimeError(f"No se pudo subir {object_name}") from None
        verify = Request(
            (
                f"{base_url}/storage/v1/object/public/"
                f"catalog-assets/{encoded_name}"
            ),
            headers={
                "Authorization": f"Bearer {api_key}",
                "apikey": api_key,
            },
            method="GET",
        )
        try:
            with urlopen(verify, timeout=30) as response:
                remote = response.read(8 * 1024 * 1024 + 1)
        except HTTPError:
            raise RuntimeError(f"No se pudo verificar {object_name}") from None
        if hashlib.sha256(remote).hexdigest() != Path(object_name).stem:
            raise RuntimeError(f"Activo remoto incompatible: {object_name}")
    return object_name


def _upload_assets(assets: dict[str, Path], workers: int) -> None:
    completed = 0
    total = len(assets)
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(_upload_asset, object_name, local_path): object_name
            for object_name, local_path in assets.items()
        }
        for future in as_completed(futures):
            object_name = futures[future]
            result = future.result()
            if result != object_name:
                raise RuntimeError(f"Confirmacion de activo invalida: {object_name}")
            completed += 1
            if completed == total or completed % 25 == 0:
                print(f"activos {completed}/{total}", flush=True)


def _promote(
    payloads: dict[str, dict],
    reviewed_by: int,
    review_note: str,
) -> list[dict]:
    repository = CatalogRepository.from_environment()
    results = []
    for supplier, payload in payloads.items():
        source = repository.get_source(supplier)
        published = repository.get_published_snapshot(source)
        if published is not None and published.source_hash == payload["source_hash"]:
            results.append(
                {
                    "supplier": supplier,
                    "status": "already_published",
                    "items": len(payload["items"]),
                }
            )
            continue

        run_id = repository.start_run(source.id, "manual", reviewed_by)
        if run_id is None:
            raise RuntimeError(f"Ya existe una sincronizacion activa para {supplier}")
        candidate_id = repository.stage_candidate(
            run_id,
            {
                **payload,
                "generated_at": _timestamp(payload["generated_at"]),
            },
            {
                "bootstrap": True,
                "items": len(payload["items"]),
                "incident": "catalogs_unavailable_2026_07_27",
            },
            None,
        )
        repository.publish_candidate(
            candidate_id,
            reviewed_by,
            note=review_note,
        )

        verified_source = repository.get_source(supplier)
        verified = repository.get_published_snapshot(verified_source)
        if (
            verified is None
            or verified.id != candidate_id
            or verified.source_hash != payload["source_hash"]
            or len(verified.payload.get("items", [])) != len(payload["items"])
        ):
            raise RuntimeError(f"No se pudo verificar la publicacion de {supplier}")
        results.append(
            {
                "supplier": supplier,
                "status": "published",
                "items": len(payload["items"]),
                "source_hash": payload["source_hash"],
            }
        )
        print(f"publicado {supplier}: {len(payload['items'])} items", flush=True)
    return results


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Promueve snapshots locales validados al pipeline oficial de catalogos.",
    )
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--assets-dir", type=Path, required=True)
    parser.add_argument("--supplier", action="append", choices=SUPPLIERS)
    parser.add_argument("--reviewed-by", type=int)
    parser.add_argument("--review-note", default="Promocion de snapshot local validado")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    selected = tuple(args.supplier or SUPPLIERS)
    if not 1 <= args.workers <= 16:
        parser.error("--workers debe estar entre 1 y 16")
    if args.apply and (args.reviewed_by is None or args.reviewed_by < 1):
        parser.error("--reviewed-by es obligatorio con --apply")

    payloads, assets = _load_validated_catalogs(
        args.database.resolve(),
        args.assets_dir.resolve(),
        selected,
    )
    report = {
        "mode": "apply" if args.apply else "dry-run",
        "suppliers": {
            supplier: {
                "items": len(payload["items"]),
                "source_hash": payload["source_hash"],
            }
            for supplier, payload in payloads.items()
        },
        "unique_assets": len(assets),
        "asset_bytes": sum(path.stat().st_size for path in assets.values()),
    }
    print(json.dumps(report, ensure_ascii=False, sort_keys=True), flush=True)
    if not args.apply:
        return 0

    required = ("SUPABASE_URL", "SUPABASE_SERVICE_KEY")
    if any(not os.environ.get(name) for name in required):
        raise RuntimeError("Falta configuracion de Supabase")
    _upload_assets(assets, args.workers)
    results = _promote(payloads, args.reviewed_by, args.review_note)
    print(json.dumps({"results": results}, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (CatalogRepositoryError, ValueError, RuntimeError) as error:
        raise SystemExit(str(error)) from None
