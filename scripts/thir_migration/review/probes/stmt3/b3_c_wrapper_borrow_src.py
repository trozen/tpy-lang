from tpy import int32, Own
type Tree = int32 | list[Tree]
def a(xs: list[Tree]) -> Tree:
    return xs[0]
def main() -> None:
    xs: list[Tree] = [1]
    print(1)
main()
