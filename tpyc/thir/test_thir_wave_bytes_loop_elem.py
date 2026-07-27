"""A bytes-yielding iterator's loop var: the element type reaches the
binding as a `PendingViewType`, so it must resolve through the view family
before spelling -- the same resolution the str element already took."""

from __future__ import annotations

from .testutil import _lower_ctx, _fn, _assert_byte_identical

_SRC = ("from typing import Iterator\n"
        "def lines() -> Iterator[bytes]:\n"
        "    yield b\"a\"\n"
        "    yield b\"bc\"\n")


class TestBytesLoopElem:
    def test_bytes_iterator_loop_routes(self):
        src = (_SRC
               + "def use() -> None:\n"
               + "    for line in lines():\n"
               + "        print(len(line))\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "use") is not None
        _assert_byte_identical(src)

    def test_bytes_list_element_routes(self):
        src = ("def use(xs: list[bytes]) -> None:\n"
               "    for b in xs:\n"
               "        print(len(b))\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "use") is not None
        _assert_byte_identical(src)

    def test_str_element_still_routes(self):
        # The shared view-family resolver replaced a str-only call; the str
        # element must keep its existing render.
        src = ("def use(xs: list[str]) -> None:\n"
               "    for s in xs:\n"
               "        print(len(s))\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "use") is not None
        _assert_byte_identical(src)


class TestBytesLoopElemBoundaries:
    def test_optional_bytes_element_stays_ast(self):
        # The Optional exclusion runs BEFORE the pending-family check, so
        # resolving the bytes view must not leak an Optional loop var in.
        src = ("def use(xs: list[bytes | None]) -> None:\n"
               "    for b in xs:\n"
               "        print(b is None)\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "use") is None

    def test_bytearray_element_render_unchanged(self):
        # `bytearray` is a REFERENCE type on the other axis; the view
        # resolver returns None for it, so its (already routing) borrow-alias
        # binding must render exactly as before.
        src = ("def use(xs: list[bytearray]) -> None:\n"
               "    for b in xs:\n"
               "        b.append(1)\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "use") is not None
        _assert_byte_identical(src)

    def test_nested_container_element_render_unchanged(self):
        src = ("def use() -> None:\n"
               "    xs = [[1, 2], [3]]\n"
               "    for inner in xs:\n"
               "        print(len(inner))\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "use") is not None
        _assert_byte_identical(src)
