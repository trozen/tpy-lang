from tpy import int32, Own, Ptr, StrView, nocopy
class Box:
    n: int32
    def __init__(self, n: int32) -> None:
        self.n = n
def main() -> None:
    g: dict[str, list[int32]] = {}
    a = [1, 2]
    g['a'] = a
    print(len(a))
main()
