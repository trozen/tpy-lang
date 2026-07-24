# Over-trigger guard: a comprehension loop var reusing a reference-typed
# capture's name is a separate Python-3 scope, NOT a rebind of the capture, so
# it must not trigger the reference-rebind reject. Read-only through `p` after
# the comprehension is intentional -- the point is that `p` still resolves to
# the aliased Pet (the comprehension `p` did not leak or rebind it), not a
# copy-vs-alias observation.
class Pet:
    hp: int
    def __init__(self, h: int) -> None:
        self.hp = h


class Cat:
    pet: Pet
    def __init__(self, x: Pet) -> None:
        self.pet = x


def f(a: Cat) -> int:
    match a:
        case Cat(pet=p):
            squares = [p * p for p in range(3)]   # comprehension `p`: fresh scope
            return len(squares) + p.hp            # `p` still aliases a.pet (Pet)
    return -1


def main():
    print(f(Cat(Pet(5))))   # 3 + 5 = 8


main()
