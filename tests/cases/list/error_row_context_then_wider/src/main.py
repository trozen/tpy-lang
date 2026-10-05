# A typed container decides the row element of a nested list: a wider store
# into a row afterwards is refused naming the container.
from tpy import int32, int64


def a64() -> int64:
    return 1099511627776


def rows32(v: list[list[int32]]) -> None:
    print(len(v))


def main() -> None:
    g = [[1, 2], [3]]
    rows32(g)
    g[1].append(a64())  # tpyc: error(/'g' holds list\[int32\] elements since line 16 \(passed as list\[list\[int32\]\]\), and this value is int64 \(row element\); the list is passed as list\[list\[int32\]\], so the value must be int32/)
    print(g)


main()
