from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import zlib

import pytest

from scripts import recover_cylex_labenze_document_candidates as recovery


WORKSPACE = Path(__file__).resolve().parents[1]
CANONICAL_SOURCE = Path(
    r"C:\Users\pepem\AppData\Local\Temp\labenze-document-audit-20260819T044340"
    r"\cylex-chrome.bin"
)
CANONICAL_ROWS = (
    WORKSPACE
    / ".mobiliti_dev_store"
    / "visual-remediation"
    / "web-intake-20260819T141500Z"
    / "normalized-research.jsonl"
)


def test_audited_selection_map_is_exact_and_has_no_shared_xobject():
    selections = recovery.AUDITED_SELECTIONS

    assert len(selections) == 75
    assert len({selection.internal_id for selection in selections}) == 75
    assert len({selection.xref for selection in selections}) == 75
    assert all(selection.page_number > 0 for selection in selections)
    assert all(
        selection.bbox[0] < selection.bbox[2]
        and selection.bbox[1] < selection.bbox[3]
        for selection in selections
    )

    focal = {selection.source_code: selection for selection in selections}
    assert (focal["106-00850"].page_number, focal["106-00850"].xref) == (25, 168)
    assert (focal["155-10650"].page_number, focal["155-10650"].xref) == (106, 588)
    assert (focal["157-10650"].page_number, focal["157-10650"].xref) == (117, 650)
    assert (focal["107-00224"].page_number, focal["107-00224"].xref) == (108, 602)
    assert not recovery.CYLEX_SEMANTICALLY_BLOCKED_CODES & set(focal)


def test_read_exact_source_fails_before_pdf_parser_on_hash_size_or_magic(tmp_path):
    source = tmp_path / "source.bin"
    source.write_bytes(b"%PDF-safe-fixture")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()

    assert recovery._read_exact_source(
        source,
        expected_sha256=digest,
        expected_bytes=len(source.read_bytes()),
    ) == source.read_bytes()

    with pytest.raises(recovery.RecoveryError, match="SOURCE_HASH"):
        recovery._read_exact_source(
            source,
            expected_sha256="0" * 64,
            expected_bytes=len(source.read_bytes()),
        )
    with pytest.raises(recovery.RecoveryError, match="SOURCE_SIZE"):
        recovery._read_exact_source(
            source,
            expected_sha256=digest,
            expected_bytes=len(source.read_bytes()) + 1,
        )

    non_pdf = tmp_path / "not-pdf.bin"
    non_pdf.write_bytes(b"not a pdf")
    with pytest.raises(recovery.RecoveryError, match="SOURCE_MAGIC"):
        recovery._read_exact_source(
            non_pdf,
            expected_sha256=hashlib.sha256(non_pdf.read_bytes()).hexdigest(),
            expected_bytes=len(non_pdf.read_bytes()),
        )


def test_bounded_flate_decoder_enforces_size_and_expansion_ratio():
    raw = zlib.compress(b"A" * 4096)
    assert recovery._bounded_flate_decode(
        raw,
        expected_size=4096,
        max_expanded_bytes=8192,
        max_ratio=512,
    ) == b"A" * 4096

    with pytest.raises(recovery.RecoveryError, match="PDF_STREAM_SIZE"):
        recovery._bounded_flate_decode(
            raw,
            expected_size=4095,
            max_expanded_bytes=8192,
            max_ratio=512,
        )
    with pytest.raises(recovery.RecoveryError, match="PDF_STREAM_RATIO"):
        recovery._bounded_flate_decode(
            raw,
            expected_size=4096,
            max_expanded_bytes=8192,
            max_ratio=2,
        )


@pytest.mark.parametrize(
    ("raw_object", "expected"),
    [
        (b"<< /Subtype /Link /A << /S /URI /URI (https://example.test) >> >>", "discard_uri"),
        (b"<< /Type /Action /S /GoTo /D [37 0 R /Fit] >>", "discard_goto"),
        (b"<< /Type /XObject /Subtype /Image /Width 10 /Height 10 >>", None),
    ],
)
def test_only_uri_and_internal_goto_are_explicitly_discardable(raw_object, expected):
    assert recovery._classify_discardable_object(raw_object) == expected


@pytest.mark.parametrize(
    "token",
    [b"JavaScript", b"JS", b"Launch", b"GoToR", b"SubmitForm", b"OpenAction", b"AA"],
)
def test_active_or_chained_pdf_actions_fail_closed(token):
    raw_object = b"<< /Type /Action /S /" + token + b" >>"
    with pytest.raises(recovery.RecoveryError, match="PDF_ACTIVE_CONTENT"):
        recovery._classify_discardable_object(raw_object)


def test_unknown_pdf_action_type_fails_closed():
    with pytest.raises(recovery.RecoveryError, match="PDF_ACTIVE_CONTENT"):
        recovery._classify_discardable_object(b"<< /Type /Action /S /Named /N /Print >>")


