from tpy import ConstPtr, Int32

class Counter:
    x: Int32
    def __init__(self, x: Int32) -> None:
        self.x = x
    def __len__(self) -> Int32:
        return self.x

def main() -> None:
    c: Counter = Counter(42)
    cp: ConstPtr[Counter] = ConstPtr(c)
    # Const-safe dunder method call through ConstPtr auto-deref
    print(cp.__len__())

main()
