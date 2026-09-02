from tpy import Int32
type Tree = Int32 | list[Tree]
def main() -> None:
    xs: list[Tree] = [1, 2]
    xs[0] = [3, 4]
    print(len(xs))
main()
