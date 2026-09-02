from tpy import Int32
class H:
    t: tuple[Int32, tuple[Int32, str]]
    def __init__(self, d: dict[Int32, Int32]) -> None:
        self.t = (1, (sum(k for k in d), "a"))
def main() -> None:
    h = H({1: 2})
    print(h.t[0])
main()
