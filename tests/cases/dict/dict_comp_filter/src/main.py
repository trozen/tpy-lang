# Dict comprehension: filter clause
from tpy import int32

def main() -> None:
    # Single condition
    evens: dict[int32, int32] = {x: x * x for x in range(10) if x % 2 == 0}
    for k in evens:
        print(k, evens[k])

    # Filter from existing dict
    src: dict[str, int32] = {"a": 1, "b": 5, "c": 2, "d": 8}
    big: dict[str, int32] = {k: v for k, v in src.items() if v > 3}
    for k in big:
        print(k, big[k])

main()
