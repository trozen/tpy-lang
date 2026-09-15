# A subclass RVALUE at a base-typed CONTAINER element still rejects. The
# sibling FIELD write is admitted (upcast_field_rvalue) because the slicing
# copy is what sema's "upcast narrows" warning declares; the element sink has
# no such arm, so the boundary of that admission is pinned here.
class Pet:
    name: str

    def __init__(self, name: str) -> None:
        self.name = name


class Dog(Pet):
    def __init__(self, name: str) -> None:
        super().__init__(name)


def main() -> None:
    d: dict[str, Pet] = {}
    d["k"] = Dog("x")  # tpyc: warning(/upcast narrows/) error(/not yet supported.*setitem\.record_value_shape/)
    print(d["k"].name)


main()
