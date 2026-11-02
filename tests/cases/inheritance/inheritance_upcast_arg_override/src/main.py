# Defining a method that hides a parent method emits a warning at the child
# class definition. Static dispatch means parent-typed references will call
# the parent's method, not the child's -- differs from Python. The call site
# warns at a base parameter, a Ptr[Base] one included, and a child
# implementing a @dynamic protocol its base does not still dispatches
# statically through the base. A callable whose parameter takes the base,
# passed where its slot hands it the subclass, warns the same at the
# argument, one section per position the callable is handed over at.
import asyncio
from typing import Callable, Iterator, Protocol
from tpy import Fn, Ptr, dispatch, dynamic, readonly

class Animal:
    name: str
    def __init__(self, name: str) -> None:
        self.name = name
    def speak(self) -> str:
        return "..."

class Dog(Animal):  # tpyc: warning(/hides 'Animal.speak'/)
    breed: str
    def __init__(self, name: str, breed: str) -> None:
        super().__init__(name)
        self.breed = breed
    def speak(self) -> str:
        return "Woof!"

def describe(a: Animal) -> str:
    return a.speak()

def describe_ptr(a: Ptr[Animal]) -> str:
    if a is not None:
        return a.speak()
    return ""

@dynamic
class Speaker(Protocol):
    def speak(self) -> str: ...

class Parrot(Animal, Speaker):  # tpyc: warning(/'Parrot.speak' hides 'Animal.speak'/)
    def __init__(self, name: str) -> None:
        super().__init__(name)
    def speak(self) -> str:
        return "Hello!"

class Base:
    def __init__(self) -> None:
        pass

    @readonly
    def name(self) -> str:
        return "base"

class Child(Base):  # tpyc: warning(/hides 'Base.name'/)
    def __init__(self) -> None:
        super().__init__()

    @readonly
    def name(self) -> str:
        return "child"

def f(b: Base) -> str:
    return b.name()

def f2(b: Base, k: int) -> str:
    return b.name()

def fro(b: readonly[Base]) -> str:
    return b.name()

class Namer:
    def __call__(self, b: Base) -> str:
        return b.name()

def call_child(g: Fn[[Child], str], c: Child) -> str:
    return g(c)

def callc_child(g: Callable[[Child], str], c: Child) -> str:
    return g(c)

def opt_child(c: Child, g: Callable[[Child], str] | None = None) -> str:
    if g is not None:
        return g(c)
    return "none"

def pick() -> Callable[[Child], str]:
    # Return position: the slot is the declared return type.
    return f  # tpyc: warning(/calls through 'b' run 'Base.name'/)

@dispatch
def ov(g: Fn[[Child], str], c: Child) -> str:
    return g(c)

@dispatch
def ov(g: Fn[[int], str], c: Child) -> str:
    return "int"

@dispatch
def ovp(g: Fn[[Base], str], c: Child) -> str:
    return g(c)  # tpyc: warning(/calls through 'arg1' run 'Base.name'/)

@dispatch
def ovp(g: Fn[[Child], str], c: Child) -> str:
    return "child"

def ap[T](g: Fn[[T], str], x: T) -> str:
    return g(x)

def ap2[T](g: Fn[[Child, T], str], x: T) -> str:
    return g(Child(), x)

def fwd[T](g: Fn[[T], str], x: T) -> str:
    # Generic body: T is still open at ap's slot, so nothing warns here.
    return ap(g, x)  # tpyc: ok

def apply_l[T](g: Fn[[Child, list[T]], str], x: list[T]) -> str:
    return g(Child(), x)

def forward_l[T](x: list[T]) -> str:
    def fl(b: Base, y: list[T]) -> str:
        return b.name()
    # A slot keeping an open T: the reference warns once, at its
    # resolution, not again at the argument's value check.
    hl: Callable[[Base, list[T]], str] = fl
    # A Callable value at the same slot is checked as a value: once.
    return (apply_l(fl, x)  # tpyc: warning(/calls through 'b' run 'Base.name'/)
            + apply_l(hl, x))  # tpyc: warning(/calls through 'arg1' run 'Base.name'/)

# Module-level Callable binding.
G: Callable[[Child], str] = f  # tpyc: warning(/calls through 'b' run 'Base.name'/)

class Holder:
    def __init__(self, g: Callable[[Child], str]) -> None:
        self.g = g

    def run(self, g: Fn[[Child], str], c: Child) -> str:
        return g(c)

# Generator body: the yielded value is checked at the yield type.
def gen_cb() -> Iterator[Callable[[Child], str]]:
    yield f  # tpyc: warning(/calls through 'b' run 'Base.name'/)

# Async body: a local Callable binding.
async def co_cb(c: Child) -> str:
    h: Callable[[Child], str] = f  # tpyc: warning(/calls through 'b' run 'Base.name'/)
    return h(c)

