from tpy import Int32, Own
type Tree[T] = T | list[Tree[T]]
def a() -> Own[Tree[Int32]]:
    t: Tree[Int32] = [1, 2]
    u: Tree[Int32] = 1
    return t
def main() -> None:
    print(1)
main()
