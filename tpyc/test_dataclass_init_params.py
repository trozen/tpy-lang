"""The parameter types of the `__init__` that `build_init` generates."""

from . import get_lib_dir
from .compiler import Compiler
from .sema.builder_trace import _build_default_init
from .typesys import INT32, STR, NominalType, OptionalType, TupleType, make_list

_STDLIB_DIRS = [get_lib_dir() / "tpy"]

# @model is checked here rather than in a snapshot case: its generated
# __json_decode__ does not lower a tuple field of records yet
# (BUGS.md#model-tuple-record-field-decode-rejects).
_SOURCE = (
    "from dataclasses import dataclass\n"
    "from typing import Callable\n"
    "from tpy import Ptr, int32\n"
    "from tplib.json.model import model\n"
    "\n"
    "\n"
    "@model\n"
    "class P:\n"
    "    n: int32\n"
    "\n"
    "\n"
    "@dataclass\n"
    "class Fields:\n"
    "    rec: P\n"
    "    pair: tuple[P, int32]\n"
    "    nested: tuple[tuple[P, int32], int32]\n"
    "    values: tuple[int32, str]\n"
    "    n: int32\n"
    "    f: Callable[[P], int32]\n"
    "\n"
    "    def __eq__(self, other: 'Fields') -> bool:\n"
    "        return True\n"
    "\n"
    "    def __repr__(self) -> str:\n"
    "        return 'Fields'\n"
    "\n"
    "\n"
    "@dataclass\n"
    "class Handle:\n"
    "    p: Ptr[P]\n"
    "\n"
    "    def __eq__(self, other: 'Handle') -> bool:\n"
    "        return True\n"
    "\n"
    "    def __repr__(self) -> str:\n"
    "        return 'Handle'\n"
    "\n"
    "\n"
    "@dataclass\n"
    "class Q:\n"
    "    n: int32\n"
    "\n"
    "\n"
    "@dataclass\n"
    "class Leaves:\n"
    "    opt: tuple[P | None, int32]\n"
    "    uni: tuple[P | Q, int32]\n"
    "\n"
    "    def __eq__(self, other: 'Leaves') -> bool:\n"
    "        return True\n"
    "\n"
    "    def __repr__(self) -> str:\n"
    "        return 'Leaves'\n"
    "\n"
    "\n"
    "@dataclass\n"
    "class G[T]:\n"
    "    pair: tuple[T, int32]\n"
    "\n"
    "    def __eq__(self, other: 'G[T]') -> bool:\n"
    "        return True\n"
    "\n"
    "    def __repr__(self) -> str:\n"
    "        return 'G'\n"
    "\n"
    "\n"
    "@dataclass\n"
    "class Base:\n"
    "    values: tuple[int32, str]\n"
    "\n"
    "\n"
    "@dataclass\n"
    "class Child(Base):\n"
    "    m: int32\n"
    "\n"
    "\n"
    "@model\n"
    "class M:\n"
    "    pair: tuple[P, int32]\n"
    "\n"
    "\n"
    "def main() -> None:\n"
    "    pass\n"
    "\n"
    "\n"
    "main()\n"
)


def _init_params(record_name: str) -> dict[str, str]:
    compiler = Compiler.from_source(_SOURCE, lib_dirs=_STDLIB_DIRS)
    modules = {m.name: m for m in compiler.compile()}
    (record,) = (r for r in modules["main"].ast.records if r.name == record_name)
    (init,) = (f for f in record.methods if f.name == "__init__")
    return {name: str(t) for name, t in init.params}


def test_reference_anywhere_takes_own_values_stay_plain():
    assert _init_params("Fields") == {
        "rec": "Own[P]",
        "pair": "Own[tuple[P, int32]]",
        "nested": "Own[tuple[tuple[P, int32], int32]]",
        "values": "tuple[int32, str]",
        "n": "int32",
        "f": "Callable[[P], int32]",
    }


def test_ptr_field_stays_a_plain_parameter():
    assert _init_params("Handle") == {"p": "Ptr[P]"}


def test_optional_and_union_leaves_take_own():
    assert _init_params("Leaves") == {
        "opt": "Own[tuple[P | None, int32]]",
        "uni": "Own[tuple[P | Q, int32]]",
    }


def test_open_type_parameter_leaf_takes_own():
    assert _init_params("G") == {"pair": "Own[tuple[T, int32]]"}


def test_value_only_parent_field_stays_plain():
    assert _init_params("Child") == {
        "values": "tuple[int32, str]", "m": "int32"}


def test_builder_trace_init_shares_the_rule():
    point = NominalType("Point")
    init = _build_default_init([
        ("pair", TupleType((point, INT32))),
        ("values", TupleType((INT32, STR))),
        ("rec", point),
        ("maybe", OptionalType(point)),
        ("items", make_list(INT32)),
        ("n", INT32),
    ], None)
    assert {name: str(t) for name, t in init.params} == {
        "pair": "Own[tuple[Point, int32]]",
        "values": "tuple[int32, str]",
        "rec": "Own[Point]",
        "maybe": "Own[Point | None]",
        "items": "Own[list[int32]]",
        "n": "int32",
    }


def test_model_shares_the_rule():
    assert _init_params("M") == {"pair": "Own[tuple[P, int32]]"}
