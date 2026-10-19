# A call macro expanding to an int constant is that constant's literal: an
# f-string formats it and a list index (a run-time BigInt at this case's
# --default-int=BigInt) takes it as the hand literal; a macro still receives
# the operator tree it was given, unfolded.
from shape_macro import generated, identity, is_binop


def take_int(v: int) -> int:
    return v


def main() -> None:
    print("fstr", f"{identity(2**40)}", f"{identity(-2**63)}")  # tpyc: ok
    xs = [10, 20, 30, 40]
    print("index", xs[identity(2**0)], xs[identity(1)])  # tpyc: ok
    print("sees_binop", is_binop(2 * 3))
    print("generated", take_int(generated()), generated())


main()
