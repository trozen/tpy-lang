# Test: type parameters used in local variable annotations inside generic functions
from tpy import int32, Own


class Box:
    value: int32

    def __init__(self, value: int32) -> None:
        self.value = value


def collect[T](a: T, b: T) -> Own[list[T]]:
    result: list[T] = []
    result.append(a)
    result.append(b)
    return result


def main() -> None:
    xs = collect(int32(10), int32(20))
    print(len(xs))
    print(xs[0])
    print(xs[1])
    ys = collect(Box(int32(1)), Box(int32(2)))
    print(len(ys))
    print(ys[0].value)

main()
