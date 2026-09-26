# A container name still used after `g[k] = y` cannot feed an `Own[V]`
# `__setitem__` slot, which takes a moved value; `g[k] = copy(y)` spells the copy.
from tpy import Own, int32


class Store:
    data: dict[str, list[int32]]

    def __init__(self) -> None:
        self.data = {}

    def __getitem__(self, key: str) -> list[int32]:
        return self.data[key]

    def __setitem__(self, key: str, value: Own[list[int32]]) -> None:
        self.data[key] = value


def main() -> None:
    g = Store()
    y: list[int32] = [9]
    g["d"] = y  # tpyc: warning(/copies list\[int32\] into container/) error(/not yet supported by C\+\+ code generation/)
    y.append(1)
    print(g.data["d"], y)


main()
