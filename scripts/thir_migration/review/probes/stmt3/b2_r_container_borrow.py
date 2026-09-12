from tpy import int32, Own, Ptr, StrView
class Box:
    n: int32
    def __init__(self, n: int32) -> None:
        self.n = n
def a(d: dict[str, list[int32]]) -> list[int32]:
    return d['k']
def main() -> None:
    d: dict[str, list[int32]] = {}
    d['k'] = [1]
    print(len(a(d)))
main()
