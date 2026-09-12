from tpy import int32, StrView
def f(t: tuple[StrView, int32]) -> int32:
    return t[1]
def main() -> None:
    print(f(("a", 2)))
main()
