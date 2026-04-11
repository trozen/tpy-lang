# Annotation-driven literal inference for recursive union types
# Nested list/dict literals infer element types from the target annotation
from dataclasses import dataclass
from tpy import Int32

@dataclass
class Leaf:
    value: Int32

type Tree = Leaf | list[Tree]

# Primitive variant
type IntTree = int | list[IntTree]

# Dict variant
type JsonValue = str | int | dict[str, JsonValue]


def depth(t: Tree) -> Int32:
    if isinstance(t, Leaf):
        return 0
    else:
        result = 0
        for child in t:
            d = depth(child)
            if d > result:
                result = d
        return result + 1


def int_depth(t: IntTree) -> int:
    if isinstance(t, int):
        return 0
    else:
        result = 0
        for child in t:
            d = int_depth(child)
            if d > result:
                result = d
        return result + 1


def json_keys(v: JsonValue) -> int:
    if isinstance(v, str):
        return 0
    elif isinstance(v, int):
        return 0
    else:
        result = 0
        for k in v:
            result = result + 1
        return result


def main() -> None:
    # Record variant
    x: Tree = [Leaf(1), [Leaf(2), Leaf(3)]]
    print(depth(x))

    y: Tree = [Leaf(1), [Leaf(2), [Leaf(3)]]]
    print(depth(y))

    # list[Tree] annotation
    zs: list[Tree] = [Leaf(1), [Leaf(2), Leaf(3)]]
    print(depth(zs[1]))

    # Function argument
    print(depth([Leaf(10), [Leaf(20), Leaf(30)]]))
    print(depth([Leaf(1), [Leaf(2), [Leaf(3), Leaf(4)]]]))

    # Primitive variant
    a: IntTree = [1, [2, 3]]
    print(int_depth(a))

    b: IntTree = [1, [2, [3]]]
    print(int_depth(b))

    print(int_depth([10, [20, 30]]))

    # Dict variant
    d: JsonValue = {"a": 1, "b": {"c": 2, "d": 3}}
    print(json_keys(d))

    print(json_keys({"x": 1, "y": {"z": 2}}))

main()
