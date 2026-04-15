# Recursive union alias combined with Optional (T | None)
# Tests: is None / is not None narrowing, isinstance after narrowing,
# return T | None, and passing T to T | None parameters.
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


def maybe_depth(t: Tree | None) -> int:
    if t is None:
        return -1
    return depth(t)


def show(t: Tree | None) -> None:
    if t is not None:
        print(depth(t))
    else:
        print("none")


def first_or_none(items: list[Tree]) -> Tree | None:
    if len(items) == 0:
        return None
    return items[0]


def get_depth_or_default(t: Tree | None, default: int) -> int:
    if t is None:
        return default
    result = depth(t)
    return result


def describe(t: Tree | None) -> str:
    if t is None:
        return "nothing"
    if isinstance(t, int):
        return "leaf"
    return "branch"


def main() -> None:
    leaf: Tree = 42
    branch: Tree = [1, [2, 3]]

    print(maybe_depth(leaf))
    print(maybe_depth(branch))
    print(maybe_depth(None))

    show(leaf)
    show(None)

    print(get_depth_or_default(branch, 99))
    print(get_depth_or_default(None, 99))

    print(describe(leaf))
    print(describe(branch))
    print(describe(None))

    items: list[Tree] = [branch, leaf]
    r = first_or_none(items)
    print(maybe_depth(r))
    print(maybe_depth(first_or_none([])))

main()
