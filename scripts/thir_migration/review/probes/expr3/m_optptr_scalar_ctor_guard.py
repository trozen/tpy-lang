from tpy import Int32
class Container[T]:
    _val: T | None
    def __init__(self, val: T | None):
        self._val = val
    def probe(self, val: T | None) -> bool:
        return val is not None
def main() -> None:
    k = 1
    match k:
        case 1 if Container[Int32](Int32(42)).probe(None):
            print("a")
        case _:
            print("b")
main()
