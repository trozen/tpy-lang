from tpy import Int32
class H:
    t: tuple[Int32, Int32]
    def __init__(self, d: dict[Int32, Int32]) -> None:
        self.t = (sum(k for k in d), 1)
def main() -> None:
    h = H({1: 2})
    print(h.t[1])
main()
