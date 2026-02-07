from tpy import Int32, Bool, NativeIterable

def sum_ints(items: NativeIterable[Int32]) -> Int32:
    total: Int32 = 0
    for x in items:
        total += x
    return total

def process_bools(items: NativeIterable[Bool]) -> None:
    sum_ints(items)  # tpyc: error(/does not conform to protocol/)

def main() -> None:
    pass

main()
