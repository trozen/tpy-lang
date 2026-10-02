# An inferred local bound to a field read owns a copy (the view rule,
# docs/LANGUAGE_FEATURES.md "String Type Semantics"), so returning it as
# `StrView` would hand back a view of a dying local. The hint names the one
# spelling that keeps the view, `return h.s`; the explicit `y: StrView = h.s`
# opt-in is not suggested while a spelled view local takes no loan.
from tpy import StrView


class H:
    s: str

    def __init__(self) -> None:
        self.s = "a" * 40


def head(h: H) -> StrView:
    y = h.s
    return y  # tpyc: error(/'y' owns a copy of 'h.s'; return h.s directly$/)


def main() -> None:
    print(head(H()))


main()
