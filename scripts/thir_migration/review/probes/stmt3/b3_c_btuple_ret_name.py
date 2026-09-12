from tpy import int32, Own, Ptr, StrView, char, Array
class Box:
    n: int32
    def __init__(self, n: int32) -> None:
        self.n = n
def a(d: dict[int32, tuple[int32, Box]]) -> tuple[int32, Box]:
    return d[1]
def main() -> None:
    print(1)
main()
