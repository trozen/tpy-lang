"""Tuple payload loans and replacement storage, without pinning C++ spelling."""

import pytest

from .parse import RebindStorage, TpyVarDecl
from .diagnostics import DiagnosticLevel
from .thir.testutil import _compile, _entry


PRELUDE = """\
from tpy import int32, readonly
class Cell:
    value: int32
    def __init__(self, value: int32):
        self.value = value
def borrowed(cell: Cell) -> tuple[Cell]:
    return (cell,)
def const_borrowed(cell: Cell) -> readonly[Cell]:
    return cell
def borrow_record(cell: Cell) -> Cell:
    return cell
"""


@pytest.mark.parametrize(("binding", "use"), [
    ("saved = (current,)\ncurrent.value = 4", "saved[0].value"),
    ("saved = (current,)\ncurrent.value = 4\nsaved = saved", "saved[0].value"),
    ("alias = const_borrowed(current)\nsaved = (alias,)\ncurrent.value = 4",
     "saved[0].value"),
    ("saved = (current.value,)", "saved[0]"),
    ("saved = (current,)", "saved[0].value"),
    ("saved = borrowed(current)\ncurrent.value = 4", "saved[0].value"),
    ("flag = current.value > 0\nsaved = (current,) if flag else (other,)\n"
     "current.value = 4\nother.value = 5", "saved[0].value"),
    ("flag = current.value > 0\nsaved = (other,) if flag else (current,)\n"
     "current.value = 4\nother.value = 5", "saved[0].value"),
    ("saved: tuple[Cell | None] = (borrow_record(current),)\ncurrent.value = 4",
     "saved[0] is None"),
    ("print((saved := (1, current))[0])\ncurrent.value = 4", "saved[1].value"),
    ("pair = (current,)\nsaved = pair\npair = (other,)\n"
     "other.value = 5\ncurrent.value = 4", "saved[0].value"),
    ("saved = (current,)\ncurrent.value = 4\nsaved = (other,)\n"
     "other.value = 5", "saved[0].value"),
    ("pair = (current,)\ncurrent.value = 4\n"
     "saved: tuple[Cell] | None = pair", "current.value"),
])
def test_replacement_takes_its_own_storage(binding: str, use: str) -> None:
    # Sema-only: nullable/readonly/conditional tuples have narrower THIR
    # coverage. Whatever a tuple capture still holds of the old object, the
    # rebind takes storage of its own (reuse is MIR's to prove).
    body = ("current = Cell(1)\nother = Cell(3)\n" + binding
            + "\ncurrent = Cell(2)\nprint(" + use + ")\n")
    source = PRELUDE + "def subject() -> None:\n" + "".join(
        "    " + line + "\n" for line in body.splitlines())
    _, modules = _compile(source)
    subject = next(f for f in _entry(modules).ast.functions
                   if f.name == "subject")
    replacements = [stmt for stmt in subject.body
                    if isinstance(stmt, TpyVarDecl) and stmt.name == "current"
                    and stmt.rebind_storage is not None]
    assert len(replacements) == 1
    assert replacements[0].rebind_storage is RebindStorage.OWN


@pytest.mark.parametrize(("write", "warns"), [
    ("holder.count = 2", True),
    ("holder.scalar = 2", False),
    ("holder.scalar += 2", False),
])
@pytest.mark.parametrize("scalar_type", ["int32", "int"])
def test_scalar_field_exemption_does_not_hide_setter_effects(
        write: str, warns: bool, scalar_type: str) -> None:
    # A property setter can invalidate storage even when its argument is scalar.
    source = PRELUDE + """\
class Holder:
    cells: list[Cell]
    scalar: int32
    def __init__(self):
        self.cells = [Cell(1)]
        self.scalar = 0
    @property
    def count(self) -> int32:
        return 1
    @count.setter
    def count(self, value: int32) -> None:
        self.cells = [Cell(value)]
def borrowed_holder(holder: Holder) -> Cell:
    return holder.cells[0]
def subject() -> None:
    holder = Holder()
    saved = borrowed_holder(holder)
""" + "    " + write + "\n    print(saved.value)\n"
    source = source.replace("scalar: int32", "scalar: " + scalar_type)
    _, modules = _compile(source)
    warnings = [d.message for d in _entry(modules).analyzer.diagnostics
                if d.level is DiagnosticLevel.WARNING]
    assert any("field assignment may invalidate references" in w
               for w in warnings) is warns
