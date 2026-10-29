# isinstance() takes any expression as its subject: the value is evaluated
# once, left to right, and tested; only a name or a walrus target narrows.
import asyncio
from typing import Protocol, Any, Iterator
from tpy import Own, dynamic, deref, readonly, error_return, ReturnException
from tplib.rc import Rc
from tplib.box import Box


@dynamic
class Pet(Protocol):
    def name(self) -> str: ...


class Dog(Pet):
    n: int

    def __init__(self) -> None:
        self.n = 0

    def name(self) -> str:
        return "dog"

    @readonly
    def bark(self) -> str:
        return "woof"


class Cat(Pet):
    def __init__(self) -> None:
        pass

    def name(self) -> str:
        return "cat"


class Bird:
    # A structural conformer of Pet: behind a Pet it sits in an adapter.
    def __init__(self) -> None:
        pass

    def name(self) -> str:
        return "bird"


@dynamic
class Tagged(Protocol):
    pass


class Animal(Tagged):
    def __init__(self) -> None:
        pass


class Hound(Animal):
    def __init__(self) -> None:
        super().__init__()

    @readonly
    def howl(self) -> str:
        return "awoo"


class Wolf(Animal):
    def __init__(self) -> None:
        super().__init__()


class Shape:
    def __init__(self) -> None:
        pass


class Square(Shape):
    def __init__(self) -> None:
        super().__init__()


class A:
    a: int

    def __init__(self, a: int) -> None:
        self.a = a


class B:
    b: int

    def __init__(self, b: int) -> None:
        self.b = b


type Tree = int | list[Tree]


class Shelf:
    xs: list[A | B]

    def __init__(self) -> None:
        self.xs = [A(1), B(2)]

    def first_is_a(self) -> bool:
        # method: an element of a field
        return isinstance(self.xs[0], A)  # tpyc: ok


class Probe:
    dog: bool

    def __init__(self, rc: Rc[Pet]) -> None:
        # constructor: a call result, not `self`
        self.dog = isinstance(deref(rc), Dog)  # tpyc: ok


class Ctx:
    def __init__(self) -> None:
        pass

    def __enter__(self) -> None:
        print("with: enter")

    def __exit__(self, exc_type: None, exc_val: None, exc_tb: None) -> None:
        print("with: exit")


class Missing(Exception, ReturnException):
    pass


def pick(tag: str, i: int) -> Own[A | B]:
    print(tag, "pick", i)
    if i % 2 == 0:
        return A(i)
    return B(i)


def make_a(tag: str) -> Own[A]:
    print(tag, "make_a")
    return A(5)


def make_box(tag: str) -> Own[Box[Pet]]:
    print(tag, "make_box")
    return Box(Dog())


def anything(v: int) -> Any:
    return v


def same[T](x: T) -> T:
    return x


def is_square[T: Shape](x: T) -> bool:
    # a type-parameter subject with no name
    return isinstance(same(x), Square)  # tpyc: ok


def howls(rc: Rc[Animal], s: str) -> bool:
    # a walrus target held as a pointer narrows the right side of `and`
    return isinstance(h := deref(rc), Hound) and h.howl() == s  # tpyc: ok


def chosen(xs: list[A | B], i: int) -> A | B:
    return xs[i]


class Holder:
    u: A | B

    def __init__(self) -> None:
        self.u = B(7)

    @property
    def item(self) -> A | B:
        return self.u


def ident(u: A | B) -> A | B:
    return u


def make_holder(tag: str) -> Own[Holder]:
    print(tag, "make_holder")
    return Holder()


def make_animal(tag: str) -> Own[Animal]:
    print(tag, "make_animal")
    return Animal()


def anys(tag: str) -> Own[list[Any]]:
    print(tag, "anys")
    return [1, "s"]


def keyless(u: A | B) -> bool:  # tpyc: mir(uncovered /missing or invalid union test/)
    # a keyless union test states no selection fact: MIR refuses it explicitly
    return isinstance(ident(u), A)  # tpyc: ok


def is_odd(i: int) -> bool:
    return i % 2 == 1


def first_of(xs: readonly[list[A | B]]) -> bool:
    # const source: an element of a readonly parameter
    return isinstance(xs[0], B)  # tpyc: ok


