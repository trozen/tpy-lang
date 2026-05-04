# Edge cases for nested type sub-patterns in match/case
from tpy import Int32, Own
from dataclasses import dataclass


@dataclass
class Cat:
    name: str

@dataclass
class Dog:
    name: str


class Wrapper:
    pet: Cat | Dog
    def __init__(self, pet: Cat | Dog) -> None:
        self.pet = pet


class Box[T]:
    value: T
    def __init__(self, value: Own[T]) -> None:
        self.value = value


# --- Optional subject with union field guard ---
def opt_wrapper(w: Wrapper | None) -> str:
    match w:
        case None:
            return "none"
        case Wrapper(pet=Cat() as c):
            return "cat: " + c.name
        case Wrapper(pet=Dog() as d):
            return "dog: " + d.name
        case _:
            return "unknown"


# --- Or-pattern with union field guards on same variant ---
class Tag:
    label: str
    def __init__(self, label: str) -> None:
        self.label = label


def or_nested(x: Wrapper | Tag) -> str:
    match x:
        case Wrapper(pet=Cat(name=n)) | Wrapper(pet=Dog(name=n)):
            return "pet: " + n
        case Tag(label=l):
            return "tag: " + l
    return ""  # unreachable in practice; sema flags the match as non-exhaustive


# --- 3 levels deep (type-param disambiguation) ---
def deep3(x: Box[Box[Box[str]]] | Box[Box[Box[Int32]]]) -> str:
    match x:
        case Box(value=Box(value=Box(value=str() as v))):
            return "string: " + v
        case Box(value=Box(value=Box(value=Int32() as n))):
            return "number: " + str(n)


# --- Positional nested patterns ---
def positional_nested(x: Box[str] | Box[Int32]) -> str:
    match x:
        case Box(str() as v):
            return "string: " + v
        case Box(Int32() as n):
            return "number: " + str(n)


# --- Nested positional field extraction on union field ---
def nested_pos_extract(w: Wrapper) -> str:
    match w:
        case Wrapper(pet=Cat(n)):
            return "cat: " + n
        case Wrapper(pet=Dog(n)):
            return "dog: " + n
        case _:
            return "other"


# --- Guard clause + union field guard ---
def guard_combo(w: Wrapper) -> str:
    match w:
        case Wrapper(pet=Cat(name=n)) if n == "Luna":
            return "special cat"
        case Wrapper(pet=Cat(name=n)):
            return "cat: " + n
        case Wrapper(pet=Dog(name=n)):
            return "dog: " + n
        case _:
            return "other"


# --- Primitive types as union field sub-patterns (float, bool) ---
class FloatHolder:
    value: float | str
    def __init__(self, value: float | str) -> None:
        self.value = value


class BoolHolder:
    value: bool | str
    def __init__(self, value: bool | str) -> None:
        self.value = value


def check_float(h: FloatHolder) -> str:
    match h:
        case FloatHolder(value=float() as f):
            return "float: " + str(f)
        case FloatHolder(value=str() as s):
            return "str: " + s
        case _:
            return "other"


def check_bool(h: BoolHolder) -> str:
    match h:
        case BoolHolder(value=bool() as b):
            if b:
                return "true"
            return "false"
        case BoolHolder(value=str() as s):
            return "str: " + s
        case _:
            return "other"


def main() -> None:
    # Optional
    a: Wrapper | None = Wrapper(Cat("Luna"))
    b: Wrapper | None = Wrapper(Dog("Rex"))
    c: Wrapper | None = None
    print(opt_wrapper(a))
    print(opt_wrapper(b))
    print(opt_wrapper(c))

    # Or-pattern
    d: Wrapper | Tag = Wrapper(Cat("Luna"))
    e: Wrapper | Tag = Wrapper(Dog("Rex"))
    f: Wrapper | Tag = Tag("hello")
    print(or_nested(d))
    print(or_nested(e))
    print(or_nested(f))

    # 3-deep
    g: Box[Box[Box[str]]] | Box[Box[Box[Int32]]] = Box(Box(Box("abc")))
    h: Box[Box[Box[str]]] | Box[Box[Box[Int32]]] = Box(Box(Box(Int32(99))))
    print(deep3(g))
    print(deep3(h))

    # Positional
    i: Box[str] | Box[Int32] = Box("pos")
    j: Box[str] | Box[Int32] = Box(Int32(7))
    print(positional_nested(i))
    print(positional_nested(j))

    # Nested positional extraction
    print(nested_pos_extract(Wrapper(Cat("Nala"))))
    print(nested_pos_extract(Wrapper(Dog("Buddy"))))

    # Guard + union field
    print(guard_combo(Wrapper(Cat("Luna"))))
    print(guard_combo(Wrapper(Cat("Nala"))))
    print(guard_combo(Wrapper(Dog("Rex"))))

    # Primitive types: float, bool
    print(check_float(FloatHolder(3.14)))
    print(check_float(FloatHolder("pi")))
    print(check_bool(BoolHolder(True)))
    print(check_bool(BoolHolder(False)))
    print(check_bool(BoolHolder("yes")))

main()
