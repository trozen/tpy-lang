"""The `error_` case annotation gate, pinned directly.

`test_case` runs it on every error case, but a case that passes proves only
the accepting side; the refusing side (a vacuous `# tpyc: ok`, a missing
`error` leg) has no case that can carry it, since such a case fails. The
`# tpyc: mir(...)` validator and the line-level MIR fact family
(`mir_owned(x)`, ...) are pinned here for the same reason: their failure
messages only show on a wrong annotation.
"""
from dataclasses import replace
from pathlib import Path

import pytest

from conftest import (
    error_case_annotation_problems, key_mir_verdicts, parse_mir_fact_annotations, validate_mir_annotations,
    validate_mir_fact_annotations,
)
from tpyc.mir.call_contract import MIRSummaryResult
from tpyc.mir.collect import (
    MIRBodyVerdict, MIRLineFacts, MIRLineReferents, MIRLineWrite, MIRStorageCheck, MIRStorageState,
    MIRVerdictStatus,
)
from tpyc.mir.nodes import MIRBodyId, MIRBodyKind, MIRValueKind


def _case(tmp_path: Path, source: str) -> Path:
    src = tmp_path / "src"
    src.mkdir()
    (src / "main.py").write_text(source)
    return src


def test_error_leg_alone_passes(tmp_path):
    src = _case(tmp_path, "x = 1  # tpyc: error(/nope/)\n")
    assert error_case_annotation_problems(src) == []


def test_ok_leg_is_refused_and_named(tmp_path):
    src = _case(tmp_path,
                "a = 1  # tpyc: ok\n"
                "b = 2  # tpyc: error(/nope/)\n"
                "c = 3  # tpyc: ok\n")
    problems = error_case_annotation_problems(src)
    assert len(problems) == 1
    assert "main.py:1" in problems[0] and "main.py:3" in problems[0]


def test_warning_leg_is_not_vacuous(tmp_path):
    src = _case(tmp_path,
                "a = 1  # tpyc: warning(/w/)\n"
                "b = 2  # tpyc: error(/nope/)\n")
    assert error_case_annotation_problems(src) == []


def test_missing_error_leg_is_refused(tmp_path):
    src = _case(tmp_path, "a = 1  # tpyc: warning(/w/)\n")
    problems = error_case_annotation_problems(src)
    assert len(problems) == 1
    assert "at least one" in problems[0]


def test_gate_walks_nested_source_packages(tmp_path):
    src = _case(tmp_path, "a = 1  # tpyc: error(/nope/)\n")
    pkg = src / "pkg"
    pkg.mkdir()
    (pkg / "helper.py").write_text("h = 1  # tpyc: ok\n")
    problems = error_case_annotation_problems(src)
    assert len(problems) == 1 and "helper.py:1" in problems[0]


def test_mir_leg_in_an_error_case_is_refused_and_named(tmp_path):
    src = _case(tmp_path,
                "def f() -> int:  # tpyc: mir(covered)\n"
                "    return 1\n"
                "def g() -> int:  # tpyc: mir_summary(known)\n"
                "    return 'x'  # tpyc: error(/nope/)\n")
    problems = error_case_annotation_problems(src)
    assert len(problems) == 1
    assert "mir(" in problems[0] and "main.py:1" in problems[0] and "main.py:3" in problems[0]


# --- `# tpyc: mir(...)` against hand-built verdicts ---------------------------

def _verdict(name: str, line: int, status: MIRVerdictStatus, *, reason: str | None = None,
             storage: MIRStorageState | None = None, conflicts: tuple[str, ...] = (),
             summary: MIRSummaryResult | None = None, declaration: str | None = None) -> MIRBodyVerdict:
    check = MIRStorageCheck(storage) if storage is not None else None
    return MIRBodyVerdict(MIRBodyId("main", declaration or f"{name}@{line}:0"), name, line,
                          MIRBodyKind.FREE_FUNCTION, status, reason, conflicts, False, check, None, None, None,
                          summary)


def _validate(tmp_path: Path, source: str, *verdicts: MIRBodyVerdict) -> list[str]:
    main = tmp_path / "main.py"
    main.write_text(source)
    return validate_mir_annotations(main, key_mir_verdicts(verdicts))


