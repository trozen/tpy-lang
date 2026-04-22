# Exercises the runtime semantic divergence from CPython: a Ptr[Parent]
# that actually holds a Child reports False under tpyc's static dispatch
# (CPython's runtime dispatch would report True). Marked no_cpython for
# this reason -- the compile-time warning and folded `return false;`
# cover the feature; this case locks in the runtime behavior.
from tpy import Ptr

class Animal:
    name: str
    def __init__(self, name: str) -> None:
        self.name = name

class Dog(Animal):
    breed: str
    def __init__(self, name: str, breed: str) -> None:
        super().__init__(name)
        self.breed = breed


def ptr_downcast(a: Ptr[Animal]) -> bool:
    return isinstance(a, Dog)  # tpyc: warning(/descendant type 'Dog'/)


def main() -> None:
    d = Dog("Rex", "lab")
    print(ptr_downcast(d))  # tpyc: False, CPython: True


main()
