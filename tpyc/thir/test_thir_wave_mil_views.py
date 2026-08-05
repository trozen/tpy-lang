"""Ctor member-init-list cells for the view-shaped field families: a
`std::span<T>` field and a `Send[Callable[...]]` field, both bare copies."""

from __future__ import annotations

from .testutil import _lower_ctor, _assert_byte_identical


class TestSpanFieldMil:
    def test_span_param_copies_bare(self):
        src = ("from tpy import Int32, Span\n"
               "class Box:\n"
               "    items: Span[Int32]\n"
               "    def __init__(self, items: Span[Int32]) -> None:\n"
               "        self.items = items\n")
        assert _lower_ctor(src, "Box") is not None
        _assert_byte_identical(src)

    def test_array_source_stays_ast(self):
        # An Array local takes the implicit span conversion, a render the
        # exact-type row does not describe.
        src = ("from tpy import Int32, Span, Array\n"
               "class Box:\n"
               "    items: Span[Int32]\n"
               "    def __init__(self) -> None:\n"
               "        arr: Array[Int32, 2] = [1, 2]\n"
               "        self.items = arr\n")
        assert _lower_ctor(src, "Box") is None


class TestSendCallableFieldMil:
    def test_send_wrapper_peels_on_both_sides(self):
        # `Send[...]` is erased in storage form on BOTH the field and the
        # param, so the pair the AST spells identically must not be rejected
        # by comparing a peeled param against an unpeeled field.
        src = ("from tpy import Int32, Send\n"
               "from typing import Callable\n"
               "class Handler:\n"
               "    cb: Send[Callable[[Int32], None]]\n"
               "    def __init__(self, cb: Send[Callable[[Int32], None]]) "
               "-> None:\n"
               "        self.cb = cb\n")
        assert _lower_ctor(src, "Handler") is not None
        _assert_byte_identical(src)

    def test_field_read_source_stays_ast(self):
        # Peeling `Send[...]` off the field type must not weaken the
        # NAME-source pin: only a param name is admitted, so a field read
        # (another emit shape) keeps rejecting.
        src = ("from tpy import Int32\n"
               "from typing import Callable\n"
               "class Src:\n"
               "    cb: Callable[[Int32], None]\n"
               "    def __init__(self, cb: Callable[[Int32], None]) -> None:\n"
               "        self.cb = cb\n"
               "class Handler:\n"
               "    cb: Callable[[Int32], None]\n"
               "    def __init__(self, s: Src) -> None:\n"
               "        self.cb = s.cb\n")
        assert _lower_ctor(src, "Handler") is None

    def test_lambda_source_routes(self):
        # A routable lambda renders its closure into the MIL direct-init
        # (`action([]() { ... })` -- the mil.callable_lambda row; former
        # fence, converted when the row landed; byte identity pinned in
        # test_thir_wave_callable_ref).
        src = ("from typing import Callable\n"
               "class Handler:\n"
               "    action: Callable[[], None]\n"
               "    def __init__(self) -> None:\n"
               "        self.action = lambda: print(0)\n")
        assert _lower_ctor(src, "Handler") is not None

    def test_self_capturing_lambda_source_stays_ast(self):
        # A self-capturing lambda stays out: the MIL never confirmed the
        # `this` receiver spelling (_lambda_routable's self_this default).
        src = ("from typing import Callable\n"
               "from tpy import Int32\n"
               "class Handler:\n"
               "    n: Int32\n"
               "    action: Callable[[], None]\n"
               "    def __init__(self) -> None:\n"
               "        self.n = 1\n"
               "        self.action = lambda: print(self.n)\n")
        assert _lower_ctor(src, "Handler") is None