def test_key_mir_verdicts_keeps_every_body_of_a_def():
    pair = (_verdict("f", 1, MIRVerdictStatus.COVERED, declaration="Buffer.f@1:4"),
            _verdict("f", 1, MIRVerdictStatus.COVERED, declaration="Buffer.f@1:4#2"))
    other = _verdict("g", 5, MIRVerdictStatus.COVERED)
    init = _verdict("__tpy_init", None, MIRVerdictStatus.UNCOVERED, reason="module initialization",
                    declaration="__tpy_init")
    assert key_mir_verdicts([*pair, other, init]) == {(1, "f"): pair, (5, "g"): (other,)}


def test_mir_on_a_def_with_no_body_names_it(tmp_path):
    errors = _validate(tmp_path, "def f() -> int:  # tpyc: mir(covered)\n    return 1\n")
    assert errors == ["Line 1: no MIR body recorded for 'f'"]


def test_wrong_mir_verdict_names_the_actual_one(tmp_path):
    errors = _validate(tmp_path, "def f(s: str) -> str:  # tpyc: mir(covered)\n    return s\n",
                       _verdict("f", 1, MIRVerdictStatus.UNCOVERED, reason="view return"))
    assert errors == ["Line 1: expected mir(covered) for 'f' but MIR says uncovered (view return)"]


def test_covered_includes_certified(tmp_path):
    assert _validate(tmp_path, "def f() -> int:  # tpyc: mir(covered)\n    return 1\n",
                     _verdict("f", 1, MIRVerdictStatus.CERTIFIED, storage=MIRStorageState.CERTIFIED)) == []


def test_certified_refuses_a_body_with_nothing_to_prove(tmp_path):
    errors = _validate(tmp_path, "def f() -> int:  # tpyc: mir(certified)\n    return 1\n",
                       _verdict("f", 1, MIRVerdictStatus.COVERED, storage=MIRStorageState.NO_PROOF_REQUIRED))
    assert errors == ["Line 1: expected mir(certified) for 'f' but MIR says covered "
                      "(storage: no_proof_required)"]


def test_covered_refuses_a_conflict_and_conflict_matches_each_kind(tmp_path):
    source = ("def f() -> int:  # tpyc: mir(covered)\n    return 1\n"
              "def g() -> int:  # tpyc: mir(conflict /^replacement$/)\n    return 1\n")
    conflicted = ("scope_end", "replacement")
    state = MIRStorageState.CONFLICT
    errors = _validate(tmp_path, source,
                       _verdict("f", 1, MIRVerdictStatus.COVERED, storage=state, conflicts=conflicted),
                       _verdict("g", 3, MIRVerdictStatus.COVERED, storage=state, conflicts=conflicted))
    assert errors == ["Line 1: expected mir(covered) for 'f' but MIR says covered (storage: conflict), "
                      "conflicts: scope_end, replacement"]


def test_uncovered_and_incomplete_match_the_reason(tmp_path):
    source = ("def f() -> int:  # tpyc: mir(uncovered /generic/)\n    return 1\n"
              "def g() -> int:  # tpyc: mir(incomplete /^retention: /)\n    return 1\n")
    assert _validate(tmp_path, source,
                     _verdict("f", 1, MIRVerdictStatus.UNCOVERED, reason="generic body"),
                     _verdict("g", 3, MIRVerdictStatus.INCOMPLETE, reason="retention: no facts",
                              storage=MIRStorageState.NOT_COVERED)) == []
    errors = _validate(tmp_path, source,
                       _verdict("f", 1, MIRVerdictStatus.UNCOVERED, reason="resumable body"),
                       _verdict("g", 3, MIRVerdictStatus.INCOMPLETE, reason="storage: no facts",
                                storage=MIRStorageState.NOT_COVERED))
    assert errors == [
        "Line 1: expected mir(uncovered /generic/) for 'f' but MIR says uncovered (resumable body)",
        "Line 3: expected mir(incomplete /^retention: /) for 'g' but MIR says incomplete (storage: no facts)"]


def test_mir_summary_reads_the_workspace_state(tmp_path):
    source = ("def f() -> int:  # tpyc: mir_summary(opaque /recursive/)\n    return 1\n"
              "def g() -> int:  # tpyc: mir_summary(known)\n    return 1\n")
    opaque = MIRSummaryResult.opaque("recursive or recursion-dependent call")
    assert _validate(tmp_path, source,
                     _verdict("f", 1, MIRVerdictStatus.UNCOVERED, reason="x", summary=opaque),
                     _verdict("g", 3, MIRVerdictStatus.UNCOVERED, reason="x", summary=opaque)) == [
        "Line 3: expected mir_summary(known) for 'g' but MIR summary is opaque "
        "(recursive or recursion-dependent call)"]
    assert _validate(tmp_path, source,
                     _verdict("f", 1, MIRVerdictStatus.UNCOVERED, reason="x")) == [
        "Line 1: expected mir_summary(opaque /recursive/) for 'f' but no summary recorded",
        "Line 3: no MIR body recorded for 'g'"]


