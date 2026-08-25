# A literal-seeded local that a later `int`-returning assignment retro-widens to
# BigInt still types Int32 at every EARLIER use. Codegen must key the checked
# `.to_fixed_check<T>()` narrows on the local's DECLARED type -- keying on
# sema's per-occurrence type emits a bare BigInt into an int32 slot (no viable
# overload). One case per narrow position.
from enum import Enum
from tpy import Int32


class Color(Enum):
    Red = 0
    Green = 1


def widen() -> int:
    return 1


def subscript_positions(data: str, xs: list[Int32],
                        d: dict[Int32, Int32]) -> None:
    p = 0
    print(data[p])       # read index
    print(xs[p])
    xs[p] = 9            # __setitem__ index
    xs[p] += 1           # element aug-assign: read AND write index
    del d[p]             # __delitem__ index
    print(xs[0], len(d))
    p = widen()          # the assignment that retro-widens `p` to BigInt
    print(p)


def value_positions(data: str) -> None:
    p = 1
    print(data[p:])      # slice lower bound
    print(data[:p])      # slice upper bound
    q: Int32 = 7
    q += p               # FixedInt += (declared) BigInt
    print(q)
    print(f"{p}")        # f-string arg -> .to_string()
    print(Color(p))      # enum from_value arg
    p = widen()
    print(p)


def main() -> None:
    subscript_positions("abc", [1, 2, 3], {0: 1})
    value_positions("abcd")


main()
