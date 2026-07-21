"""Polymorphic-isinstance narrowing (dyn-protocol / inheritance subjects):
if-init cast condition, spelled reads, and the excluded-face fallbacks."""
from __future__ import annotations

from ..codegen_cpp.context import CodeGenOptions
from .testutil import _compile, _entry, _fn, _lower_ctx

_PRELUDE = (
    "from typing import Protocol\n"
    "from tpy import dynamic\n"
    "@dynamic\n"
    "class Pet(Protocol):\n"
    "    def name(self) -> str: ...\n"
    "class Dog(Pet):\n"
    "    def name(self) -> str:\n        return \"dog\"\n"
    "class Cat:\n"
    "    label: str\n"
    "    def __init__(self, label: str) -> None:\n"
    "        self.label = label\n"
    "    def name(self) -> str:\n        return self.label\n"
    "    def purr(self) -> str:\n        return self.label + \"-purr\"\n"
)


def _cpp(src: str, thir: bool) -> str:
    compiler, modules = _compile(src)
    entry = _entry(modules)
    hpp, cpp = compiler.generate_code_to_strings(
        entry, options=CodeGenOptions(emit_source_comments=False,
                                      thir_codegen=thir))
    return hpp + cpp


class TestDynNarrowLowering:
    SRC = (
        _PRELUDE
        + "def structural(p: Pet) -> str:\n"
        + "    if isinstance(p, Cat):\n"
        + "        return \"cat:\" + p.purr()\n"
        + "    return \"other:\" + p.name()\n"
        + "def inherits(p: Pet) -> str:\n"
        + "    if isinstance(p, Dog):\n"
        + "        return \"dog:\" + p.name()\n"
        + "    return \"other\"\n"
    )

    def test_routing_is_non_vacuous(self):
        thir = _lower_ctx(self.SRC)
        for name in ("structural", "inherits"):
            assert _fn(thir, name) is not None, name

    def test_byte_identical(self):
        assert _cpp(self.SRC, thir=True) == _cpp(self.SRC, thir=False)

    def test_emitted_shapes(self):
        out = _cpp(self.SRC, thir=True)
        # Structural conformer: adapter cast through the @dynamic base.
        assert ("if (Cat* __p_ptr = ::tpy::dyn_adapter_cast<Pet, Cat>(&p); "
                "(__p_ptr != nullptr)) {") in out
        # Inheritance conformer: plain dynamic_cast.
        assert ("if (Dog* __p_ptr = dynamic_cast<Dog*>(&p); "
                "(__p_ptr != nullptr)) {") in out
        # Branch reads render the pre-bound pointer's deref.
        assert "(*__p_ptr).purr()" in out

    def test_elif_link_routes(self):
        # A dyn condition in an elif link: each chain link gates
        # independently through the shared skeleton.
        src = (
            _PRELUDE
            + "from tpy import Int32\n"
            + "def f(p: Pet, k: Int32) -> str:\n"
            + "    if k > 0:\n        return \"k\"\n"
            + "    elif isinstance(p, Cat):\n"
            + "        return p.purr()\n"
            + "    return p.name()\n"
        )
        thir = _lower_ctx(src)
        assert _fn(thir, "f") is not None
        out = _cpp(src, thir=True)
        assert out == _cpp(src, thir=False)
        assert ("} else if (Cat* __p_ptr = "
                "::tpy::dyn_adapter_cast<Pet, Cat>(&p); "
                "(__p_ptr != nullptr)) {") in out

    def test_while_head_falls_back(self):
        # The while-isinstance position is an excluded rung for the dyn arm
        # (the fresh-reference-local face, not the if-init form).
        src = (
            _PRELUDE
            + "from tpy import Int32\n"
            + "def f(p: Pet) -> Int32:\n"
            + "    n = 0\n"
            + "    while isinstance(p, Cat):\n"
            + "        n += 1\n"
            + "        if n > 3:\n            break\n"
            + "    return n\n"
        )
        thir = _lower_ctx(src)
        assert _fn(thir, "f") is None

    def test_optional_subject_falls_back(self):
        # Pointer-repr Optional subjects spell the cast input differently
        # (bare name, not &name) -- excluded from the slice.
        src = (
            _PRELUDE
            + "def f(p: Pet | None) -> str:\n"
            + "    if p is not None:\n"
            + "        if isinstance(p, Cat):\n"
            + "            return p.purr()\n"
            + "    return \"no\"\n"
        )
        thir = _lower_ctx(src)
        assert _fn(thir, "f") is None

    def test_tuple_check_falls_back(self):
        # A tuple check yields a union fact -- no single-fact if-init form.
        src = (
            _PRELUDE
            + "def f(p: Pet) -> str:\n"
            + "    if isinstance(p, (Cat, Dog)):\n"
            + "        return \"pet\"\n"
            + "    return \"no\"\n"
        )
        thir = _lower_ctx(src)
        assert _fn(thir, "f") is None