def test_mir_spelling_mistakes_are_named(tmp_path):
    source = ("def f() -> int:  # tpyc: mir(lowered)\n    return 1\n"
              "def g() -> int:  # tpyc: mir(covered /x/)\n    return 1\n")
    errors = _validate(tmp_path, source)
    assert len(errors) == 2
    assert "unknown mir(lowered)" in errors[0] and "takes no /regex/" in errors[1]


def test_mir_off_a_def_line_is_named(tmp_path):
    source = "def f() -> int:\n    return 1  # tpyc: mir(covered)\n"
    assert _validate(tmp_path, source, _verdict("f", 1, MIRVerdictStatus.COVERED)) == [
        "Line 2: could not extract function name from line"]


def test_mir_holds_for_every_body_a_def_emits(tmp_path):
    # An @auto_readonly method emits a clone pair under one def line; an
    # annotation checking one arbitrary clone would leave the other unpinned.
    source = "def f() -> int:  # tpyc: mir(covered)\n    return 1\n"
    first = _verdict("f", 1, MIRVerdictStatus.COVERED, storage=MIRStorageState.NO_PROOF_REQUIRED,
                     declaration="Buffer.f@1:4")
    second = _verdict("f", 1, MIRVerdictStatus.UNCOVERED, reason="view return", declaration="Buffer.f@1:4#2")
    assert _validate(tmp_path, source, first) == []
    assert _validate(tmp_path, source, first, second) == [
        "Line 1: expected mir(covered) for 'f' (Buffer.f@1:4#2) but MIR says uncovered (view return)"]


# --- the line-level MIR fact family against hand-built facts ---------------------

def _facts(declaration: str, *, names: dict[str, dict[str, str]],
           writes: dict[tuple[int, str], tuple[MIRLineWrite, ...]] | None = None,
           events: dict[tuple[int, str], tuple[str, ...]] | None = None,
           borrows: dict[tuple[int, str], tuple[MIRLineReferents, ...]] | None = None,
           holders: frozenset[str] = frozenset(), lines: frozenset[int] = frozenset({2, 3}),
           borrows_gap: str | None = None) -> MIRLineFacts:
    return MIRLineFacts(MIRBodyId("main", declaration), names, holders, lines, writes or {}, events or {},
                        borrows or {}, borrows_gap=borrows_gap)


def _validate_facts(tmp_path: Path, source: str,
                    *bodies: tuple[MIRBodyVerdict, MIRLineFacts | None]) -> list[str]:
    main = tmp_path / "main.py"
    main.write_text(source)
    return validate_mir_fact_annotations(main, key_mir_verdicts([v for v, _ in bodies]),
                                         {v.body: facts for v, facts in bodies})


def _lowered(name: str = "f", line: int = 1, declaration: str | None = None) -> MIRBodyVerdict:
    return _verdict(name, line, MIRVerdictStatus.COVERED, storage=MIRStorageState.NO_PROOF_REQUIRED,
                    declaration=declaration)


_OWNED = MIRLineWrite(0, MIRValueKind.OWNED, False)
_COPIED = MIRLineWrite(0, MIRValueKind.OWNED, True)
_BORROWED = MIRLineWrite(0, MIRValueKind.BORROWED, False)


def test_mir_fact_items_parse_every_spelling():
    source = ("def f(s: str) -> None:\n"
              "    t = s  # tpyc: mir_owned(t) mir_borrowed(self.name) mir_copy(local(s)) mir_write(param(s).x)\n"
              "    q = p  # tpyc: ok mir_borrows(q, p|r.name|static|G|local(s)|r@13) mir_borrows( q , t[0] )\n"
              "    # tpyc: mir_owned(ignored)\n"
              "    return  # tpyc: mir_summary(known) mir(covered)\n")
    parsed = [(a.line, a.family, a.name, a.referents, a.problem) for a in parse_mir_fact_annotations(source)]
    assert parsed == [
        (2, "mir_owned", "t", (), None),
        (2, "mir_borrowed", "self.name", (), None),
        (2, "mir_copy", "local(s)", (), None),
        (2, "mir_write", "param(s).x", (), None),
        (3, "mir_borrows", "q", ("p", "r.name", "static", "G", "local(s)", "r@13"), None),
        (3, "mir_borrows", "q", ("t[0]",), None)]


