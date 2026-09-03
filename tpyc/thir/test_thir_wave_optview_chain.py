"""The value-optional VIEW chain: the owned/view inner split at the decl,
return, container-element and setitem sinks.

`str | None` binds the BORROW `std::optional<std::string_view>` at a param
and the OWNED `std::optional<std::string>` everywhere else, while
`StrView | None` is the view optional in every position. Which side a sink
is on decides whether the value passes bare or takes the per-element
materialize shim -- these pins fix each pairing, plus the boundary where
the AST's render is an unmirrored quirk.
"""

from __future__ import annotations

from .testutil import (
    _assert_rejects_at,
    _reject_tally,
    _assert_byte_identical, _assert_routes_byte_identical, _fn, _lower_ctx,
    _lower_ctx_witnessed,
)


class TestOptViewParamSinks:
    _SRC = (
        "from typing import Optional\n"
        "def assign_local(s: Optional[str]) -> None:\n"
        "    local: Optional[str] = s\n"
        "    if local is not None:\n"
        "        print(local)\n"
        "def append_to_list(items: list[Optional[str]],\n"
        "                   s: Optional[str]) -> None:\n"
        "    items.append(s)\n"
        "def main() -> None:\n"
        "    assign_local(\"world\")\n"
        "    assign_local(None)\n"
        "    xs: list[Optional[str]] = []\n"
        "    append_to_list(xs, \"a\")\n"
        "    append_to_list(xs, None)\n"
        "main()\n"
    )

    def test_decl_and_element_sinks_take_the_shim(self):
        # Both owned sinks rebuild the borrow `optional<string_view>` param
        # into `optional<string>`; the container insert adds the consuming
        # move its slot takes.
        hpp, cpp = _assert_routes_byte_identical(self._SRC)
        _thir, wit = _lower_ctx_witnessed(self._SRC)
        assert wit.get("decl.optview_shim", 0) == 1
        assert wit.get("arg.optview_param_own_elem", 0) == 1
        out = hpp + cpp
        assert ("std::optional<std::string> local = s ? "
                "std::make_optional(std::string(*s)) : std::nullopt;") in out
        assert ("items.push_back(std::move(s ? std::make_optional("
                "std::string(*s)) : std::nullopt));") in out

    def test_record_method_arg_still_passes_bare(self):
        # BOUNDARY: a USER-record method's arg loop threads no target type,
        # so the optional goes BARE there -- only the container stubs (which
        # do thread their element slot) take the shim.
        src = (
            "from typing import Optional\n"
            "class Rec:\n"
            "    s: Optional[str]\n"
            "    def __init__(self, s: Optional[str]) -> None:\n"
            "        self.s = s\n"
            "    def take(self, s: Optional[str]) -> None:\n"
            "        print(s)\n"
            "def call_it(r: Rec, s: Optional[str]) -> None:\n"
            "    r.take(s)\n"
            "def main() -> None:\n"
            "    call_it(Rec(None), \"x\")\n"
            "main()\n"
        )
        hpp, cpp = _assert_routes_byte_identical(src)
        _thir, wit = _lower_ctx_witnessed(src)
        assert wit.get("arg.optview_param_own_elem", 0) == 0
        assert "r.take(s);" in hpp + cpp


class TestViewInnerOptionalLocal:
    _SRC = (
        "from tpy import StrView\n"
        "from typing import Optional\n"
        "def maybe_prefix(subject: str, keep: bool) -> Optional[StrView]:\n"
        "    if keep:\n"
        "        return subject[:5]\n"
        "    return None\n"
        "def main() -> None:\n"
        "    items: list[Optional[str]] = []\n"
        "    subject = \"hello world\"\n"
        "    local = maybe_prefix(subject, True)\n"
        "    items.append(local)\n"
        "    items.append(maybe_prefix(subject, True))\n"
        "    items[0] = maybe_prefix(subject, False)\n"
        "    print(items)\n"
        "main()\n"
    )

    def test_slice_return_decl_and_shim_route(self):
        hpp, cpp = _assert_routes_byte_identical(self._SRC)
        _thir, wit = _lower_ctx_witnessed(self._SRC)
        assert wit.get("ret.value_opt_view_slice", 0) == 1
        assert wit.get("decl.view_inner_opt_call", 0) == 1
        # Once per append + once at the setitem.
        assert wit.get("arg.opt_strview_own_shim", 0) == 3
        out = hpp + cpp
        assert ("return ::tpy::str_slice(subject, "
                "::tpy::BasicSlice{std::nullopt, 5});") in out
        assert ("std::optional<std::string_view> local = "
                "maybe_prefix(subject, true);") in out
        assert ("items.push_back(({ auto __ov = (local); __ov ? "
                "std::make_optional(std::string(*__ov)) : "
                "std::nullopt; }));") in out

    def test_none_test_reads_has_value(self):
        # A VIEW-inner value-opt LOCAL carries no registered binding kind
        # (its narrowed read differs from the owned twin's), but its None
        # test is still `has_value()`, not a pointer compare.
        src = (
            "from tpy import StrView\n"
            "from typing import Optional\n"
            "def head(subject: str) -> Optional[StrView]:\n"
            "    return subject[:2]\n"
            "def probe(subject: str) -> int:\n"
            "    v = head(subject)\n"
            "    if v is None:\n"
            "        return 0\n"
            "    return 1\n"
            "def main() -> None:\n"
            "    print(probe(\"hello\"))\n"
            "main()\n"
        )
        hpp, cpp = _assert_routes_byte_identical(src)
        assert "if ((!v.has_value()))" in hpp + cpp

    def test_narrowed_read_of_a_view_inner_local_keeps_rejecting(self):
        # BOUNDARY: the AST's narrowed read of such a local is a
        # whole-optional-wrap quirk THIR does not mirror -- only the
        # WHOLE-optional read routes.
        src = (
            "from tpy import StrView\n"
            "from typing import Optional\n"
            "def head(subject: str) -> Optional[StrView]:\n"
            "    return subject[:2]\n"
            "def probe(subject: str) -> int:\n"
            "    v = head(subject)\n"
            "    if v is not None:\n"
            "        return len(v)\n"
            "    return 0\n"
            "def main() -> None:\n"
            "    print(probe(\"hello\"))\n"
            "main()\n"
        )
        _assert_rejects_at(_reject_tally(src),
                           "body:stmt.return:name.value_opt_view_inner_local")

    def test_owned_inner_return_from_a_view_source_takes_the_copy(self):
        # The VIEW-inner slice row must not capture `-> str | None`: at an
        # owned inner sema wraps the slice in the view->owned coercion, and
        # the copy that coercion renders is what reaches the slot.
        src = (
            "from typing import Optional\n"
            "def head(subject: str) -> Optional[str]:\n"
            "    if len(subject) > 2:\n"
            "        return subject[:2]\n"
            "    return None\n"
            "def main() -> None:\n"
            "    print(head(\"hello\"))\n"
            "main()\n"
        )
        hpp, cpp = _assert_routes_byte_identical(src)
        assert ("return std::string(::tpy::str_slice(subject, "
                "::tpy::BasicSlice{std::nullopt, 2}));") in hpp + cpp
        _thir, wit = _lower_ctx_witnessed(src)
        assert wit.get("ret.value_opt_view_slice", 0) == 0
        assert wit.get("ret.value_opt_view_materialize", 0) >= 1
