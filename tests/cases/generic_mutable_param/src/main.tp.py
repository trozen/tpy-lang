from tpy import Int32


class Box[T]:
    value: T

    def __init__(self, value: T) -> None:
        self.value = value

    def get(self) -> T:
        return self.value

    def set(self, value: T) -> None:
        self.value = value


def modify_list(items: list[Int32]) -> None:
    items.append(100)


def main() -> None:
    # Test 1: Box with value type (Int32) - param is const T&
    box_int: Box[Int32] = Box[Int32](42)
    box_int.set(99)
    print(box_int.get())

    # Test 2: Box with object type (list) - param is T&
    box_list: Box[list[Int32]] = Box[list[Int32]]([1, 2, 3])
    new_items: list[Int32] = [4, 5, 6]
    box_list.set(new_items)
    for x in box_list.get():
        print(x)

    # Test 3: Direct list mutation through non-generic function
    nums: list[Int32] = [10, 20]
    modify_list(nums)
    for x in nums:
        print(x)


main()
