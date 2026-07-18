import hashlib
import ipaddress
import json
import os
import re
import secrets
import socket
import stat
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from types import MappingProxyType
from typing import Mapping
from urllib.error import HTTPError
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


_GRAPH_HOST = "graph.microsoft.com"
_GRAPH_ROOT = "https://graph.microsoft.com/v1.0"
_SCOPE = ["https://graph.microsoft.com/.default"]
_ENV_NAMES = (
    "MS_GRAPH_TENANT_ID",
    "MS_GRAPH_CLIENT_ID",
    "MS_GRAPH_CERT_PATH",
    "MS_GRAPH_CERT_THUMBPRINT",
    "SHAREPOINT_HOSTNAME",
    "SHAREPOINT_SITE_PATH",
    "SHAREPOINT_DRIVE_NAME",
    "SHAREPOINT_CATALOG_ROOT",
)
_MAX_CERT_BYTES = 1024 * 1024
_MAX_JSON_BYTES = 4 * 1024 * 1024
_MAX_PAGES = 100
_MAX_ITEMS = 10_000
_MAX_DOWNLOAD_BYTES = 64 * 1024 * 1024
_TIMEOUT = 15
_ID_RE = re.compile(r"^[^\x00-\x1f\x7f]{1,512}$")
_MIME_RE = re.compile(r"^[a-z0-9!#$&^_.+-]+/[a-z0-9!#$&^_.+-]+$", re.I)


class GraphError(ValueError):
    pass


class DeltaExpiredError(GraphError):
    pass


@dataclass(frozen=True)
class GraphItem:
    id: str
    name: str
    path: str | None
    size: int | None
    e_tag: str | None
    c_tag: str | None
    mime_type: str | None
    is_folder: bool
    deleted: Mapping[str, object] | None


@dataclass(frozen=True)
class DeltaResult:
    items: tuple[GraphItem, ...]
    delta_link: str


@dataclass(frozen=True)
class DownloadedFile:
    path: Path
    size: int
    sha256: str


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _default_opener():
    return build_opener(ProxyHandler({}), _NoRedirect())


def _invalid_response():
    raise GraphError("Invalid Graph response")


def _request_failed():
    raise GraphError("Graph request failed")


def _download_failed():
    raise GraphError("Download failed")


