# The temp-free rows of the sibling argument tables, at a STRUCTURAL PROTOCOL
# method call. That family carried five rows and refused every other source,
# including the plainest ones: a str local at an owned-str slot, a member
# source at a value-union slot, a container literal or field read at a
# readonly container slot, `None` at a value-repr Optional slot.
# Only value-typed and readonly parameters appear here; the mutable
# reference-typed ones are in protocols/proto_param_forms.
from typing import Optional, Protocol
from tpy import Own, int32, readonly


class W:
    items: list[int32]

    def __init__(self) -> None:
        self.items = [1, 2]


class Reader(Protocol):
    def take_str(self, s: Own[str]) -> int32: ...
    def take_union(self, u: int32 | str) -> int32: ...
    def take_ro_list(self, xs: readonly[list[int32]]) -> int32: ...
    def take_opt(self, s: Optional[str]) -> int32: ...


class Sink:
    tag: int32

    def __init__(self, tag: int32) -> None:
        self.tag = tag

    def take_str(self, s: Own[str]) -> int32:
        return len(s)

    def take_union(self, u: int32 | str) -> int32:
        return len(str(u))

    def take_ro_list(self, xs: readonly[list[int32]]) -> int32:
        return len(xs)

    def take_opt(self, s: Optional[str]) -> int32:
        return 0 if s is None else len(s)


def drive(pp: Reader) -> None:
    w = W()
    # a view-form str NAME at an Own[str] protocol slot
    s0 = "hello"
    print("str_local", pp.take_str(s0))  # tpyc: ok
    # ... and a str literal at the same slot
    print("str_literal", pp.take_str("xy"))  # tpyc: ok
    # a member-typed source at a value-union slot
    print("union_member", pp.take_union(7))  # tpyc: ok
    # a container LITERAL at a readonly container slot -- rendered in place,
    # exactly as at a record-method slot
    print("ro_list_literal", pp.take_ro_list([3, 4]))  # tpyc: ok
    # ... and the container FIELD read at the same slot
    print("ro_list_field", pp.take_ro_list(w.items))  # tpyc: ok
    # `None` at a value-repr Optional slot
    print("opt_none", pp.take_opt(None))  # tpyc: ok


def main() -> None:
    drive(Sink(0))


main()
