# Set comprehension: filter clause
from tpy import Int32

def main() -> None:
    # Single condition -- integer sets have deterministic order
    evens: set[Int32] = {x for x in range(10) if x % 2 == 0}
    for v in evens:
        print(v)

    # Filter from list -- check membership and size instead of iteration order
    words: list[str] = ["hello", "hi", "world", "hey", "wow"]
    long_words: set[str] = {w for w in words if len(w) > 2}
    print(len(long_words))
    print("hello" in long_words)
    print("hi" in long_words)

main()
