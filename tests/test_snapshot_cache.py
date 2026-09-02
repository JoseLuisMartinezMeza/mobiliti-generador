import copy
import threading

import pytest

from mobiliti_saas.quote_engine.snapshot_cache import SnapshotCache


class FakeS3Error(Exception):
    def __init__(self, status, code=""):
        self.response = {
            "ResponseMetadata": {"HTTPStatusCode": status},
            "Error": {"Code": code or str(status)},
        }


class Body:
    def __init__(self, content):
        self.content = content
        self.position = 0
        self.closed = False

    def read(self, amount=-1):
        if amount < 0:
            amount = len(self.content) - self.position
        result = self.content[self.position:self.position + amount]
        self.position += len(result)
        return result

    def close(self):
        self.closed = True


class FakeS3:
    def __init__(self):
        self.objects = {}
        self.put_calls = []
        self.get_calls = []
        self.fail_get = None
        self.last_body = None

    def get_object(self, *, Bucket, Key):
        self.get_calls.append((Bucket, Key))
        if self.fail_get:
            raise self.fail_get
        try:
            object_data = self.objects[(Bucket, Key)]
        except KeyError:
            raise FakeS3Error(404, "NoSuchKey") from None
        self.last_body = Body(object_data["Body"])
        return {**object_data, "Body": self.last_body}

    def put_object(self, **kwargs):
        self.put_calls.append(kwargs)
        key = (kwargs["Bucket"], kwargs["Key"])
        if key in self.objects:
            raise FakeS3Error(412, "PreconditionFailed")
        self.objects[key] = {
            "Body": kwargs["Body"],
            "ContentType": kwargs["ContentType"],
            "ContentEncoding": kwargs["ContentEncoding"],
            "CacheControl": kwargs["CacheControl"],
            "Metadata": copy.deepcopy(kwargs["Metadata"]),
        }


def snapshot(version="v1"):
    return {"supplier": "offiho", "source_hash": version, "payload": {"items": []}}


def cache_kwargs(s3, loader, **overrides):
    values = {
        "namespace": "https://example.supabase.co",
        "supplier": "offiho",
        "revision": "v1:2026-09-02",
        "loader": loader,
        "validator": lambda row: row.get("source_hash") == "v1",
        "client_factory": lambda: s3,
        "bucket": "quote-files",
    }
    values.update(overrides)
    return values


def test_second_process_uses_private_snapshot_without_second_database_download():
    s3 = FakeS3()
    reads = []

    def loader():
        reads.append(1)
        return snapshot()

    first = SnapshotCache().load(**cache_kwargs(s3, loader))
    second = SnapshotCache().load(**cache_kwargs(s3, loader))

    assert first == second == snapshot()
    assert len(reads) == 1
    assert s3.put_calls[0]["Bucket"] == "quote-files"
    assert s3.put_calls[0]["Key"].startswith("internal/catalog-snapshots/v1/")
    assert "/" not in s3.put_calls[0]["Key"].removeprefix("internal/catalog-snapshots/v1/")
    assert s3.put_calls[0]["ContentType"] == "application/json"
    assert s3.put_calls[0]["ContentEncoding"] == "gzip"
    assert s3.put_calls[0]["CacheControl"] == "private,no-store"
    assert first is not second


def test_memory_hit_returns_isolated_copy_without_remote_or_database_read():
    s3 = FakeS3()
    reads = []
    cache = SnapshotCache()

    first = cache.load(**cache_kwargs(s3, lambda: reads.append(1) or snapshot()))
    first["payload"]["items"].append("mutated")
    gets_before = len(s3.get_calls)
    second = cache.load(**cache_kwargs(s3, lambda: reads.append(1) or snapshot()))

    assert second == snapshot()
    assert reads == [1]
    assert len(s3.get_calls) == gets_before
    assert cache.counters["memory_hit"] == 1


@pytest.mark.parametrize(
    ("field", "value"),
    [("namespace", "https://other.supabase.co"), ("supplier", "tarkett"), ("revision", "v2:2026-09-02")],
)
def test_distinct_cache_identity_never_reuses_snapshot(field, value):
    s3 = FakeS3()
    reads = []
    first = SnapshotCache()
    first.load(**cache_kwargs(s3, lambda: reads.append("first") or snapshot()))
    changed = {field: value}
    second = SnapshotCache()
    second.load(**cache_kwargs(s3, lambda: reads.append("second") or snapshot(), **changed))

    assert reads == ["first", "second"]


@pytest.mark.parametrize("failure", ["corrupt", "truncated", "too_large", "forbidden"])
def test_unusable_private_object_falls_back_to_authoritative_loader(failure):
    s3 = FakeS3()
    writer = SnapshotCache()
    writer.load(**cache_kwargs(s3, snapshot))
    (_, key), stored = next(iter(s3.objects.items()))
    if failure == "corrupt":
        stored["Metadata"]["sha256"] = "bad"
    elif failure == "truncated":
        stored["Body"] = stored["Body"][:-2]
    elif failure == "too_large":
        stored["Body"] = b"x" * (SnapshotCache.MAX_COMPRESSED_BYTES + 1)
    else:
        s3.fail_get = FakeS3Error(403, "AccessDenied")
    reads = []
    value = SnapshotCache().load(**cache_kwargs(s3, lambda: reads.append(1) or snapshot()))

    assert value == snapshot()
    assert reads == [1]
    assert s3.last_body is None or s3.last_body.closed


def test_authoritative_loader_error_is_not_hidden_by_cache_failure():
    s3 = FakeS3()

    def broken_loader():
        raise RuntimeError("database unavailable")

    with pytest.raises(RuntimeError, match="database unavailable"):
        SnapshotCache().load(**cache_kwargs(s3, broken_loader))


def test_invalid_authoritative_row_is_not_cached_or_returned():
    s3 = FakeS3()
    reads = []

    value = SnapshotCache().load(
        **cache_kwargs(s3, lambda: reads.append(1) or snapshot("wrong"))
    )

    assert value is None
    assert reads == [1]
    assert s3.objects == {}


def test_concurrent_reads_load_once_and_share_no_mutable_state():
    s3 = FakeS3()
    cache = SnapshotCache()
    started = threading.Event()
    allow = threading.Event()
    reads = []
    values = []

    def loader():
        reads.append(1)
        started.set()
        assert allow.wait(2)
        return snapshot()

    def read():
        values.append(cache.load(**cache_kwargs(s3, loader)))

    first = threading.Thread(target=read)
    second = threading.Thread(target=read)
    first.start()
    assert started.wait(2)
    second.start()
    allow.set()
    first.join(2)
    second.join(2)

    assert reads == [1]
    assert values == [snapshot(), snapshot()]
    assert values[0] is not values[1]


def test_cache_counter_records_authoritative_content_bytes():
    s3 = FakeS3()
    row = {"supplier": "offiho", "source_hash": "v1", "payload": {"padding": "abc"}}
    cache = SnapshotCache()
    cache.load(**cache_kwargs(s3, lambda: row))

    assert cache.counters["db_load"] == 1
    assert cache.counters["loader_content_bytes"] > 3
