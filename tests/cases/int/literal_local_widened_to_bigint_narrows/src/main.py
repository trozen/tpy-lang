# A literal-seeded local that a later `int`-returning assignment makes an `int`
# is an `int` at every EARLIER use too: each position that converts an int
# into a fixed width takes the checked `.to_fixed_check<T>()` narrow there.
# One section per narrow position.
from tpy import int32


def widen() -> int:
    return 1


def subscript_positions(data: str, xs: list[int32]) -> None:
    p = 0
    print(data[p])       # read index
    print(xs[p])
    xs[p] = 9            # __setitem__ index
    xs[p] += 1           # element aug-assign: read AND write index
    print(xs[0])
    p = widen()          # the assignment that makes `p` an int
    print(p)


def value_positions(data: str) -> None:
    p = 1
    print(data[p:])      # slice lower bound
    print(data[:p])      # slice upper bound
    q: int32 = 7
    q += p               # FixedInt += (declared) BigInt
    print(q)
    print(f"{p}")        # f-string arg -> .to_string()
    p = widen()
    print(p)


def main() -> None:
    subscript_positions("abc", [1, 2, 3])
    value_positions("abcd")


main()
