from tpy import int32


class Box[T]:
    value: T

    def __init__(self, value: T) -> None:
        self.value = value

    def get(self) -> T:
        return self.value

    def set(self, value: T) -> None:
        self.value = value


def modify_list(items: list[int32]) -> None:
    items.append(100)


def main() -> None:
    # Test 1: Box with value type (int32) - param is const T&
    box_int: Box[int32] = Box[int32](42)
    box_int.set(99)
    print(box_int.get())

    # Test 2: Box with object type (list) - param is T&
    # Pass a literal - should create a temporary
    box_list: Box[list[int32]] = Box[list[int32]]([1, 2, 3])
    box_list.set([4, 5, 6])
    for x in box_list.get():
        print(x)

    # Test 3: Direct list mutation through non-generic function
    nums: list[int32] = [10, 20]
    modify_list(nums)
    for x in nums:
        print(x)


main()
