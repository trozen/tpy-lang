# Own[T] params use T&& for non-value types, T by value for value types.
# Generic Own[T] uses std::type_identity_t<T>&& to prevent forwarding-ref deduction.
# Tests: last-use auto-move, non-last-use copy-temp, value-type by-value, and generic path.
from tpy import Int32, Own, copy

class Box:
    value: Int32
    def __init__(self, value: Int32) -> None:
        self.value = value

class Container[T]:
    items: list[T]
    def __init__(self) -> None:
        self.items = []
    def push(self, item: Own[T]) -> None:
        self.items.append(item)

def consume(b: Own[Box]) -> Int32:
    return b.value

def use_int(x: Own[Int32]) -> Int32:
    return x

def main() -> None:
    # Last use of b -- auto-moved into consume (Box&& param, std::move at call site)
    b = Box(42)
    print(consume(b))

    # Non-last use -- copy-temp inserted (sema warns about the copy)
    b2 = Box(99)
    print(consume(copy(b2)))
    print(b2.value)

    # Value type Own[Int32] -- passed by value (no T&&), std::move is harmless
    n: Int32 = 7
    print(use_int(n))

    # Generic Own[T] -- uses std::type_identity_t<T>&& in C++
    c: Container[Box] = Container()
    b3 = Box(10)
    b4 = Box(20)
    c.push(b3)
    c.push(b4)
    print(c.items[0].value)
    print(c.items[1].value)

main()
