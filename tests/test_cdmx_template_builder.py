from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from mobiliti_saas.quote_engine.ooxml_package import (
    PackageMutation,
    XlsxPackage,
    assert_packages_preserved,
)
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
WORKBOOK_PART = "xl/workbook.xml"
MC_NAMESPACE = "http://schemas.openxmlformats.org/markup-compatibility/2006"
X15AC_NAMESPACE = (
    "http://schemas.microsoft.com/office/spreadsheetml/2010/11/ac"
)


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _fake_payload(candidate: Path) -> dict[str, object]:
    return {"sha256": hashlib.sha256(candidate.read_bytes()).hexdigest()}


class _ComFailure(Exception):
    def __init__(self, hresult: int) -> None:
        super().__init__(hresult)
        self.hresult = hresult


class _NonComFailure(Exception):
    def __init__(self, hresult: int) -> None:
        super().__init__(hresult)
        self.hresult = hresult


class _SequencedWorkbooks:
    def __init__(self, outcomes: list[object]) -> None:
        self.outcomes = iter(outcomes)
        self.calls: list[tuple[object, ...]] = []

    def Open(self, *args):
        self.calls.append(args)
        outcome = next(self.outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class _SequencedRange:
    def __init__(
        self,
        *,
        copy_outcomes: list[object] | None = None,
        paste_outcomes: list[object] | None = None,
    ) -> None:
        self.copy_outcomes = iter(copy_outcomes or [None])
        self.paste_outcomes = iter(paste_outcomes or [None])
        self.copy_calls: list[dict[str, object]] = []
        self.paste_calls: list[dict[str, object]] = []
        self.clear_calls = 0

    def Copy(self, **kwargs):
        self.copy_calls.append(kwargs)
        outcome = next(self.copy_outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def PasteSpecial(self, **kwargs):
        self.paste_calls.append(kwargs)
        outcome = next(self.paste_outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def ClearContents(self) -> None:
        self.clear_calls += 1


class _RangeSheet:
    def __init__(self, ranges: dict[str, _SequencedRange]) -> None:
        self.ranges = ranges
        self.range_calls: list[str] = []

    def Range(self, address: str) -> _SequencedRange:
        self.range_calls.append(address)
        return self.ranges[address]


class _TailRange:
    def __init__(self) -> None:
        self.unmerge_calls = 0
        self.clear_calls = 0

    def UnMerge(self) -> None:
        self.unmerge_calls += 1

    def ClearContents(self) -> None:
        self.clear_calls += 1


class _TailSheet:
    def __init__(self, first_used_row: int, used_row_count: int) -> None:
        self.UsedRange = type(
            "UsedRange",
            (),
            {
                "Row": first_used_row,
                "Rows": type("Rows", (), {"Count": used_row_count})(),
            },
        )()
        self.tail = _TailRange()
        self.range_calls: list[str] = []

    def Range(self, address: str) -> _TailRange:
        self.range_calls.append(address)
        return self.tail


class _SequencedRangeSheet:
    def __init__(self, outcomes: list[object]) -> None:
        self.outcomes = iter(outcomes)
        self.range_calls: list[str] = []

    def Range(self, address: str) -> _SequencedRange:
        self.range_calls.append(address)
        outcome = next(self.outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class _Logo(_SequencedRange):
    Type = 13
    Left = 1
    Top = 2
    Width = 3
    Height = 4
    Placement = 5


class _Shapes:
    def __init__(self, items: list[_Logo]) -> None:
        self.items = items

    @property
    def Count(self) -> int:
        return len(self.items)

    def Item(self, index: int) -> _Logo:
        return self.items[index - 1]


class _Activatable:
    def Activate(self) -> None:
        pass


class _LogoSheet(_Activatable):
    def __init__(
        self,
        logos: list[_Logo],
        *,
        paste_outcomes: list[object] | None = None,
    ) -> None:
        self.Shapes = _Shapes(logos)
        self.Parent = _Activatable()
        self.paste_outcomes = iter(paste_outcomes or [None])
        self.paste_calls = 0

    def Paste(self) -> None:
        self.paste_calls += 1
        outcome = next(self.paste_outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        self.Shapes.items.append(_Logo())


class _WorksheetCollection:
    def __init__(
        self,
        *,
        count_outcomes: list[object],
        lookup_outcomes: dict[object, list[object]],
    ) -> None:
        self.count_outcomes = iter(count_outcomes)
        self.lookup_outcomes = {
            key: iter(outcomes) for key, outcomes in lookup_outcomes.items()
        }
        self.count_calls = 0
        self.lookup_calls: list[object] = []

    @property
    def Count(self) -> int:
        self.count_calls += 1
        outcome = next(self.count_outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def __call__(self, key):
        self.lookup_calls.append(key)
        outcome = next(self.lookup_outcomes[key])
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class _CopyingWorksheet:
    def __init__(self) -> None:
        self.copy_calls: list[tuple[object, object]] = []

    def Copy(self, before, after) -> None:
        self.copy_calls.append((before, after))


class _ClearableRange:
    def __init__(self) -> None:
        self.clear_calls = 0

    def ClearContents(self) -> None:
        self.clear_calls += 1


class _EmptyShapes:
    Count = 0


class _CopiedWorksheet:
    def __init__(self) -> None:
        self.UsedRange = _ClearableRange()
        self.Shapes = _EmptyShapes()


class _FormulaRange:
    def __init__(
        self,
        *,
        read_outcomes: list[object] | None = None,
        write_outcomes: list[object] | None = None,
    ) -> None:
        self.read_outcomes = iter(read_outcomes or [None])
        self.write_outcomes = iter(write_outcomes or [None])
        self.read_calls = 0
        self.write_calls: list[object] = []

    @property
    def Formula(self):
        self.read_calls += 1
        outcome = next(self.read_outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    @Formula.setter
    def Formula(self, value) -> None:
        self.write_calls.append(value)
        outcome = next(self.write_outcomes)
        if isinstance(outcome, Exception):
            raise outcome


class _RowHeight:
    def __init__(
        self,
        *,
        read_outcomes: list[object] | None = None,
        write_outcomes: list[object] | None = None,
    ) -> None:
        self.read_outcomes = iter(read_outcomes or [None])
        self.write_outcomes = iter(write_outcomes or [None])
        self.read_calls = 0
        self.write_calls: list[object] = []

    @property
    def RowHeight(self):
        self.read_calls += 1
        outcome = next(self.read_outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    @RowHeight.setter
    def RowHeight(self, value) -> None:
        self.write_calls.append(value)
        outcome = next(self.write_outcomes)
        if isinstance(outcome, Exception):
            raise outcome


class _RowsSheet:
    def __init__(self, outcomes: dict[int, list[object]]) -> None:
        self.outcomes = {
            row: iter(row_outcomes) for row, row_outcomes in outcomes.items()
        }
        self.row_calls: list[int] = []

    def Rows(self, row: int) -> _RowHeight:
        self.row_calls.append(row)
        outcome = next(self.outcomes[row])
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class _PopulateLumbroSheet:
    def __init__(self) -> None:
        self.ranges: dict[str, _SequencedRange] = {}
        self.PageSetup = _Activatable()

    def Range(self, address: str) -> _SequencedRange:
        return self.ranges.setdefault(address, _SequencedRange())

    def Rows(self, _address: str) -> _Activatable:
        return _Activatable()

    def Columns(self, _address: str) -> _Activatable:
        return _Activatable()


class _Workbook:
    def __init__(self, worksheets: _WorksheetCollection) -> None:
        self.Worksheets = worksheets


def _retry_com_for_test(action):
    return builder._retry_rejected_com(
        action,
        com_error_type=_ComFailure,
        max_attempts=3,
        sleep=lambda _delay: None,
    )


def test_copy_formats_retries_rejected_source_copy() -> None:
    failure = _ComFailure(-2147418111)
    source_range = _SequencedRange(copy_outcomes=[failure, None])
    target_range = _SequencedRange()

    builder._copy_formats(
        _RangeSheet({"A1": source_range}),
        _RangeSheet({"B2": target_range}),
        "A1",
        "B2",
        _retry_com_for_test,
    )

    assert source_range.copy_calls == [{}, {}]
    assert target_range.paste_calls == [{"Paste": builder.XL_PASTE_FORMATS}]


def test_copy_formats_retries_rejected_source_range_lookup() -> None:
    failure = _ComFailure(-2147418111)
    source_range = _SequencedRange()
    source_sheet = _SequencedRangeSheet([failure, source_range])
    target_range = _SequencedRange()

    builder._copy_formats(
        source_sheet,
        _RangeSheet({"B2": target_range}),
        "A1",
        "B2",
        _retry_com_for_test,
    )

    assert source_sheet.range_calls == ["A1", "A1"]
    assert source_range.copy_calls == [{}]
    assert target_range.paste_calls == [{"Paste": builder.XL_PASTE_FORMATS}]


def test_copy_formats_retries_paste_without_replaying_source_copy() -> None:
    failure = _ComFailure(-2147418111)
    source_range = _SequencedRange()
    target_range = _SequencedRange(paste_outcomes=[failure, None])

    source_sheet = _RangeSheet({"A1": source_range})
    target_sheet = _RangeSheet({"B2": target_range})

    builder._copy_formats(
        source_sheet,
        target_sheet,
        "A1",
        "B2",
        _retry_com_for_test,
    )

    assert source_range.copy_calls == [{}]
    assert source_sheet.range_calls == ["A1"]
    assert target_sheet.range_calls == ["B2", "B2"]
    assert target_range.paste_calls == [
        {"Paste": builder.XL_PASTE_FORMATS},
        {"Paste": builder.XL_PASTE_FORMATS},
    ]


def test_copy_all_retries_one_fixed_copy_destination_operation() -> None:
    failure = _ComFailure(-2147418111)
    source_range = _SequencedRange(copy_outcomes=[failure, None])
    target_range = _SequencedRange()
    source_sheet = _RangeSheet({"A1": source_range})
    target_sheet = _RangeSheet({"B2": target_range})

    builder._copy_all(
        source_sheet,
        target_sheet,
        "A1",
        "B2",
        _retry_com_for_test,
    )

    assert source_sheet.range_calls == ["A1", "A1"]
    assert target_sheet.range_calls == ["B2", "B2"]
    assert source_range.copy_calls == [
        {"Destination": target_range},
        {"Destination": target_range},
    ]


def test_copy_single_logo_retries_only_source_logo_copy() -> None:
    failure = _ComFailure(-2147418111)
    source_logo = _Logo(copy_outcomes=[failure, None])
    target_sheet = _LogoSheet([])

    builder._copy_single_logo(
        _LogoSheet([source_logo]),
        target_sheet,
        _retry_com_for_test,
    )

    assert source_logo.copy_calls == [{}, {}]
    assert target_sheet.paste_calls == 1


def test_copy_single_logo_does_not_retry_target_paste() -> None:
    failure = _ComFailure(-2147418111)
    source_logo = _Logo()
    target_sheet = _LogoSheet([], paste_outcomes=[failure, None])

    with pytest.raises(_ComFailure) as caught:
        builder._copy_single_logo(
            _LogoSheet([source_logo]),
            target_sheet,
            _retry_com_for_test,
        )

    assert caught.value is failure
    assert source_logo.copy_calls == [{}]
    assert target_sheet.paste_calls == 1


def test_lumbro_post_copy_lookup_retries_without_replaying_worksheet_copy() -> None:
    failure = _ComFailure(-2147418111)
    source_sheet = _CopyingWorksheet()
    after_sheet = object()
    copied_sheet = _CopiedWorksheet()
    source_worksheets = _WorksheetCollection(
        count_outcomes=[0],
        lookup_outcomes={"Cantidades Lumbro ": [source_sheet]},
    )
    target_worksheets = _WorksheetCollection(
        count_outcomes=[1],
        lookup_outcomes={
            1: [after_sheet],
            "Cantidades Lumbro ": [failure, copied_sheet],
        },
    )

    builder._copy_lumbro_surfaces(
        _Workbook(source_worksheets),
        _Workbook(target_worksheets),
        _retry_com_for_test,
    )

    assert source_sheet.copy_calls == [(None, after_sheet)]
    assert source_worksheets.lookup_calls == ["Cantidades Lumbro "]
    assert target_worksheets.count_calls == 1
    assert target_worksheets.lookup_calls == [
        1,
        "Cantidades Lumbro ",
        "Cantidades Lumbro ",
    ]
    assert copied_sheet.UsedRange.clear_calls == 1


def test_cotizacion_lookup_retries_before_presentation_runs_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    failure = _ComFailure(-2147418111)
    source_sheet = object()
    target_sheet = object()
    source_worksheets = _WorksheetCollection(
        count_outcomes=[0],
        lookup_outcomes={"Cotizacion": [failure, source_sheet]},
    )
    target_worksheets = _WorksheetCollection(
        count_outcomes=[0],
        lookup_outcomes={"Cotizacion": [target_sheet]},
    )
    presentation_calls: list[tuple[object, object, object]] = []

    monkeypatch.setattr(
        builder,
        "_copy_cotizacion_presentation",
        lambda source, target, retry: presentation_calls.append(
            (source, target, retry)
        ),
    )

    builder._copy_cotizacion_from_workbooks(
        _Workbook(source_worksheets),
        _Workbook(target_worksheets),
        _retry_com_for_test,
    )

    assert source_worksheets.lookup_calls == ["Cotizacion", "Cotizacion"]
    assert target_worksheets.lookup_calls == ["Cotizacion"]
    assert presentation_calls == [
        (source_sheet, target_sheet, _retry_com_for_test)
    ]


def test_copy_formula_retries_target_write_without_rereading_source() -> None:
    failure = _ComFailure(-2147418111)
    source_range = _FormulaRange(read_outcomes=["=SUM(A1:A2)"])
    target_range = _FormulaRange(write_outcomes=[failure, None])
    source_sheet = _SequencedRangeSheet([source_range])
    target_sheet = _SequencedRangeSheet([target_range, target_range])

    builder._copy_formula_with_retry(
        source_sheet,
        target_sheet,
        "J16",
        "J18",
        _retry_com_for_test,
    )

    assert source_sheet.range_calls == ["J16"]
    assert source_range.read_calls == 1
    assert target_sheet.range_calls == ["J18", "J18"]
    assert target_range.write_calls == ["=SUM(A1:A2)", "=SUM(A1:A2)"]


def test_copy_row_height_retries_target_write_without_rereading_source() -> None:
    failure = _ComFailure(-2147418111)
    source_row = _RowHeight(read_outcomes=[27.5])
    target_row = _RowHeight(write_outcomes=[failure, None])
    source_sheet = _RowsSheet({12: [source_row]})
    target_sheet = _RowsSheet({15: [target_row, target_row]})

    builder._copy_row_heights(
        source_sheet,
        target_sheet,
        12,
        15,
        1,
        _retry_com_for_test,
    )

    assert source_sheet.row_calls == [12]
    assert source_row.read_calls == 1
    assert target_sheet.row_calls == [15, 15]
    assert target_row.write_calls == [27.5, 27.5]


def test_populate_lumbro_retries_initial_lookup_without_extra_copy_paste() -> None:
    failure = _ComFailure(-2147418111)
    sheet = _PopulateLumbroSheet()
    worksheets = _WorksheetCollection(
        count_outcomes=[0],
        lookup_outcomes={"Cantidades Lumbro ": [failure, sheet]},
    )
    workbook = _Workbook(worksheets)
    workbook.Application = _Activatable()

    builder._populate_live_lumbro_quantities(workbook, _retry_com_for_test)

    assert worksheets.lookup_calls == [
        "Cantidades Lumbro ",
        "Cantidades Lumbro ",
    ]
    assert sheet.ranges["H4:P4"].copy_calls == [{}]
    assert sheet.ranges["H4:P28"].paste_calls == [
        {"Paste": builder.XL_PASTE_FORMATS}
    ]
    assert sheet.ranges["P4"].copy_calls == [{}]
    assert sheet.ranges["P4:P28"].paste_calls == [
        {"Paste": builder.XL_PASTE_FORMATS}
    ]


def test_open_workbook_does_not_retry_non_com_error_with_rejected_hresult() -> None:
    failure = _NonComFailure(-2147418111)
    workbooks = _SequencedWorkbooks([failure, object()])
    waits: list[float] = []

    with pytest.raises(_NonComFailure) as caught:
        builder._open_workbook_with_retry(
            workbooks,
            "book.xlsx",
            com_error_type=_ComFailure,
            max_attempts=3,
            sleep=waits.append,
        )

    assert caught.value is failure
    assert workbooks.calls == [("book.xlsx",)]
    assert waits == []


def test_open_workbook_succeeds_after_transient_com_rejections() -> None:
    opened = object()
    rejected = _ComFailure(-2147418111)
    workbooks = _SequencedWorkbooks([rejected, rejected, opened])
    waits: list[float] = []
    pumps: list[bool] = []

    result = builder._open_workbook_with_retry(
        workbooks,
        "book.xlsx",
        0,
        True,
        com_error_type=_ComFailure,
        max_attempts=3,
        sleep=waits.append,
        pump_waiting_messages=lambda: pumps.append(True),
    )

    assert result is opened
    assert workbooks.calls == [
        ("book.xlsx", 0, True),
        ("book.xlsx", 0, True),
        ("book.xlsx", 0, True),
    ]
    assert waits == [0.25, 0.25]
    assert pumps == [True, True]


def test_open_workbook_does_not_retry_other_hresult() -> None:
    failure = _ComFailure(-2147024891)
    workbooks = _SequencedWorkbooks([failure, object()])
    waits: list[float] = []

    with pytest.raises(_ComFailure) as caught:
        builder._open_workbook_with_retry(
            workbooks,
            "book.xlsx",
            com_error_type=_ComFailure,
            max_attempts=3,
            sleep=waits.append,
        )

    assert caught.value is failure
    assert workbooks.calls == [("book.xlsx",)]
    assert waits == []


def test_open_workbook_propagates_rejection_after_attempt_limit() -> None:
    failure = _ComFailure(-2147418111)
    workbooks = _SequencedWorkbooks([failure] * 5 + [object()])
    waits: list[float] = []
    pumps: list[bool] = []

    with pytest.raises(_ComFailure) as caught:
        builder._open_workbook_with_retry(
            workbooks,
            "book.xlsx",
            com_error_type=_ComFailure,
            sleep=waits.append,
            pump_waiting_messages=lambda: pumps.append(True),
        )

    assert caught.value is failure
    assert workbooks.calls == [("book.xlsx",)] * 5
    assert waits == [0.25] * 4
    assert pumps == [True] * 4


def test_open_workbook_preserves_com_error_when_message_pump_fails() -> None:
    failure = _ComFailure(-2147418111)
    pump_failure = RuntimeError("pump failed")
    workbooks = _SequencedWorkbooks([failure, object()])
    waits: list[float] = []

    def fail_pump() -> None:
        raise pump_failure

    with pytest.raises(_ComFailure) as caught:
        builder._open_workbook_with_retry(
            workbooks,
            "book.xlsx",
            com_error_type=_ComFailure,
            sleep=waits.append,
            pump_waiting_messages=fail_pump,
        )

    assert caught.value is failure
    assert caught.value.__cause__ is pump_failure
    assert workbooks.calls == [("book.xlsx",)]
    assert waits == []


def test_sanitize_workbook_xml_removes_only_private_alternate_content() -> None:
    private_block = (
        f'<mc:AlternateContent xmlns:mc="{MC_NAMESPACE}">'
        '<mc:Choice Requires="x15ac">'
        f'<x15ac:absPath xmlns:x15ac="{X15AC_NAMESPACE}" '
        'url="C:\\Users\\local-user\\AppData\\Local\\Temp\\"/>'
        "</mc:Choice></mc:AlternateContent>"
    ).encode()
    public_block = (
        f'<mc:AlternateContent xmlns:mc="{MC_NAMESPACE}">'
        '<mc:Choice Requires="feature"><feature xmlns="urn:keep"/></mc:Choice>'
        "</mc:AlternateContent>"
    ).encode()
    original = b'<workbook xmlns="urn:test">' + public_block + private_block + b'</workbook>'

    sanitized = builder._sanitize_workbook_xml(original)

    assert sanitized == original.replace(private_block, b"")
    assert public_block in sanitized
    assert b"absPath" not in sanitized
    assert b"c:\\users\\" not in sanitized.lower()


def test_clear_cotizacion_tail_preserves_sidecars_and_does_not_retry_effects() -> None:
    sheet = _TailSheet(first_used_row=3, used_row_count=182)
    retried_actions = 0

    def retry_com(action):
        nonlocal retried_actions
        retried_actions += 1
        return action()

    builder._clear_cotizacion_tail(sheet, retry_com)

    assert sheet.range_calls == ["A77:J184"]
    assert sheet.tail.unmerge_calls == 1
    assert sheet.tail.clear_calls == 1
    assert retried_actions == 4


def test_clear_cotizacion_residue_preserves_row_18_and_sidecars() -> None:
    residue = _SequencedRange()
    sheet = _RangeSheet({"A19:J27": residue})
    retried_actions = 0

    def retry_com(action):
        nonlocal retried_actions
        retried_actions += 1
        return action()

    builder._clear_cotizacion_residue(sheet, retry_com)

    assert sheet.range_calls == ["A19:J27"]
    assert residue.clear_calls == 1
    assert retried_actions == 1


def test_sanitize_workbook_xml_fails_closed_for_unscoped_private_path() -> None:
    original = (
        f'<workbook xmlns:x15ac="{X15AC_NAMESPACE}">'
        '<x15ac:absPath url="C:\\Users\\local-user\\file.xlsx"/>'
        "</workbook>"
    ).encode()

    with pytest.raises(ValueError, match="AlternateContent"):
        builder._sanitize_workbook_xml(original)


def test_sanitize_candidate_changes_only_workbook_and_preserves_external_links(
    tmp_path: Path,
) -> None:
    source = XlsxPackage.read(OFFICIAL)
    workbook_xml = source.parts[WORKBOOK_PART]
    insertion = workbook_xml.index(b">", workbook_xml.index(b"<workbook")) + 1
    private_block = (
        f'<mc:AlternateContent xmlns:mc="{MC_NAMESPACE}">'
        '<mc:Choice Requires="x15ac">'
        f'<x15ac:absPath xmlns:x15ac="{X15AC_NAMESPACE}" '
        'url="C:\\Users\\local-user\\AppData\\Local\\Temp\\"/>'
        "</mc:Choice></mc:AlternateContent>"
    ).encode()
    dirty_workbook_xml = (
        workbook_xml[:insertion] + private_block + workbook_xml[insertion:]
    )
    expected_workbook_xml = builder._sanitize_workbook_xml(dirty_workbook_xml)
    candidate = tmp_path / "candidate.xlsx"
    source.write_new(
        candidate,
        PackageMutation(replacements={WORKBOOK_PART: dirty_workbook_xml}),
    )
    before = XlsxPackage.read(candidate)
    protected_external_links = {
        name: content
        for name, content in before.parts.items()
        if name.startswith("xl/externalLinks/")
    }
    assert protected_external_links

    builder._sanitize_candidate_privacy(candidate)

    after = XlsxPackage.read(candidate)
    audit = assert_packages_preserved(before, after, {WORKBOOK_PART})
    assert audit.changed_parts == frozenset({WORKBOOK_PART})
    assert after.parts[WORKBOOK_PART] == expected_workbook_xml
    assert b"absPath" not in after.parts[WORKBOOK_PART]
    assert b"c:\\users\\" not in after.parts[WORKBOOK_PART].lower()
    assert {
        name: content
        for name, content in after.parts.items()
        if name.startswith("xl/externalLinks/")
    } == protected_external_links


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
    events: list[str] = []

    def fake_build(_official: Path, _visual: Path, candidate: Path) -> None:
        candidate.write_bytes(generated)
        events.append("build")

    def fake_sanitize(candidate: Path) -> None:
        assert candidate.read_bytes() == generated
        events.append("sanitize")

    def fake_contract_payload(candidate: Path) -> dict[str, object]:
        assert events == ["build", "sanitize"]
        events.append("contract")
        return _fake_payload(candidate)

    monkeypatch.setattr(builder, "_build_with_excel", fake_build)
    monkeypatch.setattr(builder, "_sanitize_candidate_privacy", fake_sanitize)
    monkeypatch.setattr(builder, "_contract_payload", fake_contract_payload)

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
    assert events == ["build", "sanitize", "contract"]
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
    monkeypatch.setattr(builder, "_sanitize_candidate_privacy", lambda _path: None)
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
