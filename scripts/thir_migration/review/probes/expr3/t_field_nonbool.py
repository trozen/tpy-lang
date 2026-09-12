from tpy import int32, char
class Holder:
    c: char
    def __init__(self) -> None:
        self.c = char('a')
def main() -> None:
    h = Holder()
    if h.c:
        print(1)
main()
