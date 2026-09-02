from tpy import Int32, Own
type Tree = Int32 | list[Tree]
def a(xs: list[Tree], n: Int32) -> tuple[Tree, Int32]:
    return (xs[0], n)
def main() -> None:
    print(1)
main()
