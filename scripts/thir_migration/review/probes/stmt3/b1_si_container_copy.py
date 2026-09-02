from tpy import Int32, Own, Ptr, StrView, nocopy
class Box:
    n: Int32
    def __init__(self, n: Int32) -> None:
        self.n = n
def main() -> None:
    g: dict[str, list[Int32]] = {}
    a = [1, 2]
    g['a'] = a
    print(len(a))
main()
