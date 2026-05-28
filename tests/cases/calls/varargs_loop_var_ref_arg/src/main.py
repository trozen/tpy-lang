# Loop-variable as a vararg arg into a non-mutating mutable slot. Exercises
# the mark_loop_var_mutated half of the fix (the param-arg variant only hits
# mark_param_mutated). Without the marking, the loop var `b` binds as
# `const auto& b` and `&b` is `const Box*` -- mismatched with std::array<Box*>.
from tpy import Int32, nocopy


@nocopy
class Box:
    val: Int32

    def __init__(self, v: Int32) -> None:
        self.val = v


def take_mut(*items: Box) -> Int32:
    n: Int32 = 0
    for b in items:
        n += b.val
    return n


def via_loop(xs: list[Box]) -> Int32:
    total: Int32 = 0
    for b in xs:
        total += take_mut(b)  # tpyc: ok
    return total


def main() -> None:
    items: list[Box] = []
    items.append(Box(5))
    items.append(Box(6))
    print(via_loop(items))


main()
