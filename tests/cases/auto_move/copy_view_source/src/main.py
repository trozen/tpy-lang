# `copy()` of a str/bytes source whose binding is the view family's PENDING
# type until sema resolves it -- a loop variable over a literal list, and a
# literal-seeded local. Both spell the source's RESOLVED type; the already
# routing owned-`str` param sibling is here so one case pins both halves.
from tpy import copy


def owned(s: str) -> str:
    return copy(s)  # tpyc: ok


def owned_bytes(b: bytes) -> bytes:
    # A `bytes` PARAM resolves OWNED but passes as a span, so the copy takes
    # the family's owning conversion -- the owned vector has no span ctor.
    return copy(b)  # tpyc: ok


def main() -> None:
    for s in ["ab", "cd"]:
        # The loop variable resolves to a view; the copy spells that view.
        u = copy(s)  # tpyc: ok
        print(u)
    b = b"xy"
    v = copy(b)  # tpyc: ok
    print(len(v))
    print(owned("hi"))
    print(len(owned_bytes(b"abc")))


main()
