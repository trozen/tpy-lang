"""Flow-sensitive LiteralType facts: branch seeding (the mirror of
emit_isinstance_extractions' LiteralType row), reassign pops, the
expression-level ==/!= and chain folds (bare true/false renders), and the
fences at the unmirrored assert/while seeding sites."""

from __future__ import annotations

from .testutil import (
    _lower_ctx, _lower_ctx_witnessed, _fn, _assert_byte_identical,
)

_LIT = "from typing import Literal\n"


class TestLiteralFactFolds:
    def test_nested_fold_renders_true_false(self):
        # Single-value fact: nested compares fold to bare true/false, the
        # branch bodies kept (no dead-branch elimination outside overload
        # specialization).
        src = (_LIT
               + "def f(mode: Literal['r', 'rb']) -> None:\n"
               + "    if mode == 'rb':\n"
               + "        if mode == 'rb':\n"
               + "            print('always')\n"
               + "        if mode == 'r':\n"
               + "            print('never')\n"
               + "    else:\n"
               + "        print('text')\n"
               + "def main() -> None:\n    f('rb')\n    f('r')\nmain()\n")
        thir, w = _lower_ctx_witnessed(src)
        assert _fn(thir, "f") is not None
        assert w.get("narrow.literal_branch_facts", 0) >= 1
        assert w.get("binop.literal_fact_fold", 0) >= 2
        cpp = _assert_byte_identical(src)
        assert "if (true) {" in cpp[1]
        assert "if (false) {" in cpp[1]

    def test_reassign_clears_fact(self):
        # A write pops the fact: the nested compare renders plain again.
        src = (_LIT
               + "def f(x: Literal[1, 2]) -> None:\n"
               + "    if x == 1:\n"
               + "        x = 2\n"
               + "        if x == 1:\n"
               + "            print('never')\n"
               + "        else:\n"
               + "            print('reassigned')\n"
               + "    else:\n"
               + "        print('two')\n"
               + "def main() -> None:\n    f(1)\nmain()\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "f") is not None
        cpp = _assert_byte_identical(src)
        assert "x = 2;\n" in cpp[1]
        assert cpp[1].count("if ((x == 1))") == 2

    def test_else_branch_fact_folds(self):
        # The else fact is the complement: the nested compare decides.
        src = (_LIT
               + "def f(mode: Literal['r', 'rb']) -> None:\n"
               + "    if mode == 'rb':\n"
               + "        print('bin')\n"
               + "    else:\n"
               + "        if mode == 'r':\n"
               + "            print('else-fold')\n"
               + "def main() -> None:\n    f('r')\nmain()\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "f") is not None
        cpp = _assert_byte_identical(src)
        assert "if (true) {" in cpp[1]

    def test_chain_cond_folds_leaves_only(self):
        # A TRUTHY-position chain decomposes on both paths (gen_truthy /
        # _lower_truthy), so only the LEAVES fold: `((true || false))`.
        src = (_LIT
               + "def f(mode: Literal['r', 'rb']) -> None:\n"
               + "    if mode == 'rb':\n"
               + "        if mode == 'rb' or mode == 'r':\n"
               + "            print('t')\n"
               + "        if mode == 'r' and mode == 'rb':\n"
               + "            print('never')\n"
               + "def main() -> None:\n    f('rb')\nmain()\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "f") is not None
        cpp = _assert_byte_identical(src)
        assert "if ((true || false)) {" in cpp[1]
        assert "if ((false && true)) {" in cpp[1]

    def test_value_position_chain_folds_whole(self):
        # A VALUE-position chain rides _gen_binop's _try_fold_literal_chain
        # mirror: the decided chain renders the bare bool.
        src = (_LIT
               + "def f(mode: Literal['r', 'rb']) -> None:\n"
               + "    if mode == 'rb':\n"
               + "        b = mode == 'rb' or mode == 'r'\n"
               + "        print(b)\n"
               + "def main() -> None:\n    f('rb')\nmain()\n")
        thir, w = _lower_ctx_witnessed(src)
        assert _fn(thir, "f") is not None
        cpp = _assert_byte_identical(src)
        assert "bool b = true;" in cpp[1]

    def test_undecided_outer_compare_renders_plain(self):
        # No live fact at the outer compare: plain render on both paths.
        src = (_LIT
               + "def f(mode: Literal['r', 'w', 'rb', 'wb']) -> None:\n"
               + "    if mode == 'rb' or mode == 'wb':\n"
               + "        print('bin')\n"
               + "    else:\n"
               + "        print('text')\n"
               + "def main() -> None:\n    f('rb')\nmain()\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "f") is not None
        cpp = _assert_byte_identical(src)
        assert 'if (((mode == "rb") || (mode == "wb")))' in cpp[1]


class TestLiteralFactFences:
    def test_assert_seed_stays_ast(self):
        # BOUNDARY: assert seeds the fact PERSISTENTLY on the AST -- the
        # unmirrored site fences the body.
        src = (_LIT
               + "def f(mode: Literal['r', 'rb']) -> None:\n"
               + "    assert mode == 'rb'\n"
               + "    if mode == 'rb':\n"
               + "        print('always')\n"
               + "def main() -> None:\n    f('rb')\nmain()\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "f") is None
        _assert_byte_identical(src)

    def test_while_seed_stays_ast(self):
        # BOUNDARY: the while-body seed is unmirrored -- fenced.
        src = (_LIT
               + "def f(mode: Literal['r', 'rb']) -> None:\n"
               + "    while mode == 'rb':\n"
               + "        if mode == 'rb':\n"
               + "            print('spin')\n"
               + "        break\n"
               + "def main() -> None:\n    f('r')\nmain()\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "f") is None
        _assert_byte_identical(src)

    def test_decided_membership_stays_ast(self):
        # BOUNDARY: a DECIDED `in` fold has no witnessed render.
        src = (_LIT
               + "def f(mode: Literal['r', 'rb']) -> None:\n"
               + "    if mode == 'rb':\n"
               + "        if mode in ('rb', 'wb'):\n"
               + "            print('member')\n"
               + "def main() -> None:\n    f('rb')\nmain()\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "f") is None
        _assert_byte_identical(src)
