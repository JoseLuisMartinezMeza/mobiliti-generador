import hashlib
import json
import socket
import stat
import sys
import types
from pathlib import Path
from urllib.error import HTTPError

import pytest

from mobiliti_saas.worker.catalog_sync.graph import (
    DeltaExpiredError,
    GraphCatalogClient,
    GraphError,
)
import mobiliti_saas.worker.catalog_sync.graph as graph_module


FIXTURES = Path("tests/fixtures/catalog_graph")
ENV = {
    "MS_GRAPH_TENANT_ID": "tenant-id",
    "MS_GRAPH_CLIENT_ID": "client-id",
    "MS_GRAPH_CERT_PATH": "certificate.pem",
    "MS_GRAPH_CERT_THUMBPRINT": "A1B2C3",
    "SHAREPOINT_HOSTNAME": "contoso.sharepoint.com",
    "SHAREPOINT_SITE_PATH": "/sites/Catalogs",
    "SHAREPOINT_DRIVE_NAME": "Documents",
    "SHAREPOINT_CATALOG_ROOT": "Suppliers",
}


class Response:
    def __init__(self, body=b"", *, status=200, headers=None, peer_ip=None):
        self._body = body
        self.status = status
        self.headers = headers or {"Content-Type": "application/json"}
        self.peer_ip = peer_ip
        self._offset = 0

    def read(self, size=-1):
        if size < 0:
            size = len(self._body) - self._offset
        chunk = self._body[self._offset : self._offset + size]
        self._offset += len(chunk)
        return chunk

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None


class BrokenResponse(Response):
    def read(self, size=-1):
        raise OSError("SECRET response body")


class BrokenContextResponse(Response):
    def __exit__(self, *_):
        raise RuntimeError("SECRET context manager")


class FakeApp:
    def __init__(self, token="test-token"):
        self.token = token
        self.scopes = []

    def acquire_token_for_client(self, *, scopes):
        self.scopes.append(scopes)
        return {"access_token": self.token}


class QueueOpener:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []

    def open(self, request, timeout):
        self.requests.append((request, timeout))
        if not self.responses:
            raise AssertionError("unexpected network request")
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


def json_response(value, **kwargs):
    return Response(json.dumps(value).encode(), **kwargs)


def client(opener, *, resolver=None, app=None):
    return GraphCatalogClient(
        app=app or FakeApp(),
        tenant_hostname="contoso.sharepoint.com",
        site_path="/sites/Catalogs",
        drive_name="Documents",
        catalog_root="Suppliers",
        opener=opener,
        resolver=resolver or public_resolver,
    )


def public_resolver(host, port, **_kwargs):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]


@pytest.fixture(autouse=True)
def no_live_sockets(monkeypatch):
    class LiveNetworkAttempt(BaseException):
        pass

    def fail(*_args, **_kwargs):
        raise LiveNetworkAttempt("live network is forbidden")

    monkeypatch.setattr(socket, "create_connection", fail)
    monkeypatch.setattr(socket, "getaddrinfo", fail)


def test_environment_builds_certificate_app_only_when_called(monkeypatch, tmp_path):
    cert = tmp_path / "certificate.pem"
    cert.write_text("private certificate", encoding="ascii")
    values = ENV | {"MS_GRAPH_CERT_PATH": str(cert)}
    calls = []

    class ConfidentialClientApplication:
        def __init__(self, *args, **kwargs):
            calls.append((args, kwargs))

    monkeypatch.setitem(sys.modules, "msal", types.SimpleNamespace(ConfidentialClientApplication=ConfidentialClientApplication))
    monkeypatch.setattr("os.environ", values)

    graph = GraphCatalogClient.from_environment()

    assert graph.tenant_hostname == "contoso.sharepoint.com"
    assert calls == [(('client-id',), {
        "authority": "https://login.microsoftonline.com/tenant-id",
        "client_credential": {"private_key": "private certificate", "thumbprint": "A1B2C3"},
    })]


