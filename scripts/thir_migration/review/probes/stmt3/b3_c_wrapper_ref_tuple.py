from tpy import int32, Own
type Tree = int32 | list[Tree]
def a(xs: list[Tree], n: int32) -> tuple[Tree, int32]:
    return (xs[0], n)
def main() -> None:
    print(1)
main()
