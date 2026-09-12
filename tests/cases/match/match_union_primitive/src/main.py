# match/case on unions with primitive, container, and generic record patterns
from tpy import int32


class Cat:
    name: str
    def __init__(self, name: str) -> None:
        self.name = name


class Dog:
    name: str
    def __init__(self, name: str) -> None:
        self.name = name


# Primitive + record members
def describe(x: int32 | str | Cat | Dog) -> str:
    match x:
        case int32() as n:
            return "number: " + str(n)
        case str() as s:
            return "string: " + s
        case Cat(name=n):
            return "cat: " + n
        case Dog(name=n):
            return "dog: " + n


# Recursive union with list() pattern
type Tree = int32 | list[Tree]


def depth(t: Tree) -> int32:
    match t:
        case int32():
            return 0
        case list() as children:
            m: int32 = 0
            for child in children:
                d = depth(child)
                if d > m:
                    m = d
            return m + 1


# Parameterized record matched by bare name
from tpy import Own


class Box[T]:
    value: T
    def __init__(self, value: Own[T]) -> None:
        self.value = value


def unbox(x: int32 | Box[str]) -> str:
    match x:
        case int32() as n:
            return str(n)
        case Box() as b:
            return b.value


def main() -> None:
    a: int32 | str | Cat | Dog = 42
    b: int32 | str | Cat | Dog = "hello"
    c: int32 | str | Cat | Dog = Cat("Whiskers")
    d: int32 | str | Cat | Dog = Dog("Rex")
    print(describe(a))
    print(describe(b))
    print(describe(c))
    print(describe(d))

    leaf: Tree = 5
    branch: Tree = [1, 2, 3]
    nested: Tree = [1, [2, [3, 4]]]
    print(depth(leaf))
    print(depth(branch))
    print(depth(nested))

    e: int32 | Box[str] = 99
    f: int32 | Box[str] = Box("hello")
    print(unbox(e))
    print(unbox(f))

main()