@pytest.mark.parametrize("field", sorted(ENV))
def test_environment_rejects_missing_or_blank_values(monkeypatch, field):
    values = ENV.copy()
    values[field] = " "
    monkeypatch.setattr("os.environ", values)
    with pytest.raises(GraphError, match="Invalid Graph configuration"):
        GraphCatalogClient.from_environment()


def test_environment_rejects_unsafe_or_oversized_certificate(monkeypatch, tmp_path):
    for path in (tmp_path, tmp_path / "missing.pem"):
        monkeypatch.setattr("os.environ", ENV | {"MS_GRAPH_CERT_PATH": str(path)})
        with pytest.raises(GraphError, match="Invalid Graph configuration"):
            GraphCatalogClient.from_environment()
    cert = tmp_path / "large.pem"
    cert.write_bytes(b"x" * (1024 * 1024 + 1))
    monkeypatch.setattr("os.environ", ENV | {"MS_GRAPH_CERT_PATH": str(cert)})
    with pytest.raises(GraphError, match="Invalid Graph configuration"):
        GraphCatalogClient.from_environment()


def test_environment_reads_certificate_once_through_a_closed_descriptor(monkeypatch, tmp_path):
    cert = tmp_path / "certificate.pem"
    cert.write_text("private certificate", encoding="ascii")
    monkeypatch.setattr("os.environ", ENV | {"MS_GRAPH_CERT_PATH": str(cert)})
    monkeypatch.setitem(sys.modules, "msal", types.SimpleNamespace(ConfidentialClientApplication=lambda *_a, **_kw: FakeApp()))
    real_open = graph_module.os.open
    real_close = graph_module.os.close
    opened = []
    closed = []

    def tracked_open(path, flags):
        descriptor = real_open(path, flags)
        opened.append(descriptor)
        return descriptor

    def tracked_close(descriptor):
        closed.append(descriptor)
        return real_close(descriptor)

    monkeypatch.setattr(graph_module.os, "open", tracked_open)
    monkeypatch.setattr(graph_module.os, "close", tracked_close)
    monkeypatch.setattr(Path, "read_text", lambda *_a, **_kw: pytest.fail("path reopened"))
    monkeypatch.setattr(Path, "stat", lambda *_a, **_kw: pytest.fail("path stat used"))
    monkeypatch.setattr(Path, "is_file", lambda *_a, **_kw: pytest.fail("path is_file used"))
    monkeypatch.setattr(Path, "is_symlink", lambda *_a, **_kw: pytest.fail("path is_symlink used"))

    GraphCatalogClient.from_environment()

    assert len(opened) == 1
    assert closed == opened


@pytest.mark.parametrize("unsafe", ["symlink", "identity-change"])
def test_environment_rejects_certificate_descriptor_races(monkeypatch, tmp_path, unsafe):
    cert = tmp_path / "certificate.pem"
    cert.write_text("private certificate", encoding="ascii")
    monkeypatch.setattr("os.environ", ENV | {"MS_GRAPH_CERT_PATH": str(cert)})
    original = graph_module.os.lstat(cert)
    if unsafe == "symlink":
        monkeypatch.setattr(
            graph_module.os,
            "lstat",
            lambda _path: types.SimpleNamespace(st_mode=stat.S_IFLNK, st_dev=original.st_dev, st_ino=original.st_ino),
        )
    else:
        real_fstat = graph_module.os.fstat

        def changed_identity(descriptor):
            opened = real_fstat(descriptor)
            return types.SimpleNamespace(
                st_mode=opened.st_mode,
                st_dev=opened.st_dev,
                st_ino=opened.st_ino + 1,
                st_size=opened.st_size,
            )

        monkeypatch.setattr(graph_module.os, "fstat", changed_identity)

    with pytest.raises(GraphError, match="Invalid Graph configuration") as caught:
        GraphCatalogClient.from_environment()
    assert caught.value.__context__ is None


