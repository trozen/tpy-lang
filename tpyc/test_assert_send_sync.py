"""Sema behavior of assert_send[T]() / assert_sync[T]() (tpyc/sema/calls.py).

Covers the misuse-rejection paths and the chain-walker recursion guard
(a self-referential non-Send record must not stack-overflow the diagnostic
walk)."""
import pytest

from . import get_lib_dir
from .compiler import Compiler
from .diagnostics import SemanticError

_STDLIB_DIRS = [get_lib_dir() / "tpy"]
_PRELUDE = "from tpy import int32, Ptr, assert_send, assert_sync, nosend\n"


def _compile(body):
    Compiler.from_source(_PRELUDE + body, lib_dirs=_STDLIB_DIRS).compile()


def test_zero_type_args_rejected():
    with pytest.raises(SemanticError, match="requires exactly one type argument"):
        _compile("def main() -> None:\n    assert_send()\nmain()\n")


def test_value_argument_rejected():
    with pytest.raises(SemanticError, match="takes no value arguments"):
        _compile("def main() -> None:\n    assert_send[int32](5)\nmain()\n")


def test_failing_assert_reports_chain():
    with pytest.raises(SemanticError, match=r"assert_send assertion failed.*raw pointer"):
        _compile("def main() -> None:\n    assert_send[Ptr[int32]]()\nmain()\n")


def test_passing_assert_is_accepted():
    _compile("def main() -> None:\n    assert_send[int32]()\n    assert_sync[int32]()\nmain()\n")


def test_nosend_record_reports_marked_reason():
    # A @nosend record is a leaf: the chain must name the decorator, not fall
    # through to the generic "not Send" reason.
    src = (
        "@nosend\n"
        "class Arena:\n"
        "    cap: int32\n"
        "    def __init__(self) -> None:\n"
        "        self.cap = 0\n"
        "def main() -> None:\n"
        "    assert_send[Arena]()\n"
        "main()\n"
    )
    with pytest.raises(SemanticError, match=r"Arena is not Send \(marked @nosend\)"):
        _compile(src)


def test_self_referential_record_terminates():
    # The walker must mirror the oracle's cycle guard: a non-Send record that
    # references itself through a container field would otherwise recurse
    # forever. Expect a clean diagnostic, not a stack overflow.
    src = (
        "class Node:\n"
        "    nxt: list[Node]\n"
        "    raw: Ptr[int32]\n"
        "    def __init__(self, raw: Ptr[int32]) -> None:\n"
        "        self.raw = raw\n"
        "        self.nxt = []\n"
        "def main() -> None:\n"
        "    assert_send[Node]()\n"
        "main()\n"
    )
    with pytest.raises(SemanticError, match="assert_send assertion failed"):
        _compile(src)
