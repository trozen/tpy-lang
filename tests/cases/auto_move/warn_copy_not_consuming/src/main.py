# Copy warning is preserved when consuming doesn't activate
# (container lives past the loop).
from tpy import Int32

class Item:
    value: Int32
    def __init__(self, v: Int32) -> None:
        self.value = v

def main() -> None:
    items: list[Item] = [Item(1), Item(2)]
    result: list[Item] = []
    for x in items:
        result.append(x)  # tpyc: warning(/copies Item into owned storage/)
    print(len(items))

main()
