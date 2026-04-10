# Recursive type alias with isinstance narrowing (explicit else)
type Tree = int | list[Tree]


def depth(t: Tree) -> int:
    if isinstance(t, int):
        return 0
    else:
        result = 0
        for child in t:
            d = depth(child)
            if d > result:
                result = d
        return result + 1


def main() -> None:
    leaf: Tree = 1
    branch: Tree = [1, 2]
    inner: list[Tree] = [3, 4]
    nested: list[Tree] = [1, inner]
    print(depth(leaf))
    print(depth(branch))
    print(depth(nested))

main()
