# A str-family Optional PARAM at an owned Optional element slot has no
# lowering: the param spells a VIEW inner while the slot spells the owned
# buffer, so the element needs a per-element materialization, not the bare
# whole read its scalar / Span / value-tuple siblings take.
from tpy import Int32


def f(s: str | None) -> Int32:
    xs = [s]  # tpyc: error(/name\.optstr_unproven_read/)
    return len(xs)


def main() -> None:
    print(f("a"))


main()
