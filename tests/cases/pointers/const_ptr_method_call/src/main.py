from tpy import Ptr, Int32, readonly

class Counter:
    x: Int32
    def __init__(self, x: Int32) -> None:
        self.x = x
    def __len__(self) -> Int32:
        return self.x

def main() -> None:
    c: Counter = Counter(42)
    cp: Ptr[readonly[Counter]] = Ptr(c)
    # Const-safe dunder method call through Ptr[readonly[...]] auto-deref
    print(cp.__len__())

main()