def test_msal_construction_runtime_error_is_stable_and_redacted(monkeypatch, tmp_path):
    cert = tmp_path / "certificate.pem"
    cert.write_text("private certificate", encoding="ascii")
    monkeypatch.setattr("os.environ", ENV | {"MS_GRAPH_CERT_PATH": str(cert)})

    def fail(*_args, **_kwargs):
        raise RuntimeError("SECRET certificate constructor")

    monkeypatch.setitem(sys.modules, "msal", types.SimpleNamespace(ConfidentialClientApplication=fail))
    with pytest.raises(GraphError, match="Graph authentication unavailable") as caught:
        GraphCatalogClient.from_environment()
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert "SECRET" not in str(caught.value)


def test_resolvers_encode_paths_and_use_exact_default_scope():
    opener = QueueOpener(
        json_response({"id": "site-id"}),
        json_response({"value": [{"id": "drive-id", "name": "Documents"}]}),
        json_response({"id": "item-id", "name": "A B.pdf", "size": 3, "file": {"mimeType": "application/pdf"}}),
    )
    app = FakeApp()
    graph = client(opener, app=app)

    assert graph.resolve_site("contoso.sharepoint.com", "/sites/A B") == "site-id"
    assert graph.resolve_drive("site-id", "Documents") == "drive-id"
    assert graph.resolve_item("drive-id", "Folder/A B.pdf").id == "item-id"
    urls = [request.full_url for request, _ in opener.requests]
    assert urls[0].endswith("/sites/contoso.sharepoint.com:/sites/A%20B")
    assert urls[2].endswith("/root:/Folder/A%20B.pdf")
    assert app.scopes == [["https://graph.microsoft.com/.default"]] * 3


def test_delta_fixture_paginates_preserves_tombstone_and_returns_final_link():
    first = (FIXTURES / "initial-page.json").read_bytes()
    final = (FIXTURES / "delta-page.json").read_bytes()
    opener = QueueOpener(Response(first), Response(final))

    result = client(opener).iter_delta("drive-1", "folder-1")

    assert [item.id for item in result.items] == ["item-1", "item-2"]
    assert result.items[1].deleted == {"state": "deleted"}
    assert result.delta_link.endswith("$deltatoken=opaque-final")


@pytest.mark.parametrize("url", [
    "http://graph.microsoft.com/v1.0/x",
    "https://evil.example/v1.0/x",
    "https://graph.microsoft.com:444/v1.0/x",
    "https://user@graph.microsoft.com/v1.0/x",
    "https://graph.microsoft.com/v1.0/x#fragment",
])
def test_delta_rejects_untrusted_links_without_request(url):
    opener = QueueOpener()
    with pytest.raises(GraphError, match="Invalid Graph response"):
        client(opener).iter_delta("drive-1", "folder-1", url)
    assert opener.requests == []


def test_url_parser_failure_does_not_retain_delta_token(monkeypatch):
    def fail(_url):
        raise RuntimeError("SECRET delta token")

    monkeypatch.setattr(graph_module, "urlsplit", fail)
    with pytest.raises(GraphError, match="Invalid Graph response") as caught:
        client(QueueOpener()).iter_delta("drive", "folder")
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert "SECRET" not in str(caught.value)


def test_delta_rejects_cycles_and_expired_tokens_without_leaking_url():
    cycle = "https://graph.microsoft.com/v1.0/drives/d/items/f/delta?$skiptoken=SECRET"
    opener = QueueOpener(json_response({"value": [], "@odata.nextLink": cycle}))
    with pytest.raises(GraphError, match="Invalid Graph response") as caught:
        client(opener).iter_delta("d", "f", cycle)
    assert "SECRET" not in str(caught.value)

    expired = HTTPError(cycle, 410, "gone SECRET", {}, None)
    with pytest.raises(DeltaExpiredError, match="Graph delta expired") as caught:
        client(QueueOpener(expired)).iter_delta("d", "f", cycle)
    assert caught.value.__cause__ is None
    assert "SECRET" not in str(caught.value)