def test_mir_fact_misspellings_are_refused_with_the_family(tmp_path):
    source = ("def f() -> None:\n"
              "    x = 1  # tpyc: mir_own(x) mir_owned(x, y) mir_borrows(x) mir_borrows(x, /p/) mir_owned(x y)\n")
    errors = _validate_facts(tmp_path, source, (_lowered(), _facts("f@1:0", names={"x": {"local": "x"}})))
    assert errors == [
        "Line 2: mir_own(x): unknown mir_own(...); expected one of "
        "mir_borrowed, mir_borrows, mir_copy, mir_owned, mir_write",
        "Line 2: mir_owned(x, y): mir_owned takes (name)",
        "Line 2: mir_borrows(x): mir_borrows takes (holder, referent|referent...)",
        "Line 2: mir_borrows(x, /p/): mir_borrows takes no /regex/: MIR facts are exact",
        "Line 2: mir_owned(x y): 'x y' is no name, param(name), local(name) or a field path of one"]


def test_mir_fact_in_an_error_case_is_refused(tmp_path):
    src = _case(tmp_path,
                "def f() -> int:\n"
                "    x = 1  # tpyc: mir_owned(x)\n"
                "    return 'x'  # tpyc: error(/nope/)\n")
    problems = error_case_annotation_problems(src)
    assert len(problems) == 1 and "mir_<fact>(" in problems[0] and "main.py:2" in problems[0]


def test_write_facts_check_kind_and_copy(tmp_path):
    source = ("def f(a: str) -> None:\n"
              "    v = a  # tpyc: mir_owned(v) mir_copy(v) mir_borrowed(v)\n"
              "    w = a  # tpyc: mir_copy(w) mir_owned(u)\n")
    facts = _facts("f@1:0", names={"a": {"param": "a"}, "v": {"local": "v"}, "w": {"local": "w"},
                                   "u": {"local": "u"}},
                   writes={(2, "v"): (_COPIED,), (3, "w"): (_OWNED,)})
    assert _validate_facts(tmp_path, source, (_lowered(), facts)) == [
        "Line 2: expected mir_borrowed(v) in 'f' but MIR writes v as owned",
        "Line 3: expected mir_copy(w) in 'f' but no write of w on line 3 is a copy",
        "Line 3: expected mir_owned(u) in 'f' but no MIR statement on line 3 writes u"]


def test_a_line_without_statements_and_an_unknown_name_are_named(tmp_path):
    source = ("def f() -> None:\n"
              "    pass\n"
              "    pass  # tpyc: mir_owned(x)\n"
              "    pass  # tpyc: mir_owned(y)\n")
    facts = _facts("f@1:0", names={"x": {"local": "x"}}, lines=frozenset({2}))
    assert _validate_facts(tmp_path, source, (_lowered(), facts)) == [
        "Line 3: expected mir_owned(x) in 'f' but no MIR statement on line 3",
        "Line 4: expected mir_owned(y) in 'f' but no slot named 'y'"]


def test_a_name_with_two_slots_needs_a_selector(tmp_path):
    # A reassigned `str` parameter owns a LOCAL slot beside its PARAMETER one.
    source = ("def f(s: str) -> None:\n"
              "    s = s + '!'  # tpyc: mir_owned(s) mir_owned(local(s)) mir_borrows(param(s), s)\n"
              "    pass  # tpyc: mir_owned(param(t)) mir_borrows(param(s), param(s))\n")
    names = {"s": {"param": "param(s)", "local": "local(s)"}, "t": {"local": "t"}}
    facts = _facts("f@1:0", names=names, holders=frozenset({"param(s)"}),
                   writes={(2, "local(s)"): (_OWNED,)},
                   borrows={(line, "param(s)"): (MIRLineReferents(0, frozenset({"param(s)"})),)
                            for line in (2, 3)})
    assert _validate_facts(tmp_path, source, (_lowered(), facts)) == [
        "Line 2: expected mir_owned(s) in 'f' but 's' names local(s) and param(s) "
        "(ambiguous: use local(s) or param(s))",
        "Line 2: expected mir_borrows(param(s), s) in 'f' but 's' names local(s) and param(s) "
        "(ambiguous: use local(s) or param(s))",
        "Line 3: expected mir_owned(param(t)) in 'f' but no parameter named 't'"]


