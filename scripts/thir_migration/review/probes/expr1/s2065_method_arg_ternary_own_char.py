from tpy import char, Own, int32
class Sink:
    n: int32
    def __init__(self) -> None:
        self.n = 0
    def put(self, c: Own[char]) -> None:
        self.n += 1
def pick(s: Sink, flag: bool, a: char, b: char) -> None:
    s.put(a if flag else b)
def main() -> None:
    pick(Sink(), True, char('a'), char('b'))
main()
