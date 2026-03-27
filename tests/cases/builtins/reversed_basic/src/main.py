# reversed() builtin over sequences
from tpy import Int32

def main() -> None:
    items: list[Int32] = [1, 2, 3, 4, 5]
    for x in reversed(items):
        print(x)

    # strings
    words: list[str] = ["a", "b", "c"]
    for s in reversed(words):
        print(s)

    # single element
    one: list[Int32] = [42]
    for x in reversed(one):
        print(x)

    # empty
    empty: list[Int32] = []
    for x in reversed(empty):
        print(x)

main()
