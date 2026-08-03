"""Protocol-receiver @cpp_template stub arm: a ZERO-ARG dunder template on a
protocol value (`it.__next__()` on `Iterator[T]`, `x.__len__()` on `Sized`)
expands through the shared cpp_template render on both paths. Arg-carrying
templates keep rejecting (their args render through the AST's
inline_template loop, unmirrored for this family)."""

from __future__ import annotations

from .testutil import (
    _lower_ctx, _lower_ctx_witnessed, _fn, _assert_byte_identical,
    _assert_routes_byte_identical,
)

_NEXT_SRC = (
    "from tpy import Int32, Own\n"
    "class It:\n"
    "    n: Int32\n"
    "    def __init__(self) -> None:\n"
    "        self.n = 0\n"
    "    def __next__(self) -> Int32:\n"
    "        if self.n >= 2:\n"
    "            raise StopIteration\n"
    "        self.n += 1\n"
    "        return self.n\n"
    "class Src:\n"
    "    def __iter__(self) -> Own[It]:\n"
    "        return It()\n"
    "def main() -> None:\n"
    "    it = iter(Src())\n"
    "    while True:\n"
    "        try:\n"
    "            v = it.__next__()\n"
    "        except StopIteration:\n"
    "            break\n"
    "        print(v)\n"
    "main()\n"
)


class TestProtocolTemplateStub:
    def test_iterator_next_in_try_routes(self):
        # The error-return bind position: `v = it.__next__()` on the
        # Iterator[T] protocol local -- the `{self}.__next__()` template
        # expands over the bare receiver inside the try-unwrap block.
        thir, faces = _lower_ctx_witnessed(_NEXT_SRC)
        assert _fn(thir, "main") is not None
        assert faces.get("method.protocol_template", 0) >= 1
        cpp = _assert_routes_byte_identical(_NEXT_SRC)
        assert "auto __try_tmp_2 = it.__next__();" in cpp[1]

    def test_sized_len_dunder_routes(self):
        # A zero-arg free-symbol template (`x.__len__()` on Sized ->
        # `::tpy::__len__(x)`): same admission row, different spelling.
        src = ("from typing import Sized\n"
               "def show(x: Sized) -> None:\n"
               "    print(x.__len__())\n"
               "def main() -> None:\n"
               "    xs = [1, 2, 3]\n"
               "    show(xs)\n"
               "main()\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "show") is not None
        assert faces.get("method.protocol_template", 0) == 1
        cpp = _assert_routes_byte_identical(src)
        # The structural-protocol body monomorphizes into the header.
        assert "std::cout << ::tpy::__len__(x) << " in cpp[0]

    def test_arg_carrying_template_stays_ast(self):
        # `x.__getitem__(1)` on Sequence[Int32]: the template takes an arg,
        # which renders through the AST's inline_template loop -- the
        # protocol family's free-call arg rows do not mirror it, so the
        # body must keep falling back.
        src = ("from typing import Sequence\n"
               "from tpy import Int32\n"
               "def pick(x: Sequence[Int32]) -> None:\n"
               "    print(x.__getitem__(1))\n"
               "def main() -> None:\n"
               "    xs: list[Int32] = [1, 2, 3]\n"
               "    pick(xs)\n"
               "main()\n")
        assert _fn(_lower_ctx(src), "pick") is None
        _assert_byte_identical(src)
