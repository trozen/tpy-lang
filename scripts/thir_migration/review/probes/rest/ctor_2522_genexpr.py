from tpy import int32
class H:
    t: tuple[int32, int32]
    def __init__(self, d: dict[int32, int32]) -> None:
        self.t = (sum(k for k in d), 1)
def main() -> None:
    h = H({1: 2})
    print(h.t[1])
main()
