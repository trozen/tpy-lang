# Consuming for-loop with per-element move: when the loop variable is
# consumed (appended to a container) and the source is at last use,
# auto-consuming fires and elements are std::move'd at last use.
from tpy import int32

class Item:
    value: int32
    name: str
    def __init__(self, v: int32, n: str) -> None:
        self.value = v
        self.name = n

def main() -> None:
    items: list[Item] = [Item(1, "a"), Item(2, "b"), Item(3, "c")]
    result: list[Item] = []
    for x in items:
        result.append(x)
    for r in result:
        print(r.value, r.name)

main()
