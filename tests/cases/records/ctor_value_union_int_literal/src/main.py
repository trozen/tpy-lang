# An int literal at an `int | float` field renders bare: the union is the render
# target, so the literal's own scalar type must not add a BigInt wrap.
class Holder:
    u: int | float

    def __init__(self) -> None:
        self.u = 5  # bare into the variant slot


def main() -> None:
    h = Holder()
    print(h.u)


main()
