# Recursive union: isinstance early-return narrows the variable for
# code after the if-block (iteration, len on the list member).
# Also covers elif chains with more than two alternatives.
from tpy import Int32


type Tree = Int32 | list[Tree]


def depth(t: Tree) -> Int32:
    if isinstance(t, Int32):
        return 0
    m: Int32 = 0
    for child in t:
        d = depth(child)
        if d > m:
            m = d
    return m + 1


def count(t: Tree) -> Int32:
    if isinstance(t, Int32):
        return 1
    total: Int32 = 0
    for child in t:
        total = total + count(child)
    return total


def leaf_sum(t: Tree) -> Int32:
    if isinstance(t, Int32):
        return t
    s: Int32 = 0
    for child in t:
        s = s + leaf_sum(child)
    return s


def child_count(t: Tree) -> Int32:
    if isinstance(t, Int32):
        return 0
    return len(t)


# isinstance guard inside an else block
def depth_nested(t: Tree, offset: Int32) -> Int32:
    if offset < 0:
        return 0
    else:
        if isinstance(t, Int32):
            return offset
        m: Int32 = 0
        for child in t:
            d = depth_nested(child, offset)
            if d > m:
                m = d
        return m + 1


# Three-member recursive union with elif chain
type Expr = Int32 | str | list[Expr]


def eval_expr(e: Expr) -> str:
    if isinstance(e, Int32):
        return str(e)
    elif isinstance(e, str):
        return e
    parts: list[str] = []
    for child in e:
        parts.append(eval_expr(child))
    return ", ".join(parts)


def main() -> None:
    leaf: Tree = 5
    branch: Tree = [1, 2, 3]
    nested: Tree = [1, [2, [3, 4]]]

    print(depth(leaf))
    print(depth(branch))
    print(depth(nested))

    print(count(leaf))
    print(count(branch))
    print(count(nested))

    print(leaf_sum(leaf))
    print(leaf_sum(branch))
    print(leaf_sum(nested))

    print(child_count(leaf))
    print(child_count(branch))

    print(depth_nested(branch, 1))
    print(depth_nested(leaf, 1))

    print(eval_expr(42))
    print(eval_expr([1, "two", [3]]))

main()
