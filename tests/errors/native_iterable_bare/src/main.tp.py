from tpy import Int32, NativeIterable

def sum_iter(items: NativeIterable) -> Int32:  # tpyc: error(/requires type arguments/)
    total: Int32 = 0
    for x in items:
        total += x
    return total

def main() -> None:
    nums: list[Int32] = [1, 2, 3]
    print(sum_iter(nums))

main()
