"""`isinstance(self, Sub)` narrowing inside a method body.

`self` was excluded from both polymorphic-narrowing gates because its cast
argument is not the `&name` every other subject takes: a plain method's `this`
is already pointer-shaped, a simple generator's `(*this)` folds back to it,
and a resumable coro's `__self` is a `Record&` field that takes the address.
With that spelling in place the reads follow: inside the branch `self` routes
through the pre-bound cast pointer (`(*__self_ptr)`), which also flips the
member access from `this->` to `.`.

Two shapes stay out. A RESUMABLE single-fact narrowing would name its alias
`__self`, colliding with the frame field -- the AST renames it
(`__self_narrowed`) and no THIR alias maker reproduces that. And the
assert-position form (`assert isinstance(self, Dog)`), whose persistent
`const Dog& __self = *dynamic_cast<...>(this);` alias is its own maker.
"""

from __future__ import annotations

import io

from .emit import emit_thir_body
from .testutil import (_lower_ctx, _fn, _assert_byte_identical, _compile,
                       _entry)
from ..codegen_cpp import CodeGenOptions


def _body(thir, name: str) -> str:
    buf = io.StringIO()
    emit_thir_body(buf, _fn(thir, name))
    return buf.getvalue()


def _fallback(src: str) -> dict:
    """A routed RESUMABLE never enters `thir.functions`, so its reject has to
    be read off the fallback map."""
    compiler, modules = _compile(src)
    entry = _entry(modules)
    compiler.generate_code_to_strings(
        entry, options=CodeGenOptions(emit_source_comments=False,
                                      thir_codegen=True))
    return dict(compiler._thir_fallback)


_PETS = (
    "from typing import Protocol, Iterator\n"
    "from tpy import dynamic, readonly\n"
    "@dynamic\n"
    "class Tagged(Protocol):\n    pass\n"
    "class Pet(Tagged):\n"
    "    name: str\n"
    "    def __init__(self, n: str) -> None:\n        self.name = n\n"
    "    @readonly\n"
    "    def describe(self) -> str:\n"
    "        if isinstance(self, Dog):\n"
    "            return \"dog \" + self.bark()\n"
    "        return \"pet \" + self.name\n"
    "    def mutate(self, sfx: str) -> str:\n"
    "        self.name = self.name + sfx\n"
    "        if isinstance(self, Dog):\n"
    "            return \"MUT \" + self.bark()\n"
    "        return \"PET \" + self.name\n"
    "    @readonly\n"
    "    def kind(self) -> str:\n"
    "        if isinstance(self, (Dog, Cat)):\n"
    "            return \"mammal \" + self.name\n"
    "        return \"other \" + self.name\n"
    "    @readonly\n"
    "    def assert_dog(self) -> str:\n"
    "        assert isinstance(self, Dog)\n"
    "        return \"A \" + self.bark()\n"
    "    def gen_single(self) -> Iterator[str]:\n"
    "        if isinstance(self, Dog):\n"
    "            yield self.bark()\n"
    "        yield self.name\n"
    "    def gen_tuple(self) -> Iterator[str]:\n"
    "        if isinstance(self, (Dog, Cat)):\n"
    "            yield \"mammal\"\n"
    "        yield self.name\n"
    "class Dog(Pet):\n"
    "    def __init__(self, n: str) -> None:\n        super().__init__(n)\n"
    "    @readonly\n"
    "    def bark(self) -> str:\n        return \"woof \" + self.name\n"
    "class Cat(Pet):\n"
    "    def __init__(self, n: str) -> None:\n        super().__init__(n)\n"
)


class TestSelfPolymorphicNarrowing:
    def test_readonly_method_casts_this_to_a_const_pointer(self):
        body = _body(_lower_ctx(_PETS), "describe")
        assert ("const Dog* __self_ptr = dynamic_cast<const Dog*>(this);"
                in body)
        # The narrowed read derefs the cast pointer and accesses with `.`.
        assert "(*__self_ptr).bark()" in body
        assert "this->name" in body  # the un-narrowed tail is unchanged
        _assert_byte_identical(_PETS)

    def test_mutable_method_casts_this_to_a_mutable_pointer(self):
        body = _body(_lower_ctx(_PETS), "mutate")
        assert "Dog* __self_ptr = dynamic_cast<Dog*>(this);" in body
        assert "const Dog*" not in body

    def test_tuple_form_binds_no_alias(self):
        body = _body(_lower_ctx(_PETS), "kind")
        assert "dynamic_cast<const Dog*>(this) != nullptr" in body
        assert "__self_ptr" not in body

    def test_assert_form_defers_and_resumable_single_routes(self):
        # The assert form still needs the persistent-alias maker (defers).
        # The RESUMABLE single-fact form now ROUTES (the round C poly-self
        # cell: the spelled `__self_narrowed` re-extraction); only the
        # TUPLE-form cond keeps res.cond (the sibling tripwire below).
        assert _fn(_lower_ctx(_PETS), "assert_dog") is None
        fb = _fallback(_PETS)
        assert fb.get("body:stmt.assert") == 1
        assert fb.get("resumable:res.cond") == 1
        assert "resumable:res.narrowed_resume" not in fb

    def test_resumable_tuple_form_is_admitted_but_unreachable(self):
        # The TUPLE gate drops the `self` exclusion WITHOUT a resumable guard
        # (unlike its single-fact sibling), because the tuple form binds no
        # alias and so cannot collide with the `__self` frame field. Nothing
        # routes it today: the resumable condition lowering rejects first.
        # This pin is the tripwire -- if `res.cond` ever widens, the poly
        # admission behind it becomes live and needs its own byte-check.
        assert _fallback(_PETS).get("resumable:res.cond") == 1
