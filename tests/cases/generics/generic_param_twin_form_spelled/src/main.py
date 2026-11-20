# A reassigned `T: ValueType` parameter at a spelled `T = Own[str]`: the
# prologue copy takes the owned parameter over (`param_handoff<own_arg<...>>`),
# and the caller's string, copied into the argument, stays intact.
from tpy import Own, ValueType


def rebind[T: ValueType](x: T, y: T) -> T:
    x = y  # tpyc: ok
    return x


def main() -> None:
    a = "a long string that does not fit in place"
    print("rebind Own[str]", rebind[Own[str]](a, "b"), a)


main()
