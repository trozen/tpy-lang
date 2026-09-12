# A union whose members are all `Ptr[T]` already holds borrowed references,
# so its alternatives ARE the borrow form and one spelling serves every
# position -- no carve-out in the renderer. Only the alias and a parameter
# signature reach codegen today (BUGS.md#ptr-member-union-unreachable);
# those are the pins.
from tpy import int32, Ptr


class Dog:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


class Cat:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


# The alias renders `using PtrPet = ::tpy::Union<Cat*, Dog*>;`.
type PtrPet = Ptr[Dog] | Ptr[Cat]


# The parameter renders `const ::tpy::Union<Cat*, Dog*>&`: a Ptr is a VALUE
# type, so the union takes the value-union parameter convention (a const ref
# to the whole union) over a pointer alternative pack, which is what makes it
# compare through the pointee. No call site can build the argument yet.
def takes(u: PtrPet) -> int32:  # tpyc: ok
    return 1


def main() -> None:
    d = Dog(7)
    p: Ptr[Dog] = d
    print(p.n)


main()
