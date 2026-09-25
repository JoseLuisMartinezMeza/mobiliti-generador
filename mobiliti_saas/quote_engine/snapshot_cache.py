"""Caché privada, validada y acotada para snapshots de catálogos."""

from __future__ import annotations

from collections import OrderedDict
from copy import deepcopy
import gzip
import hashlib
from io import BytesIO
import json
import logging
import threading
import zlib


LOGGER = logging.getLogger(__name__)


class SnapshotCache:
    """Mantiene snapshots validados en memoria y, opcionalmente, en R2 privado."""

    MAX_ENTRIES = 32
    MAX_UNCOMPRESSED_BYTES = 32 * 1024 * 1024
    MAX_COMPRESSED_BYTES = 8 * 1024 * 1024
    PREFIX = "internal/catalog-snapshots/v1/"

    def __init__(self):
        self._memory: OrderedDict[str, bytes] = OrderedDict()
        self._lock = threading.RLock()
        self.counters = {
            "memory_hit": 0,
            "r2_hit": 0,
            "db_load": 0,
            "cache_error": 0,
            "loader_content_bytes": 0,
        }

    @staticmethod
    def _identity(namespace: str, supplier: str, revision: str) -> tuple[str, str]:
        values = (namespace, supplier, revision)
        if not all(isinstance(value, str) and value for value in values):
            raise ValueError("Identidad de snapshot invalida")
        digest = hashlib.sha256("\x00".join(values).encode("utf-8")).hexdigest()
        return digest, f"{SnapshotCache.PREFIX}{digest}.json.gz"

    @staticmethod
    def _gzip(content: bytes) -> bytes:
        output = BytesIO()
        with gzip.GzipFile(fileobj=output, mode="wb", filename="", mtime=0) as writer:
            writer.write(content)
        return output.getvalue()

    @staticmethod
    def _json_bytes(value: dict) -> bytes:
        return json.dumps(
            value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")

    def _record_error(self, reason: str) -> None:
        self.counters["cache_error"] += 1
        LOGGER.warning("Snapshot cache privado omitido: %s", reason[:120])

    @staticmethod
    def _error_status(error: Exception) -> tuple[int | None, str]:
        response = getattr(error, "response", None)
        if not isinstance(response, dict):
            return None, ""
        metadata = response.get("ResponseMetadata")
        details = response.get("Error")
        status = metadata.get("HTTPStatusCode") if isinstance(metadata, dict) else None
        code = details.get("Code") if isinstance(details, dict) else ""
        return status, str(code or "")

    def _read_body(self, body) -> bytes:
        chunks: list[bytes] = []
        remaining = self.MAX_COMPRESSED_BYTES + 1
        try:
            while remaining:
                block = body.read(min(64 * 1024, remaining))
                if not block:
                    break
                if not isinstance(block, bytes):
                    raise ValueError("cuerpo R2 invalido")
                chunks.append(block)
                remaining -= len(block)
        finally:
            close = getattr(body, "close", None)
            if callable(close):
                close()
        content = b"".join(chunks)
        if len(content) > self.MAX_COMPRESSED_BYTES:
            raise ValueError("objeto comprimido excede limite")
        return content

    def _decode(self, content: bytes, *, namespace: str, supplier: str, revision: str) -> dict | None:
        decompressor = zlib.decompressobj(16 + zlib.MAX_WBITS)
        raw = decompressor.decompress(content, self.MAX_UNCOMPRESSED_BYTES + 1)
        if (
            len(raw) > self.MAX_UNCOMPRESSED_BYTES
            or decompressor.unconsumed_tail
            or not decompressor.eof
            or decompressor.unused_data
        ):
            raise ValueError("objeto gzip invalido o excede limite")
        envelope = json.loads(raw.decode("utf-8"))
        if (
            not isinstance(envelope, dict)
            or envelope.get("namespace") != namespace
            or envelope.get("supplier") != supplier
            or envelope.get("revision") != revision
            or not isinstance(envelope.get("payload"), dict)
        ):
            return None
        return envelope["payload"]

    def _memory_get(self, identity: str, *, namespace: str, supplier: str, revision: str, validator) -> dict | None:
        content = self._memory.get(identity)
        if content is None:
            return None
        try:
            payload = self._decode(content, namespace=namespace, supplier=supplier, revision=revision)
            if payload is None or not validator(payload):
                self._memory.pop(identity, None)
                return None
        except Exception:
            self._memory.pop(identity, None)
            self._record_error("entrada de memoria invalida")
            return None
        self._memory.move_to_end(identity)
        self.counters["memory_hit"] += 1
        return deepcopy(payload)

    def _memory_put(self, identity: str, content: bytes) -> None:
        self._memory[identity] = content
        self._memory.move_to_end(identity)
        while len(self._memory) > self.MAX_ENTRIES:
            self._memory.popitem(last=False)

    def _r2_get(self, client_factory, bucket: str, key: str, *, namespace: str, supplier: str, revision: str, validator) -> dict | None:
        try:
            response = client_factory().get_object(Bucket=bucket, Key=key)
        except Exception as exc:
            status, code = self._error_status(exc)
            if status == 404 or code in {"404", "NoSuchKey", "NotFound"}:
                return None
            self._record_error("lectura R2 no disponible")
            return None
        try:
            if not isinstance(response, dict):
                raise ValueError("respuesta R2 invalida")
            if (
                response.get("ContentType") != "application/json"
                or response.get("ContentEncoding") != "gzip"
                or response.get("CacheControl") != "private,no-store"
                or not isinstance(response.get("Metadata"), dict)
            ):
                raise ValueError("metadatos R2 invalidos")
            content = self._read_body(response.get("Body"))
            expected = response["Metadata"].get("sha256")
            if expected != hashlib.sha256(content).hexdigest():
                raise ValueError("integridad R2 invalida")
            payload = self._decode(content, namespace=namespace, supplier=supplier, revision=revision)
            if payload is None or not validator(payload):
                raise ValueError("identidad R2 invalida")
        except Exception:
            self._record_error("objeto R2 invalido")
            return None
        self.counters["r2_hit"] += 1
        return payload

    def _r2_put(self, client_factory, bucket: str, key: str, content: bytes) -> None:
        try:
            client_factory().put_object(
                Bucket=bucket,
                Key=key,
                Body=content,
                IfNoneMatch="*",
                ContentType="application/json",
                ContentEncoding="gzip",
                CacheControl="private,no-store",
                Metadata={"sha256": hashlib.sha256(content).hexdigest()},
            )
        except Exception as exc:
            status, code = self._error_status(exc)
            if status == 412 or code in {"412", "PreconditionFailed"}:
                return
            self._record_error("escritura R2 no disponible")

    def load(
        self,
        *,
        namespace: str,
        supplier: str,
        revision: str,
        loader,
        validator,
        client_factory=None,
        bucket: str = "",
    ) -> dict | None:
        """Devuelve un snapshot validado sin ocultar errores del ``loader``."""
        identity, key = self._identity(namespace, supplier, revision)
        with self._lock:
            cached = self._memory_get(
                identity, namespace=namespace, supplier=supplier, revision=revision, validator=validator
            )
            if cached is not None:
                return cached
            if client_factory is not None and bucket:
                cached = self._r2_get(
                    client_factory, bucket, key,
                    namespace=namespace, supplier=supplier, revision=revision, validator=validator,
                )
                if cached is not None:
                    content = self._gzip(self._json_bytes({
                        "namespace": namespace, "supplier": supplier,
                        "revision": revision, "payload": cached,
                    }))
                    self._memory_put(identity, content)
                    return deepcopy(cached)

            row = loader()
            self.counters["db_load"] += 1
            if not isinstance(row, dict) or not validator(row):
                return None
            envelope = {
                "namespace": namespace, "supplier": supplier,
                "revision": revision, "payload": row,
            }
            raw = self._json_bytes(envelope)
            self.counters["loader_content_bytes"] += len(raw)
            if len(raw) > self.MAX_UNCOMPRESSED_BYTES:
                self._record_error("snapshot excede limite sin comprimir")
                return deepcopy(row)
            content = self._gzip(raw)
            if len(content) > self.MAX_COMPRESSED_BYTES:
                self._record_error("snapshot excede limite comprimido")
                return deepcopy(row)
            self._memory_put(identity, content)
            if client_factory is not None and bucket:
                self._r2_put(client_factory, bucket, key, content)
            return deepcopy(row)
