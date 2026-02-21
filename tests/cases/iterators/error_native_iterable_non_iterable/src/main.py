from tpy import Int32, NativeIterable

def sum_iter(items: NativeIterable[Int32]) -> Int32:
    total: Int32 = 0
    for x in items:
        total += x
    return total

def main() -> None:
    x: Int32 = 42
    sum_iter(x)  # tpyc: error(/does not conform to protocol/)

main()
