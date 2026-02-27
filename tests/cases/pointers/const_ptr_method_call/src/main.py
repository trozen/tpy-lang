from tpy import ReadOnlyPtr, Int32

class Counter:
    x: Int32
    def __init__(self, x: Int32) -> None:
        self.x = x
    def __len__(self) -> Int32:
        return self.x

def main() -> None:
    c: Counter = Counter(42)
    cp: ReadOnlyPtr[Counter] = ReadOnlyPtr(c)
    # Const-safe dunder method call through ReadOnlyPtr auto-deref
    print(cp.__len__())

main()
