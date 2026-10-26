"""A record's operator dunder is committed as its explicit call is
(`CallAnalyzer.commit_facts`): the mutation it makes of its
receiver and of its operand reaches the caller's parameters. Pinned on the
facts, since the program does not build yet: the operator's C++ wrapper is
still const (BUGS.md#mutating-dunder-const-wrapper), and a record's slice
store does not lower."""

from tpyc import get_lib_dir
from tpyc.compiler import Compiler
from tpyc.parse.nodes import TpyIntLiteral, TpyName
from tpyc.sema.arg_binding import ArgBinding, CallSource, bind
from tpyc.typesys import INT32, VOID, FunctionInfo, ParamInfo
from tpyc.value_category import CallOperands

_STDLIB_DIRS = [get_lib_dir() / "tpy"]

_SOURCE = """
from tpy import int32, readonly, basic_slice


class B:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


class A:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    @readonly(False)
    def __add__(self, o: int32) -> int32:
        self.n += o
        return self.n

    @readonly(False)
    def __sub__(self, o: B) -> int32:
        o.n += 1
        return self.n - o.n

    def __iadd__(self, o: B) -> "A":
        o.n += 10
        return self


def use_receiver(a: A) -> int32:
    return a + 1


def use_explicit(a: A) -> int32:
    return a.__add__(1)


def use_operand(a: A, b: B) -> int32:
    return a - b


class S:
    def __init__(self) -> None:
        pass

    def __setitem__(self, k: basic_slice, v: B) -> None:
        v.n += 1


def use_slice_store(s: S, b: B) -> None:
    s[:] = b


def use_inplace(b: B) -> None:
    a = A(1)
    a += b
"""


def _functions() -> 'dict[str, FunctionInfo]':
    compiler = Compiler.from_source(_SOURCE, lib_dirs=_STDLIB_DIRS)
    entry = next(m for m in compiler.compile() if m.is_entry_point)
    return {name: fis[0] for name, fis in entry.exports.functions.items()}


def test_operator_mutation_reaches_the_receiver_and_the_operand() -> None:
    fns = _functions()
    # The receiver as the explicit `a.__add__(1)` marks it.
    assert 0 in fns["use_explicit"].mutated_params
    assert 0 in fns["use_receiver"].mutated_params
    # The right operand the dunder writes.
    assert 1 in fns["use_operand"].mutated_params
    # The value of a slice store, beside the slice's own place.
    assert 1 in fns["use_slice_store"].mutated_params
    # The operand of an in-place operator.
    assert 0 in fns["use_inplace"].mutated_params


def test_an_omitted_default_is_no_operand_entry() -> None:
    """A call not laid out yet passes each parameter what it was written
    with (`ArgBinding.operands`): a default left before a supplied keyword
    passes nothing, and the operand view keeps the others' indices."""
    params = [ParamInfo("a", INT32),
              ParamInfo("k", INT32, default_expr=TpyIntLiteral(0)),
              ParamInfo("xs", INT32, keyword_only=True)]
    a, xs = TpyName("a"), TpyName("xs")
    src = CallSource((a,), (("xs", xs),))
    binding = bind(src, params, "f")
    assert isinstance(binding, ArgBinding)
    operands = binding.operands(src=src)
    assert operands == [a, None, xs]
    fi = FunctionInfo(name="f", params=params, return_type=VOID)
    assert CallOperands(fi, None, operands).positioned() == [(0, a), (2, xs)]