def test_http_410_is_delta_expiry_only_for_iter_delta():
    gone = lambda: HTTPError("https://graph.microsoft.com/SECRET", 410, "SECRET", {}, None)
    with pytest.raises(GraphError, match="Graph request failed") as caught:
        client(QueueOpener(gone())).resolve_site("contoso.sharepoint.com", "/sites/test")
    assert type(caught.value) is GraphError
    assert caught.value.__context__ is None

    with pytest.raises(DeltaExpiredError, match="Graph delta expired"):
        client(QueueOpener(gone())).iter_delta("drive", "folder")


def test_delta_rejects_oversized_json_and_duplicate_incompatible_items():
    with pytest.raises(GraphError, match="Invalid Graph response"):
        client(QueueOpener(Response(b"x" * (4 * 1024 * 1024 + 1)))).iter_delta("d", "f")
    duplicate = {"value": [
        {"id": "same", "name": "a.pdf", "file": {"mimeType": "application/pdf"}},
        {"id": "same", "name": "b.pdf", "file": {"mimeType": "application/pdf"}},
    ], "@odata.deltaLink": "https://graph.microsoft.com/v1.0/delta?$deltatoken=x"}
    with pytest.raises(GraphError, match="Invalid Graph response"):
        client(QueueOpener(json_response(duplicate))).iter_delta("d", "f")


def test_delta_rejects_bad_final_link_and_page_or_item_limits(monkeypatch):
    with pytest.raises(GraphError, match="Invalid Graph response"):
        client(QueueOpener(json_response({"value": [], "@odata.deltaLink": "https://evil.test/delta"}))).iter_delta("d", "f")

    next_link = "https://graph.microsoft.com/v1.0/delta?$skiptoken=x"
    monkeypatch.setattr(graph_module, "_MAX_PAGES", 1)
    with pytest.raises(GraphError, match="Invalid Graph response"):
        client(QueueOpener(json_response({"value": [], "@odata.nextLink": next_link}))).iter_delta("d", "f")

    monkeypatch.setattr(graph_module, "_MAX_ITEMS", 1)
    rows = [{"id": str(index), "name": f"{index}.pdf", "file": {"mimeType": "application/pdf"}} for index in range(2)]
    with pytest.raises(GraphError, match="Invalid Graph response"):
        client(QueueOpener(json_response({"value": rows, "@odata.deltaLink": next_link}))).iter_delta("d", "f")


def test_download_splits_redirect_drops_bearer_and_hashes_stream(tmp_path):
    location = "https://contoso.sharepoint.com/download/file?sig=opaque"
    graph_redirect = HTTPError("redacted", 302, "found", {"Location": location}, None)
    body = b"catalog bytes"
    opener = QueueOpener(
        graph_redirect,
        Response(body, headers={"Content-Type": "application/pdf", "Content-Length": str(len(body))}, peer_ip="93.184.216.34"),
    )
    item = client(QueueOpener(json_response({"id": "item", "name": "file.pdf", "size": len(body), "file": {"mimeType": "application/pdf"}}))).resolve_item("drive", "file.pdf")
    destination = tmp_path / "file.pdf"

    downloaded = client(opener).download_content("drive", item, destination, 1024)

    assert downloaded.path == destination
    assert downloaded.size == len(body)
    assert downloaded.sha256 == hashlib.sha256(body).hexdigest()
    assert opener.requests[0][0].get_header("Authorization") == "Bearer test-token"
    assert opener.requests[1][0].get_header("Authorization") is None
    assert destination.read_bytes() == body
    parts = list(tmp_path.glob("*.part"))
    assert len(parts) == 1
    assert parts[0].read_bytes() == body


