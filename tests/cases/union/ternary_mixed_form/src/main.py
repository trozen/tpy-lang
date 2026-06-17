# A ternary (union form) joining a borrow-form arm (pointer-variant
# param) and a storage-form arm (value-variant field) normalizes each arm to
# the pointer variant (to_ptr_variant on the field arm), so the C++ ?: operands
# match. The binding ALIASES the chosen arm; mutation is visible on the source,
# matching CPython. Pre-fix this emitted mixed ?: operands (variant<A*,B*> vs
# variant<A,B>). Explicit elif sidesteps an unrelated fall-through-narrowing
# limitation on two-member unions.
class A:
    x: int
    def __init__(self, x: int) -> None:
        self.x = x


class B:
    y: int
    def __init__(self, y: int) -> None:
        self.y = y


class Holder:
    pet: A | B
    def __init__(self, p: A | B) -> None:
        self.pet = p


def bump(p: A | B, h: Holder, c: bool) -> None:
    t = p if c else h.pet
    if isinstance(t, A):
        t.x += 100
    elif isinstance(t, B):
        t.y += 100


def main() -> None:
    h = Holder(B(7))
    param = A(3)
    bump(param, h, True)         # param (ptr-variant) arm
    print(param.x)               # 103 -- visible on caller's object
    bump(param, h, False)        # field (value-variant) arm
    pet = h.pet
    if isinstance(pet, B):
        print(pet.y)             # 107 -- visible on the field


main()
