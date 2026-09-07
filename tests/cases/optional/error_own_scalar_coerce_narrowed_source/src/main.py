# A scalar coerce over a NARROWED `int | None` name at an Own[Int32] slot: the
# narrowing deref is dropped there, so the shape keeps rejecting.
from tpy import Int32, Own


def take(x: Own[Int32]) -> Int32:
    return x


def f(v: int | None) -> None:
    if v is not None:
        print(take(v))  # tpyc: error(/call\.arg_shape/)


def main() -> None:
    f(100)


main()
