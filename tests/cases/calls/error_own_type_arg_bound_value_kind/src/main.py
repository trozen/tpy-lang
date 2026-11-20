# The bound at `T = Own[X]` holds exactly when it holds at `X`, both ways:
# `Own[str]` owns a value type, so `T: ReferenceType` still refuses it.
from tpy import Fn, Own, ReferenceType, int32


def make() -> Own[str]:
    return "a"


def f_ref[T: ReferenceType](mk: Fn[[], T]) -> int32:
    return 1


def main() -> None:
    print(f_ref[Own[str]](make))  # tpyc: error(/'Own\[str\]' does not satisfy bound 'ReferenceType'/)


main()
