# Nesting combinations for type sub-patterns in match/case
from tpy import Int32, Own


class Box[T]:
    value: T
    def __init__(self, value: Own[T]) -> None:
        self.value = value


# --- Case 1: type-param x type-param (two levels of generic disambiguation) ---
def nested_param(x: Box[Box[str]] | Box[Box[Int32]]) -> str:
    match x:
        case Box(value=Box(value=str() as v)):
            return "string: " + v
        case Box(value=Box(value=Int32() as n)):
            return "number: " + str(n)


# --- Case 2: union subject + union-typed field ---
class Container:
    value: str | Int32
    def __init__(self, value: str | Int32) -> None:
        self.value = value


def union_subj_union_field(x: Int32 | Container) -> str:
    match x:
        case Int32() as n:
            return "bare: " + str(n)
        case Container(value=str() as s):
            return "string: " + s
        case Container(value=Int32() as n):
            return "number: " + str(n)


# --- Case 3: union-field x type-param (record field is parameterized union) ---
class Outer:
    item: Box[str] | Box[Int32]
    def __init__(self, item: Box[str] | Box[Int32]) -> None:
        self.item = item  # tpyc: warning(/copies/)


def union_field_param(o: Outer) -> str:
    match o:
        case Outer(item=Box(value=str() as v)):
            return "string: " + v
        case Outer(item=Box(value=Int32() as n)):
            return "number: " + str(n)
        case _:
            return "other"


# --- Case 4: union-field x union-field (value-type unions) ---
class Tagged:
    label: str
    inner: str | Int32
    def __init__(self, label: str, inner: str | Int32) -> None:
        self.label = label
        self.inner = inner


def double_union(items: list[Tagged]) -> None:
    for t in items:
        match t:
            case Tagged(label="s", inner=str() as s):
                print("string: " + s)
            case Tagged(label="n", inner=Int32() as n):
                print("number: " + str(n))
            case _:
                print("other")


def main() -> None:
    # Case 1
    a1: Box[Box[str]] | Box[Box[Int32]] = Box(Box("hello"))
    a2: Box[Box[str]] | Box[Box[Int32]] = Box(Box(Int32(42)))
    print(nested_param(a1))
    print(nested_param(a2))

    # Case 2
    b1: Int32 | Container = Container("world")
    b2: Int32 | Container = Container(Int32(7))
    b3: Int32 | Container = 99
    print(union_subj_union_field(b1))
    print(union_subj_union_field(b2))
    print(union_subj_union_field(b3))

    # Case 3
    c1 = Outer(Box("abc"))
    c2 = Outer(Box(Int32(10)))
    print(union_field_param(c1))
    print(union_field_param(c2))

    # Case 4
    items: list[Tagged] = [
        Tagged("s", "hi"),
        Tagged("n", Int32(5)),
        Tagged("?", "x"),
    ]
    double_union(items)

main()
