from tpy import Int32, Array, NativeIterable

def sum_iter(items: NativeIterable[Int32]) -> Int32:
    total: Int32 = 0
    for x in items:
        total += x
    return total

def print_all(items: NativeIterable[Int32]) -> None:
    for x in items:
        print(x)

def process_and_sum(items: NativeIterable[Int32]) -> Int32:
    # Test protocol-to-protocol passing: NativeIterable[T] -> NativeIterable[T]
    print_all(items)
    return sum_iter(items)

def nested_iteration(outer: NativeIterable[Int32], inner: NativeIterable[Int32]) -> Int32:
    # Test nested for loops over protocol-typed params
    total: Int32 = 0
    for x in outer:
        for y in inner:
            total += x * y
    return total

def contains_value(items: NativeIterable[Int32], target: Int32) -> bool:
    # Test "in" operator with NativeIterable-typed param
    return target in items

def main() -> None:
    nums: list[Int32] = [1, 2, 3]
    print(sum_iter(nums))  # 6

    arr: Array[Int32, 3] = [10, 20, 30]
    print(sum_iter(arr))   # 60

    more: Array[Int32, 2] = [100, 200]
    print(sum_iter(more))  # 300

    print_all(nums)  # 1, 2, 3

    # Test protocol-to-protocol passing
    print(process_and_sum(arr))  # prints 10, 20, 30 then 60

    # Test nested iteration
    a: list[Int32] = [1, 2]
    b: list[Int32] = [10, 20]
    print(nested_iteration(a, b))  # (1*10 + 1*20) + (2*10 + 2*20) = 30 + 60 = 90

    # Test "in" operator with NativeIterable
    print(contains_value(nums, 2))   # True
    print(contains_value(nums, 99))  # False

main()
