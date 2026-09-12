from tpy import int32
type Tree = int32 | list[Tree]
def f(u: Tree) -> None:
    if isinstance(u, list):
        u[0] = 5
def main() -> None:
    t: Tree = [1, 2]
    f(t)
    if isinstance(t, list):
        print(len(t))
main()
