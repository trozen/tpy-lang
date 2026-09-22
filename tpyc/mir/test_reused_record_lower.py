"""Production copy writes retain the backing chosen for live and dead aliases."""

import pytest

from ..thir.testutil import _compile, _entry
from .definitions import MIRDefinitions
from .lower import lower_function
from .nodes import MIRBodyId, MIRBodyKind, MIRCopy, MIRFunction, MIRRecordWriteMode
from .scope_lifetime import inspect_scope_lifetimes
from .storage import analyze_storage
from .test_retention import analyze
from .testutil import Reference, execute


CELL_SOURCE = '''from tpy import int32, copy
class Cell:
    value: int32
    def __init__(self, value: int32):
        self.value = value
'''


@pytest.mark.parametrize("optional", [False, True])
@pytest.mark.parametrize("retained", [False, True])
def test_existing_replacement_facts_admit_source_copies(optional: bool, retained: bool) -> None:
    annotation = ": Cell | None" if optional else ""
    save = f"    saved{annotation} = current\n" if retained else ""
    read = ("    if saved is not None:\n        saved.value = 5\n        return current.value\n"
            if retained and optional else "    saved.value = 5\n    return current.value\n"
            if retained else "    return current.value\n")
    if optional:
        read = "    if current is None:\n        return 0\n" + read
    source = CELL_SOURCE + f'''
def replace_copy(source: Cell, n: int32) -> int32:
    current{annotation} = Cell(1)
{save}    for i in range(n):
        current = copy(source)
        current.value = 9
{read}    return 0
'''
    compiler, modules = _compile(source)
    (_, cpp), ctx = compiler.generate_code_and_thir(_entry(modules))
    body = next(fn for node, fn in ctx.thir_functions.items() if node.name == "replace_copy")
    fn = lower_function(body, MIRBodyId("reused", "copy"), kind=MIRBodyKind.FREE_FUNCTION,
                        definitions=MIRDefinitions(tuple(ctx.thir_constructors.values())))
    assert isinstance(fn, MIRFunction), fn
    write, = (s for s in analyze_storage(fn).writes.values() if isinstance(s.value, MIRCopy))
    assert write.storage_write.mode is (MIRRecordWriteMode.OWN_SITE if retained else MIRRecordWriteMode.IN_PLACE)
    assert ("current = &*(__slot_2 = Cell(source));" if retained else "(*current) = Cell(source);") in cpp
    field = fn.records[0].fields[0].id
    for n in (0, 3):
        heap = {0: {field: 3}}
        assert execute(fn, Reference(0), n, heap=heap) == (9 if n else 5 if retained else 1)
        assert heap[0][field] == 3
        assert len(heap) == (3 if retained and n else 2)
    assert analyze(fn).conflicts == ()
    assert inspect_scope_lifetimes(fn).conflicts == ()


def test_escape_hoisted_copy_uses_one_body_backing_across_iterations() -> None:
    source = CELL_SOURCE + '''
def escaped(source: Cell, n: int32) -> int32:
    holder = Cell(0)
    for i in range(n):
        duplicate = copy(source)
        holder = duplicate
    holder.value = 7
    return source.value
'''
    compiler, modules = _compile(source)
    (_, cpp), ctx = compiler.generate_code_and_thir(_entry(modules))
    body = next(fn for node, fn in ctx.thir_functions.items() if node.name == "escaped")
    fn = lower_function(body, MIRBodyId("reused", "escaped"), kind=MIRBodyKind.FREE_FUNCTION,
                        definitions=MIRDefinitions(tuple(ctx.thir_constructors.values())))
    assert isinstance(fn, MIRFunction), fn
    transfer, = (s for s in analyze_storage(fn).writes.values() if isinstance(s.value, MIRCopy))
    assert transfer.storage_write.mode is MIRRecordWriteMode.OWN_SITE
    assert "Cell* duplicate = &*(__slot_2 = Cell(source));" in cpp
    field = fn.records[0].fields[0].id
    for n in (0, 3):
        heap = {0: {field: 3}}
        assert execute(fn, Reference(0), n, heap=heap) == 3
        assert len(heap) == (3 if n else 2)
    assert inspect_scope_lifetimes(fn).conflicts == ()
