# reversed() builtin over sequences
from tpy import int32

def main() -> None:
    items: list[int32] = [1, 2, 3, 4, 5]
    for x in reversed(items):
        print(x)

    # strings
    words: list[str] = ["a", "b", "c"]
    for s in reversed(words):
        print(s)

    # single element
    one: list[int32] = [42]
    for x in reversed(one):
        print(x)

    # empty
    empty: list[int32] = []
    for x in reversed(empty):
        print(x)

main()
