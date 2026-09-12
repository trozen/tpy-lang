from tpy import int32
def pick(s: str | None) -> str | None:
    return s
class H:
    s: str | None
    def __init__(self, s: str | None) -> None:
        self.s = pick(s)
def main() -> None:
    h = H("a")
    print(h.s is None)
main()
