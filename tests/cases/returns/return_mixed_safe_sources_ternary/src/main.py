# Ternary RHS where the two arms reach the merge via different
# safe sources -- one arm is param-derived, the other is a trusted
# call return. The recursive is_safe_to_return_expr must descend into
# both arms; a per-call-site OR over is_param_derived_expr alone or
# is_trusted_call_return_expr alone would miss this shape because
# neither predicate accepts the whole ternary uniformly.

from tpy import Int32, StrView


class Point:
    def __init__(self, x: Int32) -> None:
        self.x = x


def trusted(p: Point) -> Point:
    return p


def pick_view(s: StrView) -> StrView:
    return s


def ternary_record(seed: Point, flag: bool) -> Point:
    result = seed if flag else trusted(seed)
    return result  # tpyc: ok


def ternary_strview(p: str, flag: bool) -> StrView:
    sv = StrView(p) if flag else pick_view(StrView("x"))
    return sv  # tpyc: ok


def main():
    seed = Point(13)
    print(ternary_record(seed, True).x)
    print(ternary_record(seed, False).x)
    print(ternary_strview("hello", True))
    print(ternary_strview("hello", False))


main()
