from tpy import int32, Own
type Tree = int32 | list[Tree]
def a() -> Own[Tree]:
    t: Tree = 1
    u: Tree = 2
    return t
def main() -> None:
    print(1)
main()
