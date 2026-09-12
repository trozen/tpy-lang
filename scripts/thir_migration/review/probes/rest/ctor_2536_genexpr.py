from tpy import int32
class H:
    t: tuple[int32, tuple[int32, str]]
    def __init__(self, d: dict[int32, int32]) -> None:
        self.t = (1, (sum(k for k in d), "a"))
def main() -> None:
    h = H({1: 2})
    print(h.t[0])
main()
