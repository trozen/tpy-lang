# A call returning a VALUE union at an ARGUMENT slot: the member-return render
# is bare and the call return-type row has no slot for it, so
# `take(pick(True))` is rejected.
from tpy import Int32


def pick(b: bool) -> Int32 | str:
    if b:
        return 1
    return "x"


def take(v: Int32 | str) -> bool:
    return isinstance(v, Int32)


def show() -> bool:
    return take(pick(True))  # tpyc: error(/call.ret_type.union_value/)


def main() -> None:
    print(show())


main()
