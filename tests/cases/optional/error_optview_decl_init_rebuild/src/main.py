# The Optional view/str identity coerce passes bare only at a plain argument slot;
# at a DECL INIT it takes a statement-expression rebuild that has no THIR row.
# Concretely, `x: str | None = returns_view_opt()` is rejected by TPy today.
from tpy import StrView


def takes_str_opt(s: str | None) -> None:
    if s is None:
        print("(none)")
    else:
        print(s)


def returns_view_opt() -> StrView | None:
    return StrView("v")


def relay() -> None:
    x: str | None = returns_view_opt()  # tpyc: error(/expr.coerce/)
    takes_str_opt(x)


def main() -> None:
    relay()


main()
