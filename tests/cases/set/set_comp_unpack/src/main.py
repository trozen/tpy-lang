# Set comprehension: tuple unpacking in generator
from tpy import int32

def main() -> None:
    # Extract keys from list of tuples (deterministic insertion order)
    pairs: list[tuple[str, int32]] = [("apple", 3), ("banana", 1), ("cherry", 5)]
    names: set[str] = {k for k, _ in pairs}
    print(len(names))
    print("apple" in names)
    print("banana" in names)
    print("cherry" in names)

    # Extract values with dedup
    pairs2: list[tuple[str, int32]] = [("a", 10), ("b", 20), ("c", 10)]
    vals: set[int32] = {v for _, v in pairs2}
    print(len(vals))
    for v in vals:
        print(v)

main()