def test_output_must_be_new_and_under_local_ignored_artifact_root(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    allowed = workspace / ".superpowers" / "sdd" / "artifacts" / "run-1"

    assert recovery._validate_output_dir(allowed, workspace) == allowed.resolve()

    with pytest.raises(recovery.RecoveryError, match="OUTPUT_SCOPE"):
        recovery._validate_output_dir(workspace / ".mobiliti_dev_store" / "run", workspace)
    with pytest.raises(recovery.RecoveryError, match="OUTPUT_SCOPE"):
        recovery._validate_output_dir(tmp_path / "outside", workspace)

    allowed.mkdir(parents=True)
    with pytest.raises(recovery.RecoveryError, match="OUTPUT_EXISTS"):
        recovery._validate_output_dir(allowed, workspace)


@pytest.mark.skipif(
    not CANONICAL_ROWS.is_file() or not CANONICAL_SOURCE.is_file(),
    reason="Los artefactos locales canónicos de Task 6C no están disponibles",
)
def test_canonical_hash_pinned_recovery_is_isolated_and_emits_75_unapproved_xobjects():
    rows = recovery._load_bbox_rows(
        CANONICAL_ROWS,
        expected_sha256=recovery.CANONICAL_NORMALIZED_RESEARCH_SHA256,
    )
    source = recovery._read_exact_source(CANONICAL_SOURCE)

    payload = recovery.recover_in_subprocess(source, rows)

    assert payload["security"]["source_sha256"] == recovery.CYLEX_SOURCE_SHA256
    assert payload["security"]["source_bytes"] == 7_211_618
    assert payload["security"]["page_count"] == 121
    assert payload["security"]["input_document_accepted"] is False
    assert payload["security"]["extraction_mode"] == "selected_xobject_only"
    assert payload["security"]["discarded_actions"] == {"goto": 5, "uri": 131}
    assert payload["security"]["discarded_unreachable_xrefs"] == [7646]
    assert payload["worker"]["pid"] != os.getpid()
    assert payload["worker"]["memory_limit_bytes"] == recovery.MAX_WORKER_MEMORY_BYTES
    assert payload["worker"]["timeout_seconds"] == recovery.WORKER_TIMEOUT_SECONDS

    assert len(payload["candidates"]) == 75
    assert len(payload["assets"]) == 75
    assert sum(len(data) for data in payload["assets"].values()) <= recovery.MAX_OUTPUT_BYTES
    assert all(row["approved"] is False for row in payload["candidates"])
    assert all(row["reviewer"] == "" for row in payload["candidates"])
    assert all(row["reviewed_at"] is None for row in payload["candidates"])
    assert all(not any(row["checks"].values()) for row in payload["candidates"])
    assert all(row["page_rendered"] is False for row in payload["candidates"])
    assert all(row["extraction_mode"] == "selected_xobject" for row in payload["candidates"])
    assert all(row["asset"]["width"] == row["xobject"]["width"] for row in payload["candidates"])
    assert all(row["asset"]["height"] == row["xobject"]["height"] for row in payload["candidates"])
    assert len({row["asset"]["sha256"] for row in payload["candidates"]}) == 75


@pytest.mark.skipif(
    not CANONICAL_ROWS.is_file() or not CANONICAL_SOURCE.is_file(),
    reason="Los artefactos locales canónicos de Task 6C no están disponibles",
)
def test_output_writer_is_content_addressed_has_contact_sheets_and_refuses_overwrite(tmp_path):
    rows = recovery._load_bbox_rows(
        CANONICAL_ROWS,
        expected_sha256=recovery.CANONICAL_NORMALIZED_RESEARCH_SHA256,
    )
    source = recovery._read_exact_source(CANONICAL_SOURCE)
    payload = recovery.recover_in_subprocess(source, rows)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    output = workspace / ".superpowers" / "sdd" / "artifacts" / "canonical"

    summary = recovery.write_recovery_output(payload, output, workspace_root=workspace)

    assert summary["counts"] == {
        "approved": 0,
        "blocked_during_recovery": 0,
        "candidate_rows": 75,
        "contact_sheets": 5,
        "unique_assets": 75,
    }
    manifest_rows = [
        json.loads(line)
        for line in (output / "candidates.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert len(manifest_rows) == 75
    assert len(list((output / "candidates").glob("*.png"))) == 75
    assert len(list((output / "contact-sheets").glob("*.png"))) == 5
    assert (output / "security-report.json").is_file()
    assert (output / "artifact-hashes.json").is_file()

    with pytest.raises(recovery.RecoveryError, match="OUTPUT_EXISTS"):
        recovery.write_recovery_output(payload, output, workspace_root=workspace)
