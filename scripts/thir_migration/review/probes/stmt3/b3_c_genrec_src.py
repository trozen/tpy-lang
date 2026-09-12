from tpy import int32, Own
type Tree[T] = T | list[Tree[T]]
def a() -> Own[Tree[int32]]:
    t: Tree[int32] = [1, 2]
    u: Tree[int32] = 1
    return t
def main() -> None:
    print(1)
main()
