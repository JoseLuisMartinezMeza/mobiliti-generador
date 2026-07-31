from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts import build_sunon_cdmx_v1c_template as builder


ROOT = Path(__file__).resolve().parents[1]
OFFICIAL = (
    ROOT
    / "mobiliti_saas"
    / "worker"
    / "templates"
    / "Formato Cotizacion 2026 Oficial.xlsx"
)
VISUAL_SOURCE = (
    Path.home() / "Downloads" / "Formato-Cotizacion-Unico - Sunon-Cdmx-V1C.xlsx"
)


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _fake_payload(candidate: Path) -> dict[str, object]:
    return {"sha256": hashlib.sha256(candidate.read_bytes()).hexdigest()}


def test_builder_rejects_same_output_and_contract_before_build(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    destination = tmp_path / "same.xlsx"
    called = False

    def fake_build(*_args) -> None:
        nonlocal called
        called = True

    monkeypatch.setattr(builder, "_build_with_excel", fake_build)

    with pytest.raises(ValueError, match="destinos distintos"):
        builder.build(
            OFFICIAL,
            VISUAL_SOURCE,
            destination,
            destination,
        )

    assert called is False


def test_builder_requires_rebuild_before_touching_existing_pair(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "template.xlsx"
    contract = tmp_path / "contract.json"
    output.write_bytes(b"old-xlsx")
    contract.write_text('{"sha256":"old"}', encoding="utf-8")
    called = False

    def fake_build(*_args) -> None:
        nonlocal called
        called = True

    monkeypatch.setattr(builder, "_build_with_excel", fake_build)

    with pytest.raises(FileExistsError, match="--rebuild"):
        builder.build(OFFICIAL, VISUAL_SOURCE, output, contract)

    assert called is False
    assert output.read_bytes() == b"old-xlsx"
    assert contract.read_text(encoding="utf-8") == '{"sha256":"old"}'


def test_builder_publishes_matching_pair_and_keeps_backups(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "template.xlsx"
    contract = tmp_path / "contract.json"
    output.write_bytes(b"old-xlsx")
    contract.write_text('{"sha256":"old"}', encoding="utf-8")
    generated = b"new-xlsx"

    def fake_build(_official: Path, _visual: Path, candidate: Path) -> None:
        candidate.write_bytes(generated)

    monkeypatch.setattr(builder, "_build_with_excel", fake_build)
    monkeypatch.setattr(builder, "_contract_payload", _fake_payload)

    payload = builder.build(
        OFFICIAL,
        VISUAL_SOURCE,
        output,
        contract,
        rebuild=True,
    )

    assert output.read_bytes() == generated
    assert json.loads(contract.read_text(encoding="utf-8"))["sha256"] == (
        _sha256_bytes(generated)
    )
    assert payload["sha256"] == _sha256_bytes(generated)
    assert any(
        path.read_bytes() == b"old-xlsx"
        for path in tmp_path.glob("template.xlsx.backup-*")
    )
    assert any(
        path.read_text(encoding="utf-8") == '{"sha256":"old"}'
        for path in tmp_path.glob("contract.json.backup-*")
    )


def test_builder_restores_both_destinations_if_second_publish_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "template.xlsx"
    contract = tmp_path / "contract.json"
    old_output = b"old-xlsx"
    old_contract = '{"sha256":"old"}'
    output.write_bytes(old_output)
    contract.write_text(old_contract, encoding="utf-8")
    generated = b"new-xlsx"

    def fake_build(_official: Path, _visual: Path, candidate: Path) -> None:
        candidate.write_bytes(generated)

    real_replace = builder.os.replace
    failed_once = False

    def fail_second_publish(source, destination) -> None:
        nonlocal failed_once
        source_path = Path(source)
        destination_path = Path(destination)
        if (
            not failed_once
            and ".building-" in source_path.name
            and source_path.suffix == ".json"
            and destination_path == contract
        ):
            failed_once = True
            raise OSError("simulated second publication failure")
        real_replace(source, destination)

    monkeypatch.setattr(builder, "_build_with_excel", fake_build)
    monkeypatch.setattr(builder, "_contract_payload", _fake_payload)
    monkeypatch.setattr(builder.os, "replace", fail_second_publish)

    with pytest.raises(OSError, match="simulated"):
        builder.build(
            OFFICIAL,
            VISUAL_SOURCE,
            output,
            contract,
            rebuild=True,
        )

    assert output.read_bytes() == old_output
    assert contract.read_text(encoding="utf-8") == old_contract
    assert any(
        path.read_bytes() == generated
        for path in tmp_path.glob("template.xlsx.failed-publication-*")
    )
    assert list(tmp_path.glob("*.failed-build-*"))
