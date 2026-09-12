# Dict comprehension: annotation propagation (type coercion)
from tpy import int32, int64

def main() -> None:
    # int32 -> int (BigInt) widening via annotation
    items: list[int32] = [1, 2, 3]
    widened: dict[int, int] = {x: x * x for x in items}
    for k in widened:
        print(k, widened[k])

    # int32 -> int64 widening via annotation
    wide64: dict[int32, int64] = {x: x * 2 for x in range(3)}
    for k in wide64:
        print(k, wide64[k])

main()
