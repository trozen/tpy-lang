# List comprehension: filter with if clause
from tpy import int32

def main() -> None:
    # Filter: keep only positive
    data: list[int32] = [3, -1, 4, -2, 5]
    pos = [x for x in data if x > 0]
    print(pos)

    # Filter with transformation
    evens = [x * x for x in range(10) if x % 2 == 0]
    print(evens)

    # Multiple conditions (all must be true)
    result = [x for x in range(20) if x % 2 == 0 if x % 3 == 0]
    print(result)

    # Filter strings by length
    words: list[str] = ["hi", "hello", "hey", "howdy", "yo"]
    short = [w for w in words if len(w) <= 3]
    print(short)

main()
