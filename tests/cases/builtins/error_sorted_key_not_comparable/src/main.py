# sorted(key=) where key returns a non-Comparable type
from tpy import Int32

class Opaque:
    val: Int32
    def __init__(self, val: Int32) -> None:
        self.val = val

def main() -> None:
    items = [1, 2, 3]
    sorted(items, key=lambda x: Opaque(x))  # tpyc: error(/Comparable/)

main()