def _read_certificate(path):
    descriptor = None
    failed = False
    certificate = None
    try:
        before = os.lstat(path)
        if stat.S_ISLNK(before.st_mode):
            raise OSError
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino)
            or not 0 < opened.st_size <= _MAX_CERT_BYTES
        ):
            raise OSError
        chunks = []
        remaining = _MAX_CERT_BYTES + 1
        while remaining:
            chunk = os.read(descriptor, min(64 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        if not raw or len(raw) > _MAX_CERT_BYTES:
            raise OSError
        certificate = raw.decode("utf-8")
    except Exception:
        failed = True
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except Exception:
                failed = True
    if failed:
        raise GraphError("Invalid Graph configuration")
    return certificate


def _bounded_string(value, *, maximum=512, allow_empty=False):
    if not isinstance(value, str) or len(value) > maximum or (not allow_empty and not value):
        return None
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        return None
    return value


def _identifier(value):
    return value if isinstance(value, str) and _ID_RE.fullmatch(value) else None


def _relative_path(value, *, maximum=2048, leading_slash=False):
    value = _bounded_string(value, maximum=maximum)
    if value is None or "\\" in value or "?" in value or "#" in value:
        return None
    path = value[1:] if leading_slash and value.startswith("/") else value
    windows = PureWindowsPath(path)
    if not path or path.startswith("/") or windows.drive or windows.root:
        return None
    if any(part in {"", ".", ".."} for part in path.split("/")):
        return None
    return "/" + path if leading_slash else path


def _graph_url(url):
    invalid = False
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except Exception:
        invalid = True
        parsed = None
        port = None
    if invalid:
        _invalid_response()
    if (
        parsed.scheme != "https"
        or parsed.hostname != _GRAPH_HOST
        or port not in (None, 443)
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        _invalid_response()
    return url


def _read_limited(response, limit):
    data = response.read(limit + 1)
    if not isinstance(data, bytes) or len(data) > limit:
        _invalid_response()
    return data


def _json_object(response):
    status = getattr(response, "status", None)
    if type(status) is not int or status != 200:
        _invalid_response()
    content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
    if content_type and content_type not in {"application/json", "text/json"} and not content_type.endswith("+json"):
        _invalid_response()
    try:
        value = json.loads(_read_limited(response, _MAX_JSON_BYTES))
    except (UnicodeDecodeError, json.JSONDecodeError):
        value = None
    if not isinstance(value, dict):
        _invalid_response()
    return value


def _item(raw):
    if not isinstance(raw, dict):
        _invalid_response()
    item_id = _identifier(raw.get("id"))
    deleted = raw.get("deleted")
    if deleted is not None:
        if not isinstance(deleted, dict) or len(deleted) > 8 or any(
            _bounded_string(key, maximum=64) is None or not isinstance(value, (str, bool, type(None)))
            for key, value in deleted.items()
        ):
            _invalid_response()
        deleted = MappingProxyType(dict(deleted))
    name = raw.get("name", "" if deleted is not None else None)
    if item_id is None or _bounded_string(name, maximum=512, allow_empty=deleted is not None) is None:
        _invalid_response()

    size = raw.get("size")
    if size is not None and (type(size) is not int or not 0 <= size <= _MAX_DOWNLOAD_BYTES):
        _invalid_response()
    e_tag = raw.get("eTag")
    c_tag = raw.get("cTag")
    if any(value is not None and _bounded_string(value, maximum=1024) is None for value in (e_tag, c_tag)):
        _invalid_response()

    file_facet = raw.get("file")
    folder_facet = raw.get("folder")
    if file_facet is not None and folder_facet is not None:
        _invalid_response()
    if file_facet is not None and not isinstance(file_facet, dict):
        _invalid_response()
    if folder_facet is not None and not isinstance(folder_facet, dict):
        _invalid_response()
    mime_type = file_facet.get("mimeType") if file_facet is not None else None
    if mime_type is not None and (
        _bounded_string(mime_type, maximum=255) is None or _MIME_RE.fullmatch(mime_type) is None
    ):
        _invalid_response()

    parent = raw.get("parentReference")
    path = None
    if parent is not None:
        if not isinstance(parent, dict):
            _invalid_response()
        path = parent.get("path")
        if path is not None and _bounded_string(path, maximum=2048) is None:
            _invalid_response()
    return GraphItem(item_id, name, path, size, e_tag, c_tag, mime_type, folder_facet is not None, deleted)


def _public_addresses(host, resolver):
    try:
        parsed = ipaddress.ip_address(host)
        addresses = [parsed]
    except ValueError:
        try:
            answers = resolver(host, 443, type=socket.SOCK_STREAM)
            addresses = [ipaddress.ip_address(answer[4][0]) for answer in answers]
        except Exception:
            addresses = []
    if not addresses or any(_unsafe_address(address) for address in addresses):
        raise GraphError("Invalid download redirect")


def _unsafe_address(address):
    return any(
        (
            address.is_private,
            address.is_loopback,
            address.is_link_local,
            address.is_multicast,
            address.is_unspecified,
            address.is_reserved,
            not address.is_global,
        )
    )


def _download_url(url, tenant_hostname, resolver):
    invalid = False
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except Exception:
        invalid = True
        parsed = None
        port = None
    if invalid:
        raise GraphError("Invalid download redirect")
    host = (parsed.hostname or "").lower()
    allowed = host == tenant_hostname or host.endswith(".sharepoint.com") or host.endswith(".sharepointonline.com")
    if (
        parsed.scheme != "https"
        or not allowed
        or port not in (None, 443)
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise GraphError("Invalid download redirect")
    _public_addresses(host, resolver)
    return url


def _peer_ip(response):
    direct = getattr(response, "peer_ip", None)
    if direct:
        return direct
    try:
        return response.fp.raw._sock.getpeername()[0]
    except (AttributeError, OSError, TypeError, IndexError):
        return None


class GraphCatalogClient:
    def __init__(
        self,
        *,
        app,
        tenant_hostname,
        site_path,
        drive_name,
        catalog_root,
        opener=None,
        resolver=None,
        timeout=_TIMEOUT,
    ):
        if type(timeout) not in {int, float} or not 0 < timeout <= 30:
            raise GraphError("Invalid Graph configuration")
        self._app = app
        self.tenant_hostname = tenant_hostname.lower()
        self.site_path = site_path
        self.drive_name = drive_name
        self.catalog_root = catalog_root
        self._opener = opener or _default_opener()
        self._resolver = resolver or socket.getaddrinfo
        self._timeout = timeout

    @classmethod
    def from_environment(cls):
        values = {name: os.environ.get(name) for name in _ENV_NAMES}
        if any(_bounded_string(value, maximum=2048) is None or value.strip() != value for value in values.values()):
            raise GraphError("Invalid Graph configuration")
        hostname = values["SHAREPOINT_HOSTNAME"].lower()
        if (
            _bounded_string(values["MS_GRAPH_TENANT_ID"], maximum=256) is None
            or re.fullmatch(r"[A-Za-z0-9.-]+", values["MS_GRAPH_TENANT_ID"]) is None
            or _bounded_string(values["MS_GRAPH_CLIENT_ID"], maximum=128) is None
            or re.fullmatch(r"[A-Za-z0-9-]+", values["MS_GRAPH_CLIENT_ID"]) is None
            or len(hostname) > 253
            or hostname != values["SHAREPOINT_HOSTNAME"]
            or not hostname.endswith((".sharepoint.com", ".sharepointonline.com"))
            or urlsplit("https://" + hostname).hostname != hostname
            or _relative_path(values["SHAREPOINT_SITE_PATH"], leading_slash=True) is None
            or _relative_path(values["SHAREPOINT_CATALOG_ROOT"]) is None
            or _bounded_string(values["SHAREPOINT_DRIVE_NAME"], maximum=256) is None
            or re.fullmatch(r"[A-Fa-f0-9]{1,128}", values["MS_GRAPH_CERT_THUMBPRINT"]) is None
        ):
            raise GraphError("Invalid Graph configuration")
        cert_path = Path(values["MS_GRAPH_CERT_PATH"])
        if not cert_path.is_absolute():
            raise GraphError("Invalid Graph configuration")
        certificate = _read_certificate(cert_path)
        auth_failed = False
        try:
            import msal

            app = msal.ConfidentialClientApplication(
                values["MS_GRAPH_CLIENT_ID"],
                authority=f"https://login.microsoftonline.com/{values['MS_GRAPH_TENANT_ID']}",
                client_credential={
                    "private_key": certificate,
                    "thumbprint": values["MS_GRAPH_CERT_THUMBPRINT"],
                },
            )
        except Exception:
            auth_failed = True
            app = None
        if auth_failed:
            raise GraphError("Graph authentication unavailable")
        return cls(
            app=app,
            tenant_hostname=hostname,
            site_path=values["SHAREPOINT_SITE_PATH"],
            drive_name=values["SHAREPOINT_DRIVE_NAME"],
            catalog_root=values["SHAREPOINT_CATALOG_ROOT"],
        )

    def _token(self):
        failed = False
        try:
            result = self._app.acquire_token_for_client(scopes=_SCOPE.copy())
        except Exception:
            failed = True
            result = None
        token = result.get("access_token") if isinstance(result, dict) else None
        if failed or _bounded_string(token, maximum=8192) is None:
            raise GraphError("Graph authentication failed")
        return token

    def _open_graph(self, url, *, delta=False):
        _graph_url(url)
        request = Request(url, headers={"Authorization": f"Bearer {self._token()}", "Accept": "application/json"})
        failed = False
        expired = False
        try:
            response = self._opener.open(request, timeout=self._timeout)
        except HTTPError as error:
            expired = delta and error.code == 410
            failed = not expired
            response = None
        except Exception:
            failed = True
            response = None
        if expired:
            raise DeltaExpiredError("Graph delta expired")
        if failed:
            _request_failed()
        return response

    def _metadata(self, url, *, delta=False):
        response = self._open_graph(url, delta=delta)
        failed = False
        try:
            with response:
                return _json_object(response)
        except GraphError:
            raise
        except Exception:
            failed = True
        if failed:
            _request_failed()

    def resolve_site(self, hostname: str, site_path: str) -> str:
        if hostname.lower() != self.tenant_hostname or hostname != hostname.lower():
            raise GraphError("Invalid site")
        path = _relative_path(site_path, leading_slash=True)
        if path is None:
            raise GraphError("Invalid site")
        raw = self._metadata(f"{_GRAPH_ROOT}/sites/{quote(hostname, safe='')}:{quote(path, safe='/')}")
        site_id = _identifier(raw.get("id"))
        if site_id is None:
            _invalid_response()
        return site_id

    def resolve_drive(self, site_id: str, drive_name: str) -> str:
        if _identifier(site_id) is None or _bounded_string(drive_name, maximum=256) is None:
            raise GraphError("Invalid drive")
        raw = self._metadata(f"{_GRAPH_ROOT}/sites/{quote(site_id, safe='')}/drives")
        rows = raw.get("value")
        if not isinstance(rows, list) or len(rows) > 1000:
            _invalid_response()
        matches = [row.get("id") for row in rows if isinstance(row, dict) and row.get("name") == drive_name]
        if len(matches) != 1 or _identifier(matches[0]) is None:
            _invalid_response()
        return matches[0]

    def resolve_item(self, drive_id: str, relative_path: str) -> GraphItem:
        path = _relative_path(relative_path)
        if _identifier(drive_id) is None or path is None:
            raise GraphError("Invalid item")
        raw = self._metadata(f"{_GRAPH_ROOT}/drives/{quote(drive_id, safe='')}/root:/{quote(path, safe='/')}")
        return _item(raw)

    def iter_delta(self, drive_id: str, folder_id: str, delta_link: str | None = None) -> DeltaResult:
        if _identifier(drive_id) is None or _identifier(folder_id) is None:
            raise GraphError("Invalid delta request")
        url = delta_link or f"{_GRAPH_ROOT}/drives/{quote(drive_id, safe='')}/items/{quote(folder_id, safe='')}/delta"
        seen_links = set()
        by_id = {}
        for _ in range(_MAX_PAGES):
            _graph_url(url)
            if url in seen_links:
                _invalid_response()
            seen_links.add(url)
            raw = self._metadata(url, delta=True)
            rows = raw.get("value")
            if not isinstance(rows, list) or len(rows) + len(by_id) > _MAX_ITEMS:
                _invalid_response()
            for raw_item in rows:
                item = _item(raw_item)
                previous = by_id.get(item.id)
                if previous is not None and previous != item:
                    _invalid_response()
                by_id[item.id] = item
            next_link = raw.get("@odata.nextLink")
            delta = raw.get("@odata.deltaLink")
            if next_link is not None and delta is not None:
                _invalid_response()
            if next_link is not None:
                if _bounded_string(next_link, maximum=8192) is None:
                    _invalid_response()
                url = next_link
                continue
            if _bounded_string(delta, maximum=8192) is None:
                _invalid_response()
            _graph_url(delta)
            return DeltaResult(tuple(by_id.values()), delta)
        _invalid_response()

    def _redirect(self, drive_id, item_id):
        url = f"{_GRAPH_ROOT}/drives/{quote(drive_id, safe='')}/items/{quote(item_id, safe='')}/content"
        request = Request(url, headers={"Authorization": f"Bearer {self._token()}"})
        location = None
        failed = False
        redirect_headers = None
        try:
            response = self._opener.open(request, timeout=self._timeout)
            status = getattr(response, "status", None)
            if type(status) is int and status in {301, 302, 303, 307, 308}:
                redirect_headers = response.headers
            else:
                failed = True
        except HTTPError as error:
            if error.code in {301, 302, 303, 307, 308}:
                redirect_headers = error.headers
            else:
                failed = True
        except Exception:
            failed = True
        if redirect_headers is not None:
            try:
                location = redirect_headers.get("Location")
            except Exception:
                failed = True
        if failed or _bounded_string(location, maximum=8192) is None:
            _download_failed()
        return _download_url(location, self.tenant_hostname, self._resolver)

    def download_content(self, drive_id: str, item: GraphItem, destination: Path, max_bytes: int) -> DownloadedFile:
        destination = Path(destination)
        destination_present = False
        destination_invalid = False
        try:
            os.lstat(destination)
            destination_present = True
        except FileNotFoundError:
            pass
        except Exception:
            destination_invalid = True
        if (
            _identifier(drive_id) is None
            or _identifier(getattr(item, "id", None)) is None
            or type(max_bytes) is not int
            or not 0 < max_bytes <= _MAX_DOWNLOAD_BYTES
            or destination_present
            or destination_invalid
            or not destination.parent.is_dir()
            or destination.parent.is_symlink()
        ):
            raise GraphError("Invalid download destination")
        location = self._redirect(drive_id, item.id)
        request = Request(location, headers={"Accept": "application/octet-stream"})
        failed = False
        try:
            response = self._opener.open(request, timeout=self._timeout)
        except Exception:
            failed = True
            response = None
        if failed:
            _download_failed()
        stream_failed = False
        try:
            with response:
                if type(getattr(response, "status", None)) is not int or response.status != 200:
                    _download_failed()
                peer = _peer_ip(response)
                if peer is not None and _unsafe_address(ipaddress.ip_address(peer)):
                    _download_failed()
                content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
                if content_type.startswith("text/") or content_type in {"application/json", "text/html"}:
                    _download_failed()
                length = response.headers.get("Content-Length")
                invalid_length = False
                try:
                    length = int(length) if length is not None else None
                except (TypeError, ValueError):
                    invalid_length = True
                    length = None
                if invalid_length:
                    _download_failed()
                if length is not None and (length < 0 or length > max_bytes):
                    raise GraphError("Download too large")

                part = destination.with_name(f"{destination.name}.{secrets.token_hex(8)}.part")
                digest = hashlib.sha256()
                total = 0
                with part.open("xb") as output:
                    while True:
                        chunk = response.read(min(1024 * 1024, max_bytes + 1 - total))
                        if not chunk:
                            break
                        if not isinstance(chunk, bytes):
                            _download_failed()
                        total += len(chunk)
                        if total > max_bytes:
                            raise GraphError("Download too large")
                        digest.update(chunk)
                        output.write(chunk)
                    output.flush()
                    os.fsync(output.fileno())
                if length is not None and total != length:
                    _download_failed()
                conflict = False
                publish_failed = False
                try:
                    os.link(part, destination)
                except FileExistsError:
                    conflict = True
                except Exception:
                    publish_failed = True
                if conflict:
                    raise GraphError("Invalid download destination")
                if publish_failed:
                    _download_failed()
                return DownloadedFile(destination, total, digest.hexdigest())
        except GraphError:
            raise
        except Exception:
            stream_failed = True
        if stream_failed:
            _download_failed()
