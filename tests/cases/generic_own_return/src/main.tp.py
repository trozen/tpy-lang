from tpy import Int32, Own


class Box[T]:
    value: T

    def __init__(self, value: T) -> None:
        self.value = value

    def get(self) -> T:
        return self.value

    def take(self) -> Own[T]:
        return self.value


def main() -> None:
    # Test 1: Own[T] with value type (Int32) - same behavior as T
    box_int: Box[Int32] = Box[Int32](42)
    val: Int32 = box_int.take()
    print(val)

    # Test 2: Own[T] with object type returns by value
    box_list: Box[list[Int32]] = Box[list[Int32]]([1, 2, 3])
    taken: list[Int32] = box_list.take()
    for x in taken:
        print(x)

    # Test 3: Verify get() still works (uses trait-based return)
    print(box_int.get())

    # Test 4: Variable inference from Own[T] unwraps to T
    c = box_list.take()
    c.append(4)
    for x in c:
        print(x)


main()