@pytest.mark.parametrize("host", ["contoso.sharepoint.com.evil.test", "notsharepoint.com", "sharepoint.com.evil.test"])
def test_download_rejects_substring_hosts(host, tmp_path):
    redirect = HTTPError("redacted", 302, "found", {"Location": f"https://{host}/file"}, None)
    item = types.SimpleNamespace(id="item", name="file.pdf")
    with pytest.raises(GraphError, match="Invalid download redirect"):
        client(QueueOpener(redirect)).download_content("drive", item, tmp_path / "file.pdf", 1024)


@pytest.mark.parametrize("address", ["127.0.0.1", "10.0.0.1", "169.254.1.1", "224.0.0.1", "192.0.2.1"])
def test_download_rejects_private_or_reserved_dns(address, tmp_path):
    redirect = HTTPError("redacted", 302, "found", {"Location": "https://contoso.sharepoint.com/file"}, None)
    resolver = lambda host, port, **kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, port))]
    with pytest.raises(GraphError, match="Invalid download redirect"):
        client(QueueOpener(redirect), resolver=resolver).download_content(
            "drive", types.SimpleNamespace(id="item", name="file.pdf"), tmp_path / "file.pdf", 1024
        )


def test_download_redacts_nonstandard_resolver_failure(tmp_path):
    redirect = HTTPError("redacted", 302, "found", {"Location": "https://contoso.sharepoint.com/file"}, None)

    def resolver(*_args, **_kwargs):
        raise RuntimeError("SECRET resolver data")

    with pytest.raises(GraphError, match="Invalid download redirect") as caught:
        client(QueueOpener(redirect), resolver=resolver).download_content(
            "drive", types.SimpleNamespace(id="item", name="file.pdf"), tmp_path / "file.pdf", 1024
        )
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert "SECRET" not in str(caught.value)


def test_download_rejects_peer_ip_length_and_stream_overflow_preserving_part(tmp_path):
    item = types.SimpleNamespace(id="item", name="file.pdf")
    location = "https://contoso.sharepoint.com/file"
    redirect = lambda: HTTPError("redacted", 302, "found", {"Location": location}, None)

    with pytest.raises(GraphError, match="Download failed"):
        client(QueueOpener(redirect(), Response(b"x", headers={"Content-Type": "application/pdf"}, peer_ip="127.0.0.1"))).download_content(
            "drive", item, tmp_path / "peer.pdf", 10
        )
    with pytest.raises(GraphError, match="Download too large"):
        client(QueueOpener(redirect(), Response(b"x", headers={"Content-Type": "application/pdf", "Content-Length": "11"}))).download_content(
            "drive", item, tmp_path / "length.pdf", 10
        )
    with pytest.raises(GraphError, match="Download too large"):
        client(QueueOpener(redirect(), Response(b"x" * 11, headers={"Content-Type": "application/pdf"}))).download_content(
            "drive", item, tmp_path / "stream.pdf", 10
        )
    assert not (tmp_path / "stream.pdf").exists()
    assert len(list(tmp_path.glob("*.part"))) == 1


@pytest.mark.parametrize(
    "response",
    [
        Response(b"error", status=503, headers={"Content-Type": "application/pdf"}),
        Response(b"<html>", headers={"Content-Type": "text/html"}),
        TimeoutError("SECRET timeout"),
    ],
    ids=["status", "content-type", "timeout"],
)
def test_download_rejects_status_content_type_and_timeout(response, tmp_path):
    redirect = HTTPError("redacted", 302, "found", {"Location": "https://contoso.sharepoint.com/file"}, None)
    with pytest.raises(GraphError, match="Download failed") as caught:
        client(QueueOpener(redirect, response)).download_content(
            "drive", types.SimpleNamespace(id="item", name="file.pdf"), tmp_path / "file.pdf", 100
        )
    assert caught.value.__cause__ is None
    assert "SECRET" not in str(caught.value)


