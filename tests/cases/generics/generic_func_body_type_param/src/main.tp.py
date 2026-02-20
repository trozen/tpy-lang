# Test: type parameters used in local variable annotations inside generic functions
from tpy import Int32, Own


class Box:
    value: Int32

    def __init__(self, value: Int32) -> None:
        self.value = value


def collect[T](a: T, b: T) -> Own[list[T]]:
    result: list[T] = []
    result.append(a)
    result.append(b)
    return result


def main() -> None:
    xs = collect(Int32(10), Int32(20))
    print(len(xs))
    print(xs[0])
    print(xs[1])
    ys = collect(Box(Int32(1)), Box(Int32(2)))
    print(len(ys))
    print(ys[0].value)

main()
