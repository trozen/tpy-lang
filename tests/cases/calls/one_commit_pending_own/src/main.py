# Subject: the warned copy of an `Own[list]` argument to a generic construction
# completed at a later use, received there as at a sole construction.
from tpy import int32, Own


class Bag[T]:
    names: list[int32]
    items: list[T]

    def __init__(self, names: Own[list[int32]]) -> None:
        self.names = names
        self.items = []

    def put(self, v: T) -> None:
        # Scaffolding: the element store's own generic copy warning.
        self.items.append(v)  # tpyc: warning(/may copy T into owned storage/)


def main() -> None:
    ns = [1]
    # Completed at `b.put(5)`: the copy of `ns` is warned.
    b = Bag(ns)  # tpyc: warning(/copies list\[int32\] into owned storage/)
    b.put(5)
    ns.append(2)
    print(b.names, ns, b.items)


main()
