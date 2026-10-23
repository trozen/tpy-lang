# Under `--default-int=BigInt` an int literal's home is `int`: the scale orders
# the fixed widths, then float, then float32, so sets pick as CPython does.
from tpy import dispatch, float32, int16, int32, int64


@dispatch
def m(x: int32) -> str:
    return "int32"


@dispatch
def m(x: float) -> str:
    return "float"


@dispatch
def w(x: int64) -> str:
    return "int64"


@dispatch
def w(x: float32) -> str:
    return "f32"


@dispatch
def fw(x: float) -> str:
    return "float"


@dispatch
def fw(x: float32) -> str:
    return "f32"


def take(x: int32 | str) -> str:
    return "union"


def tick(calls: list[int32]) -> bool:
    calls.append(1)
    return True


class V:
    def __init__(self) -> None:
        pass

    @dispatch
    def narrow(self, x: int64) -> str:
        return "int64"

    @dispatch
    def narrow(self, x: int16) -> str:
        return "int16"

    @dispatch
    def f(self, x: float32) -> float:
        # TPy holds a float32 already; the stand-in rounds here.
        return float32(x)

    @dispatch
    def f(self, x: str) -> float:
        return 0.0

    @dispatch
    def k(self, x: int) -> str:
        return "int"

    @dispatch
    def k(self, x: float) -> str:
        return "float"


def main() -> None:
    # Two int literals at the int / float variants of the builtins.
    print("builtins:", divmod(7, 2), pow(2, 10))  # tpyc: ok
    # A fixed width the literal fits beats a float.
    print("free_function:", m(3), w(3))  # tpyc: ok
    # From an `int` home, float is nearer than float32.
    print("free_function:", fw(3))  # tpyc: ok
    # The literal is exact at its home `int`.
    print("method:", V().k(3), V().k(0.5))  # tpyc: ok
    v = V()
    # A walrus binds a BigInt, converted to the picked fixed width / float32.
    print("method:", v.narrow((n := 100)), v.f((y := 3)), n, y)  # tpyc: ok
    calls: list[int32] = []
    # A walrus and a conditional render as BigInt, converted to the member.
    print("union:", take((z := 3)), take(3 if tick(calls) else 3), z, len(calls))  # tpyc: ok


main()
