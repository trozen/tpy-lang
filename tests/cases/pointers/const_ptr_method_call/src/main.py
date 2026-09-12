from tpy import Ptr, int32, readonly, take_ptr

class Counter:
    x: int32
    def __init__(self, x: int32) -> None:
        self.x = x
    def __len__(self) -> int32:
        return self.x

def main() -> None:
    c: Counter = Counter(42)
    cp: Ptr[readonly[Counter]] = take_ptr(c)
    # Const-safe dunder method call through Ptr[readonly[...]] auto-deref
    print(cp.__len__())

main()
