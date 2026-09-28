# T() default-construction syntax for generic type parameters
from typing import Final
from tpy import int32, ValueType

K: Final[int32] = 7


class Noisy(ValueType):
    n: int32

    def __init__(self, n: int32 = K) -> None:
        print("noisy init", n)
        self.n = n


class NoisyHeir(Noisy, ValueType):
    pass


def make_default[T](x: T = T()) -> T:
    return x

def main() -> None:
    a: int32 = make_default[int32]()
    print(a)

    b: str = make_default[str]()
    print(b)
    print(len(b))

    c: bool = make_default[bool]()
    print(c)

    d: int32 = make_default[int32](42)
    print(d)

    # T inferred from arg
    e: int32 = make_default(42)
    print(e)

    # a ValueType heir's `T()` runs the inherited `__init__` with its defaults
    f = make_default[NoisyHeir]()  # tpyc: ok
    print(f.n)

main()
