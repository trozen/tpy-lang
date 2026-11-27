# A @cpp_template spells every runtime parameter exactly once, so each argument
# is evaluated as in its TPy twin; sections print side effects (bindings: None).
from tpy import int32
from tpy.extern import cpp_template
from fixtures import mark, mark_i
import part_slots
import part_methods
import part_protocols
import part_defaults
import part_cycle_a


@cpp_template("(static_cast<void>({0}), 0)")
def pick(p: int32 | str) -> int32: ...


@cpp_template("(static_cast<void>({0}), 0)")
def zero(p: int32) -> int32: ...


# Never called: its union default is rendered by no C++ declaration.
@cpp_template("(static_cast<void>({0}), 0)")
def unused(x: int32 | str = 1) -> int32: ...  # tpyc: ok


def main() -> None:
    # union parameter: a literal and a side-effecting argument both convert
    a = pick(1)  # tpyc: ok
    b = pick(mark("union"))  # tpyc: ok
    print("union", "done")
    # the explicit discard still evaluates the argument, once
    c = zero(mark_i("discard", 3))  # tpyc: ok
    print("discard", "done")
    part_slots.main()
    part_methods.main()
    part_protocols.main()
    part_defaults.main()
    part_cycle_a.main()


main()