def free_function() -> None:
    rc: Rc[Pet] = Rc.new(Dog())
    # the object a handle holds, tested through deref()
    held = isinstance(deref(rc), Dog)  # tpyc: ok
    print("free: deref", held)
    # a walrus target narrows; the write through it reaches rc's object
    if isinstance(p := deref(rc), Dog):  # tpyc: ok
        p.n += 1
    if isinstance(q := rc.get(), Dog):  # tpyc: ok
        print("free: owner", q.n, q.bark())
    # an inheritance base held behind its pointer
    hound: Rc[Animal] = Rc.new(Hound())
    if isinstance(h := deref(hound), Hound):  # tpyc: ok
        print("free: inherit", h.howl())
    # a type parameter
    print("free: tparam", is_square(Square()), is_square(Shape()))
    # a pointer-variant result
    xs: list[A | B] = [A(1), B(2)]
    print("free: ptr variant", isinstance(chosen(xs, 0), A), isinstance(chosen(xs, 1), A))  # tpyc: ok
    # name subjects in a value position: a borrow of a handle's object, Any
    pn = deref(rc)
    av: Any = anything(4)
    print("free: name value", isinstance(pn, (Dog, Cat)), isinstance(av, int))  # tpyc: ok
    # a structural conformer behind the protocol
    bird: Rc[Pet] = Rc.new(Bird())
    print("free: structural", isinstance(deref(bird), Bird))  # tpyc: ok
    # static answers keep their subject's evaluation
    folded = isinstance(make_a("free:"), A)  # tpyc: ok
    built = isinstance(A(1), A)  # tpyc: ok
    print("free: fold", folded, built)
    boxed = isinstance(make_box("free:"), Dog)  # tpyc: warning(/use isinstance\(p := deref\(\.\.\.\), Dog\)/)
    print("free: handle", boxed)
    # Any and a recursive-alias union
    print("free: any", isinstance(anything(4), int), isinstance(anything(4), str))  # tpyc: ok
    trees: list[Tree] = [3, [4]]
    print("free: tree", isinstance(trees[0], int), isinstance(trees[1], int))  # tpyc: ok
    # value position, `not`, ternary
    v = isinstance(pick("free:", 1), A)  # tpyc: ok
    print("free: value", v)
    n = not isinstance(pick("free:", 2), A)  # tpyc: ok
    print("free: not", n)
    s = "a" if isinstance(pick("free:", 4), A) else "b"  # tpyc: ok
    print("free: ternary", s)
    # `and` / `or` skip their right operand's evaluation
    both = isinstance(pick("free:", 1), A) and isinstance(pick("free:", 3), A)  # tpyc: ok
    print("free: and", both)
    either = isinstance(pick("free:", 2), A) or isinstance(pick("free:", 3), A)  # tpyc: ok
    print("free: or", either)
    # `while` re-evaluates its subject each pass
    i = 0
    while isinstance(pick("free:", i), A):  # tpyc: ok
        i += 2
        if i > 4:
            break
    print("free: while", i)
    assert isinstance(pick("free:", 0), A)  # tpyc: ok
    print("free: const", first_of([B(1)]))
    # a union field, bound as the pointer-variant local `u = h.u` is
    hd = Holder()
    print("free: field", isinstance(hd.u, A), isinstance(hd.u, B))  # tpyc: ok
    k = chosen(xs, 0)
    print("free: keyless", keyless(k))


def temp_owner() -> None:
    # subjects borrowing from a temporary are tested before it dies
    held = isinstance(make_box("temp:").get(), Dog)  # tpyc: ok
    either = isinstance(make_box("temp:").get(), (Cat, Dog))  # tpyc: ok
    print("temp: poly", held, either)
    a = isinstance(make_holder("temp:").u, A)  # tpyc: ok
    ab = isinstance(make_holder("temp:").u, (A, B))  # tpyc: ok
    print("temp: union", a, ab)
    i = isinstance(anys("temp:")[0], int)  # tpyc: ok
    s = isinstance(anys("temp:")[1], (int, str))  # tpyc: ok
    print("temp: any", i, s)
    # a fresh polymorphic value: the test takes no address of it
    h = isinstance(make_animal("temp:"), Hound)  # tpyc: ok
    hw = isinstance(make_animal("temp:"), (Hound, Wolf))  # tpyc: ok
    print("temp: fresh", h, hw)
    # a union property read off a temporary
    p = isinstance(make_holder("temp:").item, B)  # tpyc: ok
    print("temp: property", p)
    # a borrow of a temporary under `and`, `or` and a ternary
    both = isinstance(make_box("temp:").get(), Dog) and isinstance(make_holder("temp:").u, B)  # tpyc: ok
    either = isinstance(make_box("temp:").get(), Cat) or isinstance(make_holder("temp:").item, A)  # tpyc: ok
    t = "cat" if isinstance(make_box("temp:").get(), Cat) else "dog"  # tpyc: ok
    print("temp: logic", both, either, t)


