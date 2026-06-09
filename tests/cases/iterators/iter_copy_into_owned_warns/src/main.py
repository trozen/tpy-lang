# Regression guard: extending a list from a still-live source of reference
# elements copies them into owned storage and warns. The source list[Item] has
# no Own in its type args, so the Own-symmetric-strip suppression must not fire
# -- the warning still reports the copy.
from tpy import Int32

class Item:
    key: Int32
    def __init__(self, key: Int32) -> None:
        self.key = key

def main() -> None:
    a: list[Item] = []
    b: list[Item] = [Item(1), Item(2)]
    a.extend(b)  # tpyc: warning(/copies Item elements/)
    print(len(b))

main()
