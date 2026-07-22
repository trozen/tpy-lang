# An elif condition mixing a walrus target with a temp-producing call arg:
# the walrus decl is flushed inside the else block, and v stays readable in
# the else branch (PEP 572 function scope).
from tpy import Int32


def take(items: list[Int32]) -> Int32:
    items.append(1)
    return len(items)


def test(x: Int32) -> Int32:
    if x < 0:
        return -1
    elif (v := take([10, 20])) == x:
        return v
    else:
        return -v


def main() -> None:
    print(test(-5))
    print(test(3))
    print(test(7))


main()
