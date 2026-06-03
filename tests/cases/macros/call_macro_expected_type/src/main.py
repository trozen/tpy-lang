# A call macro reads ctx.expected_type to choose the literal kind for the
# target slot. The SAME poly("...") call yields a bool in a bool slot, an int
# in an int slot, and a plain string where sema has no expected type -- only
# possible if expected_type is populated from the slot.
from polymac import poly
from tpy import Int32


def takes_bool(b: bool) -> Int32:
    return 1 if b else 0


def returns_bool() -> bool:
    return poly("true")          # return slot -> bool


def main() -> None:
    b: bool = poly("true")       # assignment RHS, declared bool slot
    print(1 if b else 0)

    n: Int32 = poly("100")       # int slot
    print(n + 1)                 # arithmetic proves it's an int, not "100"

    print(takes_bool(poly("true")))   # declared-typed call arg sees bool slot

    s = poly("hello")            # no expected type -> plain string
    print(s)

    print(1 if returns_bool() else 0)

    opt: Int32 | None = poly("8")     # Optional slot: macro unwraps to int
    print((opt if opt is not None else 0) + 1)


main()
