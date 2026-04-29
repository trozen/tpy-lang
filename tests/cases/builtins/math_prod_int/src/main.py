# math.prod has overloads for Iterable[Int32] and Iterable[float]. The
# return type matches the input type family; kwargs disambiguate when
# positional args alone are ambiguous (empty list literal).
import math
from tpy import Int32


def main() -> None:
    ints: list[Int32] = []
    ints.append(Int32(2))
    ints.append(Int32(3))
    ints.append(Int32(4))
    n: Int32 = math.prod(ints)
    print(n)  # 24 -- int overload preserves Int32 result

    n_with_start: Int32 = math.prod(ints, start=Int32(10))
    print(n_with_start)  # 240

    floats: list[float] = []
    floats.append(1.5)
    floats.append(2.0)
    f: float = math.prod(floats)
    print(f)  # 3.0

    # Empty Iterable[Int32] disambiguated by the float kwarg type.
    empty_int: list[Int32] = []
    f_empty: float = math.prod(empty_int, start=2.5)
    print(f_empty)  # 2.5

    # Empty Iterable[Int32] disambiguated by the int kwarg type.
    n_empty: Int32 = math.prod(empty_int, start=Int32(7))
    print(n_empty)  # 7


main()
