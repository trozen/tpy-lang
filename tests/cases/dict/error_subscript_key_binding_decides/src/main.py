# A use as a dict key decides nothing, but a binding does: once the list is
# bound to a list[int32] slot, a later wider store is refused there.
from tpy import int32, int64


def wide() -> int64:
    return 7


def main() -> None:
    d: dict[int32, str] = {1: "a", 2: "b"}
    xs = [1, 2]
    for x in xs:
        print("get", d.get(x, "none"))
    kept: list[int32] = xs
    # The binding above fixed the elements at int32, not the key use.
    xs.append(wide())  # tpyc: error(/'xs' holds int32 elements since line 15 \(bound as list\[int32\]\), and this value is int64/)
    print(len(kept))


main()
