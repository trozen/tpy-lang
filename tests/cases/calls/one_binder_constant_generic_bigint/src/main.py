# Under --default-int=BigInt a value read of an integer constant generic is
# a BigInt converted from the constant's full value: `N` past int64 prints
# whole, `str(N)` takes `str(x: int)`, `pick(N)` the `int` variant, `N + 1`
# is a BigInt addition. Positions: method, nested def (module level does not
# apply: `N` is class-scoped).
from tpy import dispatch, int32


@dispatch
def pick(x: int32) -> str:
    return "int32"


@dispatch
def pick(x: int) -> str:
    return "int"


class Box[N: int]:
    def run(self) -> None:
        # The read keeps every value of the constant.
        print("read", N)  # tpyc: ok
        # The BigInt variant of str's constructor.
        print("ctor", str(N))  # tpyc: ok
        # The default int's variant.
        print("fn", pick(N))  # tpyc: ok
        # BigInt arithmetic: no overflow past 2**63 - 1.
        print("op", N + 1, -N, ~N, N < 5)  # tpyc: ok
        x = 0
        x += N  # tpyc: ok
        print("aug", x)

        def inner() -> int:
            return N  # tpyc: ok

        print("nested", inner())


Box[7]().run()
Box[2147483647]().run()
Box[9223372036854775808]().run()