def walrus_forms() -> None:
    rc: Rc[Pet] = Rc.new(Dog())
    # a walrus subject in a value position binds, then tests its target
    print("walrus: value", isinstance(p := deref(rc), Dog), p.name())  # tpyc: ok
    w = isinstance(r := deref(rc), Dog)  # tpyc: ok
    print("walrus: assigned", w, r.name())
    print("walrus: tuple value", isinstance(t := deref(rc), (Dog, Cat)), t.name())  # tpyc: ok
    # a tuple classinfo narrows the walrus target to the union of its members
    if isinstance(q := deref(rc), (Dog, Cat)):  # tpyc: ok
        print("walrus: tuple", q.name())
        if isinstance(q, Dog):
            q.n += 3
    # a static answer still runs the walrus
    f = isinstance(m := make_a("walrus:"), A)  # tpyc: ok
    print("walrus: fold", f, m.a)
    # a static False still runs the walrus once, and binds its target
    g = isinstance(m2 := make_a("walrus: false"), B)  # tpyc: ok
    m2.a += 1
    print("walrus: fold false", g, m2.a)
    # the write through `q` reached the object rc holds
    print("walrus: owner", isinstance(o := deref(rc), Dog), deref(rc).name())
    if isinstance(d := deref(rc), Dog):  # tpyc: ok
        print("walrus: owner n", d.n)
    hound: Rc[Animal] = Rc.new(Hound())
    animal: Rc[Animal] = Rc.new(Animal())
    print("walrus: and", howls(hound, "awoo"), howls(hound, "x"),
          howls(animal, "awoo"),
          isinstance(a2 := deref(hound), Hound) and a2.howl() == "awoo")  # tpyc: ok


def call_in_subject() -> None:
    xs: list[A | B] = [A(3), B(4)]
    u = chosen(xs, 0)
    # a user call in the second subject leaves the name fact as a bool call does
    if isinstance(u, A) and isinstance(pick("kill:", 1), B):  # tpyc: ok
        print("kill: subject", u.a)
    if isinstance(u, A) and is_odd(1):
        print("kill: twin", u.a)


def union_gen(u: A | B, v: A | B) -> Iterator[int]:
    # generator: a union name narrows across a `yield`, and after an assert
    if isinstance(u, A):  # tpyc: ok
        yield 1
        yield u.a
    assert isinstance(v, B)  # tpyc: ok
    yield v.b


def tparam_gen[T: Shape](x: T) -> Iterator[bool]:
    # generator: a type-parameter walrus is tested by its value's type
    if isinstance(y := same(x), Square):  # tpyc: ok
        yield True
    else:
        yield False
    yield isinstance(z := same(x), Square)  # tpyc: ok
    w = same(x)
    yield isinstance(w, Square)  # tpyc: ok


def method_and_ctor() -> None:
    print("method:", Shelf().first_is_a())
    rc: Rc[Pet] = Rc.new(Cat())
    print("ctor:", Probe(rc).dog)


def gen(rc: Rc[Pet]) -> Iterator[bool]:
    # generator: the subject is evaluated again after each resume
    yield isinstance(deref(rc), Dog)  # tpyc: ok
    if isinstance(p := deref(rc), Dog):  # tpyc: ok
        p.n += 10
    yield isinstance(deref(rc), Cat)  # tpyc: ok


async def fetch(i: int) -> int:
    print("async: fetch", i)
    return i


async def coro() -> None:
    # async: the subject awaits, and a static answer still awaits it
    first = isinstance(await fetch(0), int)  # tpyc: ok
    second = isinstance(await fetch(1), str)  # tpyc: ok
    print("async:", first, second)


def comprehension() -> None:
    # element and filter positions
    flags = [isinstance(pick("comp:", k), A) for k in range(3)]  # tpyc: ok
    print("comp:", flags)
    odd = [k for k in range(3) if isinstance(pick("comp:", k), B)]  # tpyc: ok
    print("comp:", odd)


def closure() -> None:
    rc: Rc[Pet] = Rc.new(Dog())

    def inner() -> bool:
        # closure: the subject reads a captured handle
        return isinstance(deref(rc), Dog)  # tpyc: ok

    print("closure:", inner())


def with_body() -> None:
    with Ctx():
        r = isinstance(pick("with:", 3), B)  # tpyc: ok
        print("with:", r)


def try_finally() -> None:
    try:
        r = isinstance(pick("try:", 2), B)  # tpyc: ok
        print("try:", r)
    finally:
        r2 = isinstance(make_a("finally:"), A)  # tpyc: ok
        print("finally:", r2)


@error_return(Missing)
def checked(i: int) -> bool:
    # @error_return body
    if isinstance(pick("error_return:", i), B):  # tpyc: ok
        raise Missing
    return True


def match_arm(k: int) -> None:
    match k:
        case 1:
            # match arm
            r = isinstance(pick("match:", k), B)  # tpyc: ok
            print("match:", r)
        case _:
            print("match: other")


def main() -> None:
    free_function()
    temp_owner()
    walrus_forms()
    call_in_subject()
    print("gen: union", list(union_gen(A(3), B(4))))
    print("gen: tparam", list(tparam_gen(Square())), list(tparam_gen(Shape())))
    method_and_ctor()
    rc: Rc[Pet] = Rc.new(Dog())
    for x in gen(rc):
        print("gen:", x)
    if isinstance(d := deref(rc), Dog):  # tpyc: ok
        print("gen: owner", d.n)
    asyncio.run(coro())
    comprehension()
    closure()
    with_body()
    try_finally()
    try:
        ok = checked(0)
        print("error_return:", ok)
        ok = checked(1)
        print("error_return:", ok)
    except Missing:
        print("error_return: missing")
    match_arm(1)


main()
# module level
at_module = isinstance(make_a("module:"), A)  # tpyc: ok
print("module:", at_module)
