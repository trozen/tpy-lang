# Set comprehension: annotation propagation (type coercion)
from tpy import Int32, Int64

def main() -> None:
    # Int32 -> Int64 widening via annotation
    items: list[Int32] = [1, 2, 3]
    wide: set[Int64] = {x for x in items}
    for v in wide:
        print(v)

    # Int32 -> int (BigInt) widening via annotation
    big: set[int] = {x * x for x in range(4)}
    for v in big:
        print(v)

main()
