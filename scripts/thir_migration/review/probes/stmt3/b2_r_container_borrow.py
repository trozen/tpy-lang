from tpy import Int32, Own, Ptr, StrView
class Box:
    n: Int32
    def __init__(self, n: Int32) -> None:
        self.n = n
def a(d: dict[str, list[Int32]]) -> list[Int32]:
    return d['k']
def main() -> None:
    d: dict[str, list[Int32]] = {}
    d['k'] = [1]
    print(len(a(d)))
main()