def callbacks() -> None:
    c = Child()
    # Free-function argument at an Fn and at a Callable parameter.
    print("fn-arg", call_child(f, c))  # tpyc: warning(/calls through 'b' run 'Base.name'/)
    print("callable-arg", callc_child(f, c))  # tpyc: warning(/calls through 'b' run 'Base.name'/)
    # Local Callable binding.
    h: Callable[[Child], str] = f  # tpyc: warning(/calls through 'b' run 'Base.name'/)
    print("local", h(c))
    # A Callable value of the base signature at an Fn of the subclass.
    hb: Callable[[Base], str] = f  # tpyc: ok
    print("value-arg", call_child(hb, c))  # tpyc: warning(/calls through 'arg1' run 'Base.name'/)
    # A callable object whose __call__ takes the base.
    print("object", call_child(Namer(), c))  # tpyc: warning(/calls through parameter 'b' of Namer.__call__ run 'Base.name'/)
    # A readonly[Base] parameter.
    print("readonly", call_child(fro, c))  # tpyc: warning(/calls through 'b' run 'Base.name'/)
    # List element, dict value, tuple element.
    fs: list[Callable[[Child], str]] = [f]  # tpyc: warning(/calls through 'b' run 'Base.name'/)
    print("list", fs[0](c))
    d: dict[int, Callable[[Child], str]] = {1: f}  # tpyc: warning(/calls through 'b' run 'Base.name'/)
    print("dict", d[1](c))
    t: tuple[Callable[[Child], str], int] = (f, 1)  # tpyc: warning(/calls through 'b' run 'Base.name'/)
    print("tuple", t[0](c))
    print("return", pick()(c))
    # A @dispatch receiver: the ranking warns nothing, its commit once.
    print("overload", ov(f, c))  # tpyc: warning(/calls through 'b' run 'Base.name'/)
    # The exact Fn[[Base], str] variant wins; the variant f fits only by an
    # upcast loses, so its probe posts nothing here.
    print("overload-exact", ovp(f, c))  # tpyc: ok
    # Constructor and method arguments.
    ho = Holder(f)  # tpyc: warning(/calls through 'b' run 'Base.name'/)
    print("ctor", ho.g(c))
    print("method", ho.run(f, c))  # tpyc: warning(/calls through 'b' run 'Base.name'/)
    # An Optional callable parameter.
    print("optional", opt_child(c, f))  # tpyc: warning(/calls through 'b' run 'Base.name'/)
    # Generic slots: T bound to Child by the call, and a fixed Child
    # position beside a T one.
    print("generic", ap(f, c))  # tpyc: warning(/calls through 'b' run 'Base.name'/)
    print("generic2", ap2(f2, 1))  # tpyc: warning(/calls through 'b' run 'Base.name'/)
    # A callable object and a Callable value at the @dispatch receiver and
    # at the generic slot (explicit T: inference takes neither shape yet,
    # BUGS.md#generic-callable-param-subclass-arg-unjoined).
    print("overload-object", ov(Namer(), c))  # tpyc: warning(/calls through parameter 'b' of Namer.__call__ run 'Base.name'/)
    print("overload-value", ov(hb, c))  # tpyc: warning(/calls through 'arg1' run 'Base.name'/)
    print("generic-object", ap[Child](Namer(), c))  # tpyc: warning(/calls through parameter 'b' of Namer.__call__ run 'Base.name'/)
    print("generic-value", ap[Child](hb, c))  # tpyc: warning(/calls through 'arg1' run 'Base.name'/)
    print("global", G(c))
    # The function warns where fwd's T binds to Child, not in fwd's body.
    print("generic-open", fwd(f, c))  # tpyc: warning(/calls through 'b' run 'Base.name'/)
    print("open-list", forward_l([1]))
    # Comprehension element, generator, async body, lambda body.
    fc: list[Callable[[Child], str]] = [f for _ in range(2)]  # tpyc: warning(/calls through 'b' run 'Base.name'/)
    print("comp", fc[1](c))
    for g in gen_cb():
        print("gen", g(c))
    print("async", asyncio.run(co_cb(c)))
    k: Callable[[Child], str] = lambda x: call_child(f, x)  # tpyc: warning(/calls through 'b' run 'Base.name'/)
    print("lambda", k(c))

def main() -> None:
    callbacks()
    d: Dog = Dog("Rex", "Lab")
    # The call site warns too: through 'a' the override is lost (static
    # dispatch runs Animal.speak, not Dog.speak -- divergence from CPython).
    print(describe(d))  # tpyc: warning(/calls through 'a' run 'Animal.speak', not 'Dog.speak'/)
    # The pointer parameter dispatches statically too.
    print(describe_ptr(d))  # tpyc: warning(/'Dog' passed as 'Animal': calls through 'a'/)
    # Speaker is Parrot's protocol, not Animal's: through 'a' the call is static.
    p = Parrot("Polly")
    print(describe(p))  # tpyc: warning(/calls through 'a' run 'Animal.speak', not 'Parrot.speak'/)

main()
