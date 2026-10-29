# A generic class's `@dispatch __init__` binding set ranks as a function's
# overloads: a crossed tie is ambiguous, never the first declared
# (docs/LANGUAGE_FEATURES.md: "A genuine tie ... is an `Ambiguous overload`").
from tpy import int32, dispatch
from tpy.extern import native, cpp_template


class Animal:
    hits: int32

    def __init__(self) -> None:
        self.hits = 0


class Dog(Animal):
    def __init__(self) -> None:
        super().__init__()


@native("ns::G")
class G[T]:
    r: str

    @dispatch
    @cpp_template("ns::G({0}, {1}, {2})")
    def __init__(self, x: Dog, y: Animal, z: T) -> None: ...

    @dispatch
    @cpp_template("ns::G({0}, {1}, {2})")
    def __init__(self, x: Animal, y: Dog, z: T) -> None: ...


def main() -> None:
    print(G(Dog(), Dog(), 1).r)  # tpyc: error(/Ambiguous overload for 'G.__init__'/)


main()
