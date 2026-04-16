# Field type sub-patterns for disambiguating parameterized union members
from tpy import Int32, Own


class Box[T]:
    value: T
    def __init__(self, value: Own[T]) -> None:
        self.value = value


# Disambiguate Box[str] vs Box[Int32] using field type sub-patterns
def unwrap(x: Box[str] | Box[Int32]) -> str:
    match x:
        case Box(value=str() as v):
            return "string: " + v
        case Box(value=Int32() as n):
            return "number: " + str(n)


# Type pattern without as-binding (just disambiguation)
def describe(x: Box[str] | Box[Int32]) -> str:
    match x:
        case Box(value=str()):
            return "is string"
        case Box(value=Int32()):
            return "is number"


# Mixed type sub-pattern and regular capture
class Pair[T]:
    first: T
    second: str
    def __init__(self, first: Own[T], second: str) -> None:
        self.first = first
        self.second = second


def mixed(x: Pair[str] | Pair[Int32]) -> str:
    match x:
        case Pair(first=str() as f, second=s):
            return f + " / " + s
        case Pair(first=Int32() as n, second=s):
            return str(n) + " / " + s


def main() -> None:
    a: Box[str] | Box[Int32] = Box("hello")
    b: Box[str] | Box[Int32] = Box(42)
    print(unwrap(a))
    print(unwrap(b))

    print(describe(a))
    print(describe(b))

    c: Pair[str] | Pair[Int32] = Pair("abc", "xyz")
    d: Pair[str] | Pair[Int32] = Pair(99, "end")
    print(mixed(c))
    print(mixed(d))

main()
