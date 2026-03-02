# For-loop tuple unpacking with different type annotation patterns
from tpy import Int32

def main() -> None:
    # Explicit annotation, bare literals coerced
    items: list[tuple[Int32, str]] = [(1, "one"), (2, "two")]
    for n, s in items:
        print(n, s)

    # Inferred from typed constructors
    pairs = [(Int32(10), True), (Int32(20), False)]
    for n, flag in pairs:
        print(n, flag)

    # Tuple of two strings, fully inferred
    names = [("Alice", "A"), ("Bob", "B")]
    for full, initial in names:
        print(full, initial)

main()
