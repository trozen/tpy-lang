# Further float32 slots of a float literal (Optional payloads inside owned
# slots, method arguments, literals, reflected operands), and an
# Optional comparison, which compares the literal as a double.
import math
from tpy import Own, float32


def opt_eq(x: float32 | None) -> None:
    # a comparison operand is not a float32 slot: Python compares doubles
    print("opt_eq", x == 1.0000000596046448, 1.0000000596046448 == x)  # tpyc: ok
    print("opt_ne", x != 1.0000000596046448, 1.0000000596046448 != x)  # tpyc: ok


def opt_eq01(x: float32 | None) -> None:
    print("opt_eq01", x == 0.1, 0.1 != x)  # tpyc: ok


def opt_eq_neg01(x: float32 | None) -> None:
    # a negated literal is a float literal too, compared as a double
    print("opt_eq_neg", x == -0.1, -0.1 != x)  # tpyc: ok


def opt_eq_signed(x: float32 | None) -> None:
    # a unary plus or minus keeps the literal a double at the comparison
    print("opt_eq_plus", x == +1.0000000596046448, +1.0000000596046448 != x)  # tpyc: ok
    print("opt_eq_minus", x == -1.0000000596046448, -1.0000000596046448 != x)  # tpyc: ok
    # an int literal still compares at the payload type
    print("opt_eq_int", x == 1, 1 != x)  # tpyc: ok


# The body cannot read an owned Optional parameter yet
# (BUGS.md#own-optional-scalar-param-read-rejected), so only the
# argument's render is pinned (the generated C++).
def own_opt(x: Own[float32 | None]) -> None:
    pass


class C:
    def m(self, x: float32) -> float:
        return float(float32(x))


def show(o: float32 | None) -> float:
    return float(float32(o)) if o is not None else -1.0


def main() -> None:
    opt_eq(float32(1.0))
    opt_eq01(float32(0.1))
    opt_eq_neg01(float32(-0.1))
    opt_eq_signed(float32(1.0))
    opt_eq_signed(float32(-1.0))
    # Optional element of a list: append, insert, item assignment
    xs: list[float32 | None] = []
    xs.append(1.0000000596046448)  # tpyc: ok
    xs.insert(0, 1.0000000596046448)  # tpyc: ok
    xs[1] = 1.0000000596046448  # tpyc: ok
    print("opt_list", [show(v) for v in xs])
    xs[1] = -1.0000000596046448  # tpyc: ok
    print("opt_list_neg", show(xs[1]))
    # Optional value of a dict
    od: dict[str, float32 | None] = {}
    od["a"] = 1.0000000596046448  # tpyc: ok
    print("opt_dict_set", show(od["a"]))
    # an Own[float32 | None] argument
    own_opt(1.0000000596046448)  # tpyc: ok
    own_opt(-1.0000000596046448)  # tpyc: ok
    print("method_arg", C().m(1.0000000596046448))  # tpyc: ok
    d: dict[str, float32] = {"a": 1.0000000596046448}  # tpyc: ok
    d["b"] = 1.0000000596046448  # tpyc: ok
    print("dict", float(float32(d["a"])), float(float32(d["b"])))
    t: tuple[float32, float32] = (1.0000000596046448, 2.5)  # tpyc: ok
    print("tuple", float(float32(t[0])), float(float32(t[1])))
    # a list literal renders its elements bare
    # (BUGS.md#float32-literal-slot-retype-skipped); a value float32 does
    # not hold exactly would fail the build under clang's
    # -Wimplicit-float-conversion, so these are exact
    ls: list[float32] = [1.5, 2.5]  # tpyc: ok
    print("list_literal", float(float32(ls[0])), float(float32(ls[1])))
    b = float32(0.0)
    b = 1.0000000596046448  # tpyc: ok
    print("reassign", float(float32(b)))
    z = float32(0.0)
    print("reflected", float(float32(1.0000000596046448 + z)))  # tpyc: ok
    print("nan", math.isnan(float32(float("nan"))))
