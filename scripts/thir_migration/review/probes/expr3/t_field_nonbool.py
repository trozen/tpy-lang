from tpy import Int32, Char
class Holder:
    c: Char
    def __init__(self) -> None:
        self.c = Char('a')
def main() -> None:
    h = Holder()
    if h.c:
        print(1)
main()
