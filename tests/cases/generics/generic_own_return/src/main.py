from tpy import int32, Own, copy


class Box[T]:
    value: T

    def __init__(self, value: T) -> None:
        self.value = value

    def get(self) -> T:
        return self.value

    def take(self) -> Own[T]:
        return copy(self.value)


def main() -> None:
    # Test 1: Own[T] with value type (int32) - same behavior as T
    box_int: Box[int32] = Box[int32](42)
    val: int32 = box_int.take()
    print(val)

    # Test 2: Own[T] with object type returns by value
    box_list: Box[list[int32]] = Box[list[int32]]([1, 2, 3])
    taken: list[int32] = box_list.take()
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
