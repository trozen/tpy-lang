"""Pins for BODIED `@overload` variants as first-class lowering entries.

A bodyless `@overload` stub is emitted per specialization off a shared
implementation, so it is not a lowering entry of its own. A variant that
carries its own body is: sema forbids pairing bodied variants with a trailing
implementation, so each one owns its body outright and the function driver
emits it standalone. The structural gate has to keep the two apart -- the
stub's `sig.special_callable` reject, and the multi-entry overload-set reject
that exists to stop a shared impl being lowered against one stub's facts,
must both stand down for the bodied form.

`base64.standard_b64decode` is the live two-variant consumer; its committed
render in `tests/cases/harness/stdlib_render` is the oracle.
"""

from __future__ import annotations

from pathlib import Path

from ..codegen_cpp.context import CodeGenOptions
from ..compilation_context import activate_compiler
from .lower.functions import _check_callable_structure
from .reject import ThirUnsupported
from .testutil import _compile, _entry, _lower_ctx_witnessed

_BODIED = (
    "from typing import overload\n"
    "@overload\n"
    "def describe(x: int) -> str:\n"
    "    return 'int: ' + str(x)\n"
    "@overload\n"
    "def describe(x: str) -> str:\n"
    "    return 'str: ' + x\n"
    "def main() -> None:\n"
    "    print(describe(42))\n"
    "    print(describe('hello'))\n"
    "main()\n"
)

_STUB_SET = (
    "from typing import overload\n"
    "class Dog:\n"
    "    name: str\n"
    "    def __init__(self, name: str) -> None:\n"
    "        self.name = name\n"
    "class Cat:\n"
    "    lives: int\n"
    "    def __init__(self, lives: int) -> None:\n"
    "        self.lives = lives\n"
    "@overload\n"
    "def describe(a: Dog) -> str: ...\n"
    "@overload\n"
    "def describe(a: Cat) -> str: ...\n"
    "def describe(a: Dog | Cat) -> str:\n"
    "    if isinstance(a, Dog):\n"
    "        return 'Dog: ' + a.name\n"
    "    return 'Cat: ' + str(a.lives)\n"
    "def main() -> None:\n"
    "    print(describe(Dog('Rex')), describe(Cat(9)))\n"
    "main()\n"
)

# The stdlib oracle: base64's two bodied `standard_b64decode` variants.
_ORACLE = (Path(__file__).resolve().parents[2] / "tests" / "cases"
           / "harness" / "stdlib_render" / "expected" / "src" / "base64.cpp")

_ORACLE_BLOCK = (
    "// @overload\n"
    "// def standard_b64decode(data: str) -> bytes:\n"
    "std::vector<uint8_t> standard_b64decode(std::string_view data) {\n"
    "    // return standard_b64decode(data.encode())\n"
    "    return standard_b64decode(::tpy::bytes_from_str(data));\n"
    "}\n"
)


def _describe_variants(source: str):
    """The source's `describe` callables, in declaration order."""
    compiler, modules = _compile(source)
    entry = _entry(modules)
    funcs = [f for f in entry.ast.functions if f.name == "describe"]
    return compiler, entry, funcs


class TestBodiedOverloadSeeding:
    def test_both_bodied_variants_lower(self):
        thir, _faces = _lower_ctx_witnessed(_BODIED)
        bodies = [f for f in thir.functions if f.name == "describe"]
        assert len(bodies) == 2, [f.name for f in thir.functions]
        # Each variant kept its OWN body -- not one impl lowered twice.
        assert bodies[0].body != bodies[1].body

    def test_bodied_variant_passes_the_structural_gate(self):
        compiler, entry, funcs = _describe_variants(_BODIED)
        assert len(funcs) == 2
        with activate_compiler(compiler):
            for f in funcs:
                assert f.is_overload_stub and not f.is_stub
                _check_callable_structure(f, entry.analyzer, None,
                                          allow_resumable=True)

    def test_bodyless_stub_still_rejects_at_the_gate(self):
        # BOUNDARY: the stub form is emitted per specialization off the
        # trailing implementation, so it is not an entry of its own and the
        # gate must keep saying so.
        compiler, entry, funcs = _describe_variants(_STUB_SET)
        stubs = [f for f in funcs if f.is_stub]
        assert len(stubs) == 2, funcs
        with activate_compiler(compiler):
            for f in stubs:
                try:
                    _check_callable_structure(f, entry.analyzer, None,
                                              allow_resumable=True)
                except ThirUnsupported as ex:
                    assert ex.reason == "sig.special_callable", ex.reason
                else:
                    raise AssertionError(
                        "a bodyless overload stub was admitted as its own "
                        "lowering entry")

    def test_stub_set_still_emits_one_body_per_specialization(self):
        # ... and the shared implementation still supplies both emitted
        # bodies, each specialized to its stub.
        thir, _faces = _lower_ctx_witnessed(_STUB_SET)
        bodies = [f for f in thir.functions if f.name == "describe"]
        assert len(bodies) == 2, [f.name for f in thir.functions]

    def test_stdlib_variant_renders_as_committed(self):
        assert _ORACLE_BLOCK in _ORACLE.read_text()

        src = ("import base64\n"
               "def main() -> None:\n"
               "    print(len(base64.standard_b64decode('QQ==')))\n"
               "main()\n")
        compiler, modules = _compile(src)
        mod = [m for m in modules if m.name == "base64"][0]
        _hpp, cpp = compiler.generate_code_to_strings(
            mod, options=CodeGenOptions(emit_source_comments=True,
                                        comment_line_numbers=False))
        assert _ORACLE_BLOCK in cpp
