from tpy import Int32, StrView
def f(t: tuple[StrView, Int32]) -> Int32:
    return t[1]
def main() -> None:
    print(f(("a", 2)))
main()