def test_borrows_compare_sets_in_any_order_in_every_block(tmp_path):
    source = ("def f(p: P, o: P) -> None:\n"
              "    q = p  # tpyc: mir_borrows(q, o|p) mir_borrows(q, p|o) mir_borrows(q, p)\n"
              "    v = 1  # tpyc: mir_borrows(v, p) mir_borrows(q, static)\n"
              "    r = P(2)  # tpyc: mir_borrows(q, r@2) mir_borrows(q, r)\n")
    names = {"p": {"param": "p"}, "o": {"param": "o"}, "q": {"local": "q"}, "v": {"local": "v"},
             "r": {"local": "r"}}
    facts = _facts("f@1:0", names=names, holders=frozenset({"p", "o", "q"}), lines=frozenset({2, 3, 4}), borrows={
        (2, "q"): (MIRLineReferents(0, frozenset({"p", "o"})), MIRLineReferents(3, frozenset({"p", "o"}))),
        (3, "q"): (MIRLineReferents(1, frozenset({"static"})), MIRLineReferents(2, frozenset({"p"}))),
        (4, "q"): (MIRLineReferents(2, frozenset({"r@2"})),)})
    assert _validate_facts(tmp_path, source, (_lowered(), facts)) == [
        "Line 2: expected mir_borrows(q, p) in 'f' but q borrows {o|p} after line 2 in bb0; "
        "q borrows {o|p} after line 2 in bb3",
        "Line 3: expected mir_borrows(v, p) in 'f' but v holds no loan",
        "Line 3: expected mir_borrows(q, static) in 'f' but q borrows {p} after line 3 in bb2",
        # A name rebound to fresh storage spells each storage with its binding line.
        "Line 4: expected mir_borrows(q, r) in 'f' but q borrows {r@2} after line 4 in bb2"]


def test_write_event_names_the_replaced_storage(tmp_path):
    source = ("def f() -> None:\n"
              "    t += 'y'  # tpyc: mir_write(t) mir_write(u)\n"
              "    pass  # tpyc: mir_write(t)\n")
    names = {"t": {"local": "t"}, "u": {"local": "u"}}
    facts = _facts("f@1:0", names=names, events={(2, "t"): ("in_place",)})
    assert _validate_facts(tmp_path, source, (_lowered(), facts)) == [
        "Line 2: expected mir_write(u) in 'f' but no write event on line 2 replaces u (events replace t)",
        "Line 3: expected mir_write(t) in 'f' but no write event on line 3 replaces t"]


def test_an_analysis_gap_and_an_unlowered_body_report_the_reason(tmp_path):
    source = ("def f() -> None:\n"
              "    q = p  # tpyc: mir_borrows(q, p)\n"
              "def g() -> None:\n"
              "    x = 1  # tpyc: mir_owned(x)\n"
              "y = 1  # tpyc: mir_owned(y)\n")
    gap = _facts("f@1:0", names={"q": {"local": "q"}, "p": {"param": "p"}},
                 borrows_gap="recursive inline field paths")
    unlowered = _verdict("g", 3, MIRVerdictStatus.UNCOVERED, reason="resumable body")
    assert _validate_facts(tmp_path, source, (_lowered(), gap), (unlowered, None)) == [
        "Line 2: expected mir_borrows(q, p) in 'f' but dependencies not covered: recursive inline field paths",
        "Line 4: expected mir_owned(x) in 'g' but body not lowered: resumable body",
        "Line 5: mir_owned(y) is not inside a def"]


def test_a_fact_holds_for_every_body_a_def_emits(tmp_path):
    # An @auto_readonly clone pair shares the def line and its statement lines.
    source = ("class Buffer:\n"
              "    def f(self) -> None:\n"
              "        v = self.name  # tpyc: mir_owned(v)\n")
    names = {"self": {"param": "self"}, "v": {"local": "v"}}
    first = _lowered("f", 2, "Buffer.f@2:4")
    second = _lowered("f", 2, "Buffer.f@2:4#2")
    owned = _facts("Buffer.f@2:4", names=names, writes={(3, "v"): (_OWNED,)}, lines=frozenset({3}))
    borrowed = replace(owned, body=MIRBodyId("main", "Buffer.f@2:4#2"), writes={(3, "v"): (_BORROWED,)})
    assert _validate_facts(tmp_path, source, (first, owned)) == []
    assert _validate_facts(tmp_path, source, (first, owned), (second, borrowed)) == [
        "Line 3: expected mir_owned(v) in 'f' (Buffer.f@2:4#2) but MIR writes v as borrowed"]


