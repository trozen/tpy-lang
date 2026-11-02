# isinstance() against a @runtime_checkable protocol -- declared, inherited
# from a decorated parent, or on a stub -- compiles with no warning.
from typing import Iterable, Protocol, Sized, runtime_checkable
from tpy import Span, auto_readonly, dynamic, int32


@runtime_checkable
class Speaker(Protocol):
    def speak(self) -> str: ...


class Walker(Protocol):
    def walk(self) -> int32: ...


# Undecorated: checkability is inherited from Speaker, as in CPython.
class LoudSpeaker(Speaker, Protocol):
    def shout(self) -> str: ...


@runtime_checkable
class Getter[T](Protocol):
    def get(self) -> T: ...


@runtime_checkable
@dynamic
class Shape(Protocol):
    def area(self) -> int32: ...


class Dog:
    def __init__(self) -> None:
        pass

    def speak(self) -> str:
        return "woof"

    def shout(self) -> str:
        return "WOOF"


class Cat:
    def __init__(self) -> None:
        pass

    def walk(self) -> int32:
        return 4


class Holder:
    def __init__(self, v: int32) -> None:
        self.v = v

    def get(self) -> int32:
        return self.v


class Square(Shape):
    def __init__(self, side: int32) -> None:
        self.side = side

    def area(self) -> int32:
        return self.side * self.side


# free function: a decorated protocol
def free_fn(x: Speaker | Walker) -> str:
    if isinstance(x, Speaker):  # tpyc: ok
        return x.speak()
    return "walks " + str(x.walk())


# inherited: an undecorated child of a decorated protocol
def inherited(x: LoudSpeaker | Walker) -> str:
    if isinstance(x, LoudSpeaker):  # tpyc: ok
        return x.shout()
    return "walks " + str(x.walk())


# generic: a decorated generic protocol, tested bare
def generic(x: Getter[int32] | Walker) -> int32:
    if isinstance(x, Getter):  # tpyc: ok
        return x.get()
    return x.walk()


# dynamic: @dynamic and @runtime_checkable together are accepted
def dynamic_area(s: Shape) -> int32:
    return s.area()


# stub: a stub protocol decorated in the library is silent
def stub_sized(xs: Iterable[int32]) -> str:
    if isinstance(xs, Sized):  # tpyc: ok
        return "sized"
    return "unsized"


class Checker:
    data: list[int32]

    def __init__(self) -> None:
        self.data = [7, 8]

    # method: a decorated protocol tested in a method body
    def check(self, x: Speaker | Walker) -> str:
        if isinstance(x, Speaker):  # tpyc: ok
            return x.speak()
        return "walks"

    # twice: the const clone analyzes the body again; one diagnostic set
    @auto_readonly
    def pick(self, x: Speaker | Walker) -> Span[auto_readonly[int32]]:
        if isinstance(x, Speaker):  # tpyc: ok
            return self.data
        return self.data


def main() -> None:
    d = Dog()
    c = Cat()
    print("free_fn", free_fn(d), free_fn(c))
    print("inherited", inherited(d), inherited(c))
    h = Holder(5)
    print("generic", generic(h), generic(c))
    sq = Square(3)
    print("dynamic", dynamic_area(sq))
    print("stub", stub_sized([1, 2]))
    k = Checker()
    print("method", k.check(d), k.check(c))
    s = k.pick(d)
    print("twice", s[0], s[1])


main()
