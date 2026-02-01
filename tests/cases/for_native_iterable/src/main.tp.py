from tpy import Int32, Array, StaticList, NativeIterable

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

def main() -> None:
    nums: list[Int32] = [1, 2, 3]
    print(sum_iter(nums))  # 6

    arr: Array[Int32, 3] = [10, 20, 30]
    print(sum_iter(arr))   # 60

    sl: StaticList[Int32, 8] = StaticList[Int32, 8]()
    sl.append(100)
    sl.append(200)
    print(sum_iter(sl))    # 300

    print_all(nums)  # 1, 2, 3

    # Test protocol-to-protocol passing
    print(process_and_sum(arr))  # prints 10, 20, 30 then 60

    # Test nested iteration
    a: list[Int32] = [1, 2]
    b: list[Int32] = [10, 20]
    print(nested_iteration(a, b))  # (1*10 + 1*20) + (2*10 + 2*20) = 30 + 60 = 90

main()
