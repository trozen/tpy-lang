"""The conversion ratchet: no number `convert_gates.py` gates may grow past
its budget in `convert_gates.expected.json` (TODO.md "One conversion
boundary for every value sink"). The instrument is migration tooling that
leaves with the consolidation, so it stays a script and is loaded from its
file here."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest

_SCRIPT = (Path(__file__).resolve().parents[2] / "scripts" / "thir_migration"
           / "review" / "convert_gates.py")


def _convert_gates() -> ModuleType:
    spec = importlib.util.spec_from_file_location("convert_gates", _SCRIPT)
    assert spec is not None and spec.loader is not None, _SCRIPT
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_no_gated_count_grows() -> None:
    gates = _convert_gates()
    grown = gates.check()
    assert not grown, gates.format_growth(grown)


def test_unmeasured_names_fail(monkeypatch: pytest.MonkeyPatch,
                               tmp_path: Path) -> None:
    # A renamed entry, a moved gated file and a budget out of step with
    # the measured keys each fail the check instead of measuring 0.
    gates = _convert_gates()
    monkeypatch.setattr(gates, "RETURN_AUDIT_ENTRIES",
                        gates.RETURN_AUDIT_ENTRIES + ("_no_such_entry",))
    monkeypatch.setattr(gates, "FIELD_WRITE_FILE",
                        "tpyc/thir/lower/_no_such_file.py")
    with open(gates.EXPECTED) as fh:
        budgets = json.load(fh)
    budgets["_never_measured"] = 0
    del budgets["return_d"]
    expected = tmp_path / "expected.json"
    expected.write_text(json.dumps(budgets))
    monkeypatch.setattr(gates, "EXPECTED", str(expected))
    report = gates.format_growth(gates.check())
    for what in ("RETURN_AUDIT_ENTRIES: _no_such_entry resolves to no "
                 "function",
                 "gated file missing: tpyc/thir/lower/_no_such_file.py",
                 "budget key never measured: _never_measured",
                 "measured key with no budget: return_d"):
        assert "UNMEASURED " + what in report, report
