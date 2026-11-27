# A float literal at a float32 slot converts as Python's float32(float(L)); each
# annotated slot prints float(float32(x)), since CPython ignores the annotation.
from typing import Final
from tpy import dispatch, float32
import part_containers
import part_positions
import part_slots

# global and Final constant at a float32 slot
G: float32 = 1.0000000596046448  # tpyc: ok
K: Final[float32] = 1.0000000596046448  # tpyc: ok


class Box:
    x: float32

    def __init__(self) -> None:
        # field write
        self.x = 1.0000000596046448  # tpyc: ok


def take(x: float32) -> float:
    return float(float32(x))


def ret() -> float32:
    # return
    return 1.0000000596046448  # tpyc: ok


@dispatch
def pick(x: float) -> str:
    return "float"


@dispatch
def pick(x: float32) -> str:
    return "float32"


def main() -> None:
    # constructor: takes the `float` variant, like float32(v)
    a = float32(1.0000000596046448)  # tpyc: type(float32)
    print("ctor", float(a))
    v = 1.0000000596046448
    print("ctor_var", float(float32(v)))
    # annotated local
    b: float32 = 1.0000000596046448  # tpyc: ok
    print("local", float(float32(b)))
    print("param", take(1.0000000596046448))
    print("return", float(float32(ret())))
    print("field", float(float32(Box().x)))
    # negation at the constructor and at a slot
    n1 = float32(-1.0000000596046448)  # tpyc: ok
    n2: float32 = -1.0000000596046448  # tpyc: ok
    print("neg", float(n1), float(float32(n2)))
    # operand of an operator and of an augmented assignment
    z = float32(0.0)
    print("operand", float(float32(z + 1.0000000596046448)))
    z += 1.0000000596046448
    print("augassign", float(float32(z)))
    print("global", float(float32(G)), "final", float(float32(K)))
    # out of float32 range: the double converts to inf, as in CPython
    print("overflow", float(float32(1e40)))
    # an infinite literal keeps its numeric_limits render
    w: float32 = 1e400  # tpyc: ok
    print("inf", float(float32(w)), float(float32(1e400)))
    # inverse: exact literals and the common case are unchanged in value
    print("exact", float(float32(1.5)), float(float32(0.1)))
    # inverse: a float literal still picks the `float` variant
    print("dispatch", pick(0.1))
    # inverse: an int literal at a float32 slot keeps its plain conversion
    q: float32 = 16777217  # tpyc: ok
    print("int_literal", float(float32(q)), float(float32(5)))
    # inverse: a comparison compares the literal as a double; the explicit
    # float(a) keeps it in double, since the implicit mixed form `a == L`
    # (L not exact in float32) fails clang's -Wliteral-range
    # (BUGS.md#float32-float-literal-compare-clang-literal-range)
    print("compare", float(a) == 1.0000000596046448, float(a) < 1.0000000596046448)
    part_containers.main()
    part_positions.main()
    part_slots.main()


main()
