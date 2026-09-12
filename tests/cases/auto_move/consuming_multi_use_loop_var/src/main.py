# Consuming loop with multiple uses of the loop variable: only the last
# use is moved, earlier uses still copy and should keep their warning.
from tpy import int32

class Item:
    value: int32
    def __init__(self, v: int32) -> None:
        self.value = v

def main() -> None:
    items: list[Item] = [Item(1), Item(2), Item(3)]
    first: list[Item] = []
    second: list[Item] = []
    for x in items:
        first.append(x)  # tpyc: warning(/copies Item into owned storage/)
        second.append(x)  # tpyc: ok
    for r in second:
        print(r.value)

main()
