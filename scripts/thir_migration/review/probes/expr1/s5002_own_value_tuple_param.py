from tpy import Int32, Own
def f(p: Own[tuple[Int32, Int32]]) -> Int32:
    return p[0]
def main() -> None:
    print(f((1, 2)))
main()
