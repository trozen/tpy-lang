# raise X(rec) with a `Record | None` ctor param routes through the shared
# arg-lowering loop (optional-ptr arm: &rec -> ptr_to_optional); copy intended.
class Node:
    v: int

    def __init__(self, v: int) -> None:
        self.v = v


class MyErr(Exception):
    node: Node | None

    def __init__(self, n: Node | None) -> None:
        super().__init__("boom")
        self.node = n


def main() -> None:
    n = Node(5)
    try:
        raise MyErr(n)
    except MyErr as e:
        if e.node is not None:
            print(e.node.v)


main()
