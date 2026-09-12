# Set comprehension: annotation propagation (type coercion)
from tpy import int32, int64

def main() -> None:
    # int32 -> int64 widening via annotation
    items: list[int32] = [1, 2, 3]
    wide: set[int64] = {x for x in items}
    for v in wide:
        print(v)

    # int32 -> int (BigInt) widening via annotation
    big: set[int] = {x * x for x in range(4)}
    for v in big:
        print(v)

main()
