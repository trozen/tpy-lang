# A non-NAME source at an open `T` element slot (a `copy()` call) stays out of
# the type-param element row. TPy rejects `[copy(x)]` here today.
from tpy import Own, copy


def make[T](x: T) -> Own[list[T]]:
    return [copy(x)]  # tpyc: error(/expr.container_literal/)


def main() -> None:
    print(len(make(42)))


main()
