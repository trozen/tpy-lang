"""Pins for the async Own[F1-record] return slot: the resumable
return-type gate admits it (Poll<T> payload, `T __tpy_async_ret =
std::move(...)`), and the position-blind value tail's last-use move
serves the sources. Bare reference-type (borrow) returns keep the
return.borrow_form fence -- the pointer-payload family is its own rung."""

from __future__ import annotations

from .testutil import (_assert_byte_identical, _compile, _entry,
                      _reject_tally)


def _gen_thir(source: str):
    from ..codegen_cpp.context import CodeGenOptions
    compiler, modules = _compile(source)
    entry = _entry(modules)
    hpp, cpp = compiler.generate_code_to_strings(
        entry, options=CodeGenOptions(emit_source_comments=False))
    return hpp + cpp, compiler._thir_face_witnesses


_BOX = (
    "import asyncio\n"
    "from tpy import Int32, nocopy, Own\n"
    "@nocopy\n"
    "class Box:\n"
    "    v: Int32\n"
    "    def __init__(self, v: Int32) -> None:\n"
    "        self.v = v\n"
)


class TestAsyncOwnReturn:
    def test_own_record_return_routes_with_move(self):
        src = _BOX + (
            "async def make(v: Int32) -> Own[Box]:\n"
            "    b = Box(v)\n"
            "    return b\n"
            "async def driver() -> Int32:\n"
            "    b = await make(7)\n"
            "    b.v += 1\n"
            "    return b.v\n"
            "def main() -> None:\n"
            "    print(asyncio.run(driver()))\n"
            "main()\n"
        )
        out, faces = _gen_thir(src)
        assert "Box __tpy_async_ret = std::move((*b));" in out
        _assert_byte_identical(src)

    def test_self_borrow_return_routes(self):
        # `async def me(self) -> Res: return self` -- the Poll<T*> SELF
        # rung lifts the receiver lvalue (`Res* __tpy_async_ret =
        # &(__self);`).
        src = (
            "import asyncio\n"
            "from tpy import Int32, nocopy\n"
            "@nocopy\n"
            "class Res:\n"
            "    n: Int32\n"
            "    def __init__(self) -> None:\n"
            "        self.n = 1\n"
            "    async def me(self) -> \"Res\":\n"
            "        return self\n"
            "async def main_() -> None:\n"
            "    res = Res()\n"
            "    r = await res.me()\n"
            "    r.n = 8\n"
            "    print(res.n)\n"
            "def main() -> None:\n"
            "    asyncio.run(main_())\n"
            "main()\n"
        )
        out, faces = _gen_thir(src)
        assert faces.get("res.return_self_borrow", 0) >= 1
        assert "Res* __tpy_async_ret = &(__self);" in out
        _assert_byte_identical(src)

    def test_param_borrow_return_keeps_rejecting(self):
        # A borrow return of a PARAM name is outside the self rung -- the
        # alias-source renders stay fenced on return.borrow_form.
        src = _BOX + (
            "async def passthru(b: Box) -> Box:\n"
            "    await asyncio.sleep(0)\n"
            "    return b\n"
            "async def main_() -> None:\n"
            "    b = Box(3)\n"
            "    r = await passthru(b)\n"
            "    r.v += 1\n"
            "    print(b.v)\n"
            "def main() -> None:\n"
            "    asyncio.run(main_())\n"
            "main()\n"
        )
        fallback = _reject_tally(src)
        assert any("return.borrow_form" in r for r in fallback)

    def test_multi_local_own_return_routes(self):
        # Two frame locals; the returned one moves out, the other is
        # ordinary frame state (the scratch dualgen shape, committed).
        src = _BOX + (
            "async def pick(v: Int32) -> Own[Box]:\n"
            "    p = Box(v)\n"
            "    q = Box(v + 1)\n"
            "    print(q.v)\n"
            "    return p\n"
            "async def driver() -> Int32:\n"
            "    b = await pick(3)\n"
            "    return b.v\n"
            "def main() -> None:\n"
            "    print(asyncio.run(driver()))\n"
            "main()\n"
        )
        out, faces = _gen_thir(src)
        assert "Box __tpy_async_ret = std::move((*p));" in out
        _assert_byte_identical(src)

    def test_local_alias_borrow_return_keeps_rejecting(self):
        # A LOCAL alias of self at the borrow slot is outside the SELF
        # rung (frame-local storage dies with the frame) -- fenced.
        src = (
            "import asyncio\n"
            "from tpy import Int32\n"
            "class Res:\n"
            "    n: Int32\n"
            "    def __init__(self) -> None:\n"
            "        self.n = 1\n"
            "    async def me(self) -> \"Res\":\n"
            "        tmp = self\n"
            "        return tmp\n"
            "async def main_() -> None:\n"
            "    res = Res()\n"
            "    r = await res.me()\n"
            "    r.n = 8\n"
            "    print(res.n)\n"
            "def main() -> None:\n"
            "    asyncio.run(main_())\n"
            "main()\n"
        )
        fallback = _reject_tally(src)
        assert any("return.borrow_form" in r or "res." in r
                   for r in fallback)


class TestSelfFieldBorrowReturn:
    """The self-FIELD borrow rung (`return self.inner` ->
    `Inner* __tpy_async_ret = &(__self.inner);`): the field read lowers
    BORROW_BIND and the addr-of wrap lifts it -- the SELF rung's
    one-level sibling. Deeper chains and non-self receivers keep the
    return.borrow_form fence."""

    _SRC = (
        "import asyncio\n"
        "from tpy import Int32\n"
        "class Inner:\n"
        "    n: Int32\n"
        "    def __init__(self, n: Int32) -> None:\n"
        "        self.n = n\n"
        "class Wrap:\n"
        "    inner: Inner\n"
        "    def __init__(self) -> None:\n"
        "        self.inner = Inner(7)\n"
        "    async def unwrap(self) -> Inner:\n"
        "        await asyncio.sleep(0)\n"
        "        return self.inner\n"
        "async def main_() -> None:\n"
        "    w = Wrap()\n"
        "    i = await w.unwrap()\n"
        "    print(i.n)\n"
        "def main() -> None:\n"
        "    asyncio.run(main_())\n"
        "main()\n"
    )

    def test_self_field_borrow_return_routes(self):
        out, faces = _gen_thir(self._SRC)
        assert faces.get("res.return_self_borrow", 0) >= 1
        assert "Inner* __tpy_async_ret = &(__self.inner);" in out
        _assert_byte_identical(self._SRC)

    def test_deeper_field_chain_stays_ast(self):
        # The rung is one-level self-field only: `return self.a.b` keeps
        # the return.borrow_form fence.
        src = (
            "import asyncio\n"
            "from tpy import Int32\n"
            "class Leaf:\n"
            "    n: Int32\n"
            "    def __init__(self, n: Int32) -> None:\n"
            "        self.n = n\n"
            "class Mid:\n"
            "    leaf: Leaf\n"
            "    def __init__(self) -> None:\n"
            "        self.leaf = Leaf(3)\n"
            "class Outer:\n"
            "    mid: Mid\n"
            "    def __init__(self) -> None:\n"
            "        self.mid = Mid()\n"
            "    async def deep(self) -> Leaf:\n"
            "        await asyncio.sleep(0)\n"
            "        return self.mid.leaf\n"
            "async def main_() -> None:\n"
            "    o = Outer()\n"
            "    lf = await o.deep()\n"
            "    print(lf.n)\n"
            "def main() -> None:\n"
            "    asyncio.run(main_())\n"
            "main()\n"
        )
        fallback = _reject_tally(src)
        assert fallback, fallback
