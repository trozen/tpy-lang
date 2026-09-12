from tpy import int32, Own, Ptr, StrView, nocopy
class Box:
    n: int32
    def __init__(self, n: int32) -> None:
        self.n = n
def make() -> tuple[int32, tuple[int32, int32]]:
    return (1, (2, 3))
def main() -> None:
    d: dict[int32, tuple[int32, tuple[int32, int32]]] = {}
    d[1] = make()
    print(len(d))
main()