def test_download_never_overwrites_and_source_has_no_deletion_api(tmp_path):
    destination = tmp_path / "file.pdf"
    destination.write_bytes(b"existing")
    with pytest.raises(GraphError, match="Invalid download destination"):
        client(QueueOpener()).download_content("drive", types.SimpleNamespace(id="item", name="file.pdf"), destination, 10)
    assert destination.read_bytes() == b"existing"
    source = Path("mobiliti_saas/worker/catalog_sync/graph.py").read_text(encoding="utf-8")
    assert all(word not in source for word in ("unlink(", "remove(", "rmdir("))
    assert "destination.exists" not in source
    assert "os.rename" not in source


def test_download_publication_is_exclusive_under_race_and_retains_complete_part(monkeypatch, tmp_path):
    location = "https://contoso.sharepoint.com/file"
    redirect = HTTPError("redacted", 302, "found", {"Location": location}, None)
    body = b"complete catalog"
    destination = tmp_path / "file.pdf"
    real_link = graph_module.os.link

    def lose_race(source, target):
        Path(target).write_bytes(b"competitor")
        return real_link(source, target)

    monkeypatch.setattr(graph_module.os, "link", lose_race)
    with pytest.raises(GraphError, match="Invalid download destination") as caught:
        client(QueueOpener(redirect, Response(body, headers={"Content-Type": "application/pdf"}))).download_content(
            "drive", types.SimpleNamespace(id="item", name="file.pdf"), destination, 100
        )

    assert caught.value.__context__ is None
    assert destination.read_bytes() == b"competitor"
    parts = list(tmp_path.glob("*.part"))
    assert len(parts) == 1
    assert parts[0].read_bytes() == body


def test_download_rejects_truncated_body_and_redacts_stream_failure(tmp_path):
    location = "https://contoso.sharepoint.com/file"
    redirect = lambda: HTTPError("redacted", 302, "found", {"Location": location}, None)
    item = types.SimpleNamespace(id="item", name="file.pdf")
    with pytest.raises(GraphError, match="Download failed"):
        client(QueueOpener(redirect(), Response(b"short", headers={"Content-Type": "application/pdf", "Content-Length": "20"}))).download_content(
            "drive", item, tmp_path / "short.pdf", 100
        )
    with pytest.raises(GraphError, match="Download failed") as caught:
        client(QueueOpener(redirect(), BrokenResponse(headers={"Content-Type": "application/pdf"}))).download_content(
            "drive", item, tmp_path / "broken.pdf", 100
        )
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert "SECRET" not in str(caught.value)


def test_msal_requirement_is_exactly_once_and_no_dependency_was_added():
    lines = Path("mobiliti_saas/worker/requirements.txt").read_text(encoding="utf-8").splitlines()
    assert lines.count("msal>=1.31,<2") == 1
    assert lines == [
        "openpyxl>=3.1.0",
        "Pillow>=10.0.0",
        "psycopg[binary]>=3.2.0",
        "PyMuPDF>=1.27.0",
        "boto3>=1.34.0",
        "msal>=1.31,<2",
    ]


def test_transport_and_token_failures_are_stable_and_redacted():
    secret = "SUPER-SECRET-TOKEN"
    app = FakeApp(secret)
    app.acquire_token_for_client = lambda **_: {"error": "invalid", "error_description": secret}
    with pytest.raises(GraphError, match="Graph authentication failed") as caught:
        client(QueueOpener(), app=app).resolve_site("contoso.sharepoint.com", "/sites/test")
    assert secret not in str(caught.value)

    with pytest.raises(GraphError, match="Graph request failed") as caught:
        client(QueueOpener(OSError(secret))).resolve_site("contoso.sharepoint.com", "/sites/test")
    assert caught.value.__cause__ is None
    assert secret not in str(caught.value)


@pytest.mark.parametrize(
    "response",
    [RuntimeError("SECRET opener URL token"), BrokenContextResponse(b"{}")],
    ids=["opener", "context-manager"],
)
def test_nonstandard_transport_exceptions_are_stable_and_redacted(response):
    with pytest.raises(GraphError, match="Graph request failed") as caught:
        client(QueueOpener(response)).resolve_site("contoso.sharepoint.com", "/sites/test")
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert "SECRET" not in str(caught.value)
