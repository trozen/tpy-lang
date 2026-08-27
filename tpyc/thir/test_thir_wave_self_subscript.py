"""The `self` receiver of a user-record subscript.

A plain method's `self` is a `Record*`, so both the record's `operator[]`
and the checked setitem dunder need the `(*this)` lvalue -- the same deref
the record call-arg and ternary-arm positions apply. Without it the
receiver rendered as the raw pointer, which is not the AST's spelling and
is not valid C++ either.
"""

from .testutil import _assert_routes_byte_identical

_REC = (
    "from tpy import Int32\n"
    "class H:\n"
    "    _d: dict[str, str]\n"
    "    def __init__(self) -> None:\n"
    "        self._d = {}\n"
    "    def __setitem__(self, key: str, value: str) -> None:\n"
    "        self._d[key] = value\n"
    "    def __getitem__(self, key: str) -> str:\n"
    "        return self._d[key]\n"
)


class TestSelfReceiverSubscriptDerefs:
    SRC = (
        _REC +
        "    def seed(self) -> None:\n"
        "        self[\"a\"] = \"b\"\n"
        "    def peek(self) -> str:\n"
        "        return self[\"a\"]\n"
        "def main() -> None:\n"
        "    h = H()\n"
        "    h.seed()\n"
        "    print(h.peek())\n"
        "main()\n"
    )

    def test_routes_byte_identical(self):
        thir = _assert_routes_byte_identical(self.SRC)
        assert "(*this)" in thir[0]


class TestResumableSelfReceiverStaysBare:
    # A resumable frame binds `self` as a `Record&` field, so the same
    # subscript must NOT take the deref the pointer receiver needs. Both
    # directions of that guard are load-bearing: derefing here would spell
    # `(*__self)` on a reference.
    SRC = (
        "import asyncio\n"
        "class H:\n"
        "    _d: dict[str, str]\n"
        "    def __init__(self) -> None:\n"
        "        self._d = {\"a\": \"b\"}\n"
        "    def __getitem__(self, key: str) -> str:\n"
        "        return self._d[key]\n"
        "    async def fetch(self) -> str:\n"
        "        await asyncio.sleep(0.0)\n"
        "        return self[\"a\"]\n"
        "async def go() -> None:\n"
        "    h = H()\n"
        "    print(await h.fetch())\n"
        "def main() -> None:\n"
        "    asyncio.run(go())\n"
        "main()\n"
    )

    def test_routes_byte_identical(self):
        thir = _assert_routes_byte_identical(self.SRC)
        joined = thir[0] + thir[1]
        assert "__self[\"a\"]" in joined
        assert "(*__self)" not in joined
