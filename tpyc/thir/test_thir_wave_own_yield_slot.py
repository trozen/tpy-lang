"""An `Own[T]` generator yield slot over a bare type param.

The ownership fact lives in the consumer's loop-var binding, so the slot
renders exactly like the bare `T` it wraps -- the same reason the container
and record slot checks already peel Own. A wrapped family the bare slot does
not admit (an owned view) keeps rejecting.
"""

from .testutil import (
    _reject_tally,
    _assert_rejects_at,
    _assert_routes_byte_identical,
    _compile,
    _entry,
)
from ..codegen_cpp.context import CodeGenOptions

_SRC = (
    "from typing import Iterator\n"
    "from tpy import Own\n"
    "class Empty(Exception):\n"
    "    pass\n"
    "class Src[T]:\n"
    "    items: list[T]\n"
    "    def __init__(self, items: Own[list[T]]) -> None:\n"
    "        self.items = items\n"
    "    def take(self) -> Own[T]:\n"
    "        if len(self.items) == 0:\n"
    "            raise Empty(\"empty\")\n"
    "        return self.items.pop()\n"
    # The try/except makes this a resumable frame rather than the simple
    # generator peephole, so the yield goes through the frame's slot gate.
    "    def __iter__(self) -> Iterator[Own[{inner}]]:\n"
    "        while True:\n"
    "            try:\n"
    "                yield self.take()\n"
    "            except Empty:\n"
    "                return\n"
    "def main() -> None:\n"
    "    s = Src([{items}])\n"
    "    for v in s:\n"
    "        print(v)\n"
    "main()\n"
)

OWN_TPARAM = _SRC.format(inner="T", items="1, 2, 3")
# An owned VIEW yield slot: `str` is outside the bare capture families, so
# peeling Own must not reach it.
OWN_STR = (_SRC.format(inner="str", items="\"a\", \"b\"")
           .replace("class Src[T]:", "class Src:")
           .replace("items: list[T]", "items: list[str]")
           .replace("items: Own[list[T]]", "items: Own[list[str]]")
           .replace("-> Own[T]:", "-> Own[str]:"))


def _generate(src: str):
    """(fallback tally, face witnesses) from a full THIR generation -- the
    resumable leaves are lowered by the codegen driver, so `lower_module`
    alone never reaches the yield seam."""
    compiler, modules = _compile(src)
    compiler.generate_code_to_strings(
        _entry(modules), options=CodeGenOptions(emit_source_comments=True))
    return dict(compiler._thir_face_witnesses)


class TestOwnTypeParamYieldSlot:
    def test_routes_byte_identical(self):
        out = "".join(_assert_routes_byte_identical(OWN_TPARAM))
        # The yield hands the owned rvalue out bare, exactly as a bare `T`
        # slot would.
        assert "return __self.take();" in out

    def test_witnesses_the_yield_value_face(self):
        witnesses = _generate(OWN_TPARAM)
        assert witnesses.get("res.yield_value", 0) >= 1, witnesses


class TestOwnViewYieldSlotKeepsRejecting:
    def test_rejects_at_the_yield_slot_gate(self):
        # `res.yield_type` carries no shape detail -- the landmark IS the
        # named gate this pin claims.
        _assert_rejects_at(_reject_tally(OWN_STR),
                           "body:stmt.expr_stmt:name.own_read")
