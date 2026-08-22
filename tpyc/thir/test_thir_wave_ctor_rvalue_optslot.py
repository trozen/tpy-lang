"""The DictReader ctor-arg cell: three rows that let a generic stdlib ctor
route its arguments.

  * the MUTATED-slot record-rvalue temp no longer excludes a method-call
    rvalue -- the temp is a storage decl sink (`R __tmp_N = <init>;`), so
    threading the STORAGE result use into its init makes the whole
    `_record_rvalue_temp_arg` slice lower, module-qualified ctor and plain
    method rvalue alike;
  * the CONTAINER twin of the `Optional[Own[T]]` bare-move name arg;
  * the `Own[container]` receiver peel for the `.contains` membership member.
"""

from __future__ import annotations

from .testutil import (
    _assert_byte_identical, _assert_routes_byte_identical,
    _lower_ctx_witnessed,
)


class TestMutatedSlotMethodRvalueTemp:
    _SRC = (
        "from tpy import Int32, Own\n"
        "class Payload:\n"
        "    n: Int32\n"
        "    def __init__(self, n: Int32) -> None:\n"
        "        self.n = n\n"
        "    def bump(self) -> None:\n"
        "        self.n += 1\n"
        "class Factory:\n"
        "    seed: Int32\n"
        "    def __init__(self, seed: Int32) -> None:\n"
        "        self.seed = seed\n"
        "    def make(self) -> Own[Payload]:\n"
        "        return Payload(self.seed)\n"
        "class Holder:\n"
        "    total: Int32\n"
        "    def __init__(self, p: Payload) -> None:\n"
        "        p.bump()\n"
        "        self.total = p.n\n"
        "def main() -> None:\n"
        "    f = Factory(4)\n"
        "    h = Holder(f.make())\n"
        "    print(h.total)\n"
        "main()\n"
    )

    def test_method_rvalue_at_mutated_ctor_slot_routes(self):
        # The fence this cell removed claimed the temp's init would
        # "dead-end at the marker result gate"; the STORAGE thread is what
        # the init actually needed, and the whole class renders identically.
        _t, w = _lower_ctx_witnessed(self._SRC)
        assert w.get("argtemp.ctor_mut_rvalue", 0) >= 1
        hpp, cpp = _assert_routes_byte_identical(self._SRC)
        out = hpp + cpp
        assert "Payload __tmp_1 = f.make();" in out
        assert "Holder h = Holder(__tmp_1);" in out


class TestOptOwnContainerCtorArg:
    _SRC = (
        "from tpy import Int32, Own\n"
        "class Reader:\n"
        "    n: Int32\n"
        "    def __init__(self, fieldnames: Own[list[str]] | None = None"
        ") -> None:\n"
        "        self.n = 0\n"
        "def last_use() -> None:\n"
        "    fn: list[str] = [\"a\", \"b\"]\n"
        "    r = Reader(fn)\n"
        "    print(r.n)\n"
        "def main() -> None:\n"
        "    last_use()\n"
        "main()\n"
    )

    def test_movable_last_use_passes_bare_move(self):
        _t, w = _lower_ctx_witnessed(self._SRC)
        assert w.get("ctor.opt_own_container_name", 0) >= 1
        hpp, cpp = _assert_routes_byte_identical(self._SRC)
        assert "Reader(std::move(fn))" in hpp + cpp

    def test_still_live_source_stays_ast(self):
        # BOUNDARY: the copy shape is unwitnessed -- the shared lowering arm
        # re-derives the move verdict and rejects when the name outlives the
        # call (the record twin's rule, one payload family over).
        src = self._SRC.replace(
            "def last_use() -> None:\n"
            "    fn: list[str] = [\"a\", \"b\"]\n"
            "    r = Reader(fn)\n"
            "    print(r.n)\n",
            "def still_live() -> None:\n"
            "    fn: list[str] = [\"a\", \"b\"]\n"
            "    r = Reader(fn)\n"
            "    print(len(fn), r.n)\n").replace("last_use()", "still_live()")
        _t, _w = _lower_ctx_witnessed(src)
        _assert_byte_identical(src)


class TestOwnContainerMembershipRecv:
    _SRC = (
        "from tpy import Own\n"
        "from typing import Iterator\n"
        "def dicts() -> Iterator[Own[dict[str, str]]]:\n"
        "    yield {\"a\": \"1\"}\n"
        "def sets() -> Iterator[Own[set[str]]]:\n"
        "    yield {\"a\"}\n"
        "def lists() -> Iterator[Own[list[str]]]:\n"
        "    yield [\"a\"]\n"
        "def use_dict() -> None:\n"
        "    for d in dicts():\n"
        "        print(\"a\" in d)\n"
        "def use_set() -> None:\n"
        "    for s in sets():\n"
        "        print(\"a\" in s)\n"
        "def use_list() -> None:\n"
        "    for xs in lists():\n"
        "        print(\"a\" in xs)\n"
        "def main() -> None:\n"
        "    use_dict()\n"
        "    use_set()\n"
        "    use_list()\n"
        "main()\n"
    )

    def test_dict_and_set_route_list_keeps_rejecting(self):
        # The `.contains` member composes on the bare Own-bound name; the
        # LIST receiver has no `__contains__` and takes the unwidened
        # ranges::contains path, so it must keep falling back.
        cpp = _assert_byte_identical(self._SRC)
        out = cpp[0] + cpp[1]
        assert '(d.contains("a"))' in out
        assert '(s.contains("a"))' in out
        from .testutil import _compile, _entry
        from ..codegen_cpp.context import CodeGenOptions
        compiler, modules = _compile(self._SRC)
        compiler.generate_code_to_strings(
            _entry(modules),
            options=CodeGenOptions(emit_source_comments=True,
                                   comment_line_numbers=False,
                                   thir_codegen=True))
        body_keys = {k: v for k, v in compiler._thir_fallback.items()
                     if k.startswith("body:")}
        assert body_keys == {"body:stmt.expr_stmt:binop.shape.in": 1}
