# math.prod has overloads for Iterable[int32], Iterable[int] (BigInt),
# and Iterable[float]. The return type matches the input type family;
# kwargs disambiguate when positional args alone are ambiguous (empty
# list literal).
import math
from tpy import int32


def main() -> None:
    ints: list[int32] = []
    ints.append(int32(2))
    ints.append(int32(3))
    ints.append(int32(4))
    n: int32 = math.prod(ints)
    print(n)  # 24 -- int32 overload preserves int32 result

    n_with_start: int32 = math.prod(ints, start=int32(10))
    print(n_with_start)  # 240

    floats: list[float] = []
    floats.append(1.5)
    floats.append(2.0)
    f: float = math.prod(floats)
    print(f)  # 3.0

    # Empty Iterable[int32] disambiguated by the float kwarg type.
    empty_int: list[int32] = []
    f_empty: float = math.prod(empty_int, start=2.5)
    print(f_empty)  # 2.5

    # Empty Iterable[int32] disambiguated by the int kwarg type.
    n_empty: int32 = math.prod(empty_int, start=int32(7))
    print(n_empty)  # 7

    # BigInt iterable picks the Iterable[int] overload; result is BigInt
    # so products outside int32 range are exact.
    bigs: list[int] = []
    bigs.append(1000000)
    bigs.append(1000000)
    bigs.append(1000000)
    big_prod: int = math.prod(bigs)
    print(big_prod)  # 1000000000000000000 -- exceeds int32 max

    # Empty Iterable[int32] disambiguated to the BigInt overload by an
    # explicit BigInt start kwarg.
    big_empty: int = math.prod(empty_int, start=int(42))
    print(big_empty)  # 42


main()
