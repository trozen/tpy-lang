# Generator expressions with builtin operations: list/set/dict constructors, extend, join.
from tpy import int32

def main() -> None:
    # list() from genexpr
    squares: list[int32] = list(x * x for x in range(5))
    print(squares)

    # list.extend with genexpr
    items: list[int32] = [1, 2, 3]
    items.extend(x * 10 for x in range(3))
    print(items)

    # set() from genexpr
    mods: set[int32] = set(x % 3 for x in range(10))
    print(mods)

    # dict() from genexpr of tuples
    d: dict[str, int32] = dict((str(x), x * x) for x in range(4))
    print(d)

    # str.join with genexpr
    words: list[str] = ["hello", "world", "test"]
    print(" ".join(w.upper() for w in words))

main()