class _Request:
    """The slice of pytest's `request` the comp phase reads, with the
    finalizers captured instead of registered on THIS test."""

    def __init__(self, request):
        self.config = request.config
        self.node = request.node
        self.finalizers = []

    def addfinalizer(self, fn):
        self.finalizers.append(fn)


def test_update_mode_validates_after_the_snapshots_land(tmp_path, monkeypatch,
                                                         request):
    """An update run writes diag.txt, finishes, and THEN fails on a wrong
    annotation.

    The one update run that skipped validation regenerated nineteen
    cases around wrong annotations and left the next plain run to find
    them; failing mid-phase instead would leave the wiped tree behind.
    Drives the real comp phase on a synthetic error case, so a
    reintroduced update-mode exemption fails here. The interop harness
    carries the same call site by hand; it has no toolchain-free probe.
    """
    import conftest
    import test_case as harness

    case_dir = tmp_path / "error_probe"
    src = case_dir / "src"
    src.mkdir(parents=True)
    main = src / "main.py"
    main.write_text("from tpy import int32\n\n"
                    "too_big: int32 = 3000000000  # tpyc: error(/no such message/)\n")
    monkeypatch.setattr(conftest, "UPDATE_EXPECTED", True)
    monkeypatch.setattr(harness, "UPDATE_EXPECTED", True)
    # The options.json walk stops at the cases root; a temp case needs one.
    monkeypatch.setattr(conftest, "CASES_DIR", tmp_path)
    req = _Request(request)
    harness.test_case(case_dir, main, req)
    diag = case_dir / "expected" / "diag.txt"
    assert diag.exists() and "outside int32 range" in diag.read_text()
    assert len(req.finalizers) == 1
    with pytest.raises(pytest.fail.Exception) as info:
        req.finalizers[0]()
    assert "main.py:3" in str(info.value)


def test_error_case_gate_runs_before_the_update_wipe(tmp_path, monkeypatch,
                                                      request):
    import conftest
    import test_case as harness

    case_dir = tmp_path / "error_probe"
    src = case_dir / "src"
    src.mkdir(parents=True)
    main = src / "main.py"
    main.write_text("from tpy import int32\n\n"
                    "n: int32 = 1  # tpyc: ok\n"
                    "too_big: int32 = 3000000000  # tpyc: error(/int32 range/)\n")
    diag = case_dir / "expected" / "diag.txt"
    diag.parent.mkdir()
    diag.write_text("stale\n")
    monkeypatch.setattr(conftest, "UPDATE_EXPECTED", True)
    monkeypatch.setattr(harness, "UPDATE_EXPECTED", True)
    monkeypatch.setattr(conftest, "CASES_DIR", tmp_path)
    with pytest.raises(pytest.fail.Exception) as info:
        harness.test_case(case_dir, main, _Request(request))
    assert "main.py:3" in str(info.value)
    # The refused case keeps its snapshot: the gate fired before the wipe.
    assert diag.read_text() == "stale\n"


def test_error_case_that_compiles_fails_at_once_in_update_mode(tmp_path,
                                                                monkeypatch,
                                                                request):
    """An error case whose rejection stopped firing must not run on into
    the code snapshots and exec: it fails right after diag.txt, in update
    mode as in a plain run."""
    import conftest
    import test_case as harness

    case_dir = tmp_path / "error_probe"
    src = case_dir / "src"
    src.mkdir(parents=True)
    main = src / "main.py"
    main.write_text("from tpy import int32\n\n"
                    "n: int32 = 1  # tpyc: error(/never fires/)\n")
    monkeypatch.setattr(conftest, "UPDATE_EXPECTED", True)
    monkeypatch.setattr(harness, "UPDATE_EXPECTED", True)
    monkeypatch.setattr(conftest, "CASES_DIR", tmp_path)
    with pytest.raises(pytest.fail.Exception) as info:
        harness.test_case(case_dir, main, _Request(request))
    assert "compiled successfully" in str(info.value)
    assert (case_dir / "expected" / "diag.txt").exists()
    assert not (case_dir / "expected" / "src").exists()
