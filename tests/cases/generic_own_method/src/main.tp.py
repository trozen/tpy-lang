from tpy import Int32, Own, copy


class Box[T]:
    value: T

    def __init__(self, value: T) -> None:
        self.value = value

    def take(self) -> Own[T]:
        return copy(self.value)


def main() -> None:
    # Test method calls on Own[T] return value
    # Use separate instances to avoid copy-vs-reference semantic differences

    # Test 1: Chained method call compiles and runs
    b1: Box[list[Int32]] = Box[list[Int32]]([1, 2, 3])
    b1.take().append(4)
    print("chained call ok")

    # Test 2: Assign to variable, then call method
    b2: Box[list[Int32]] = Box[list[Int32]]([10, 20, 30])
    c: list[Int32] = b2.take()
    c.append(40)
    for x in c:
        print(x)


main()
