# Regression: a `T: Copyable` shadow bound called on a Box whose T is the
# enclosing class's *unbound* type parameter must be rejected at sema.
# Earlier shape of this branch silently accepted such call sites (the
# marker arm's `not is_type_non_copyable(TypeParamRef)` flipped the
# conservative "no info" return to "conforms"), only to fail later at C++
# template instantiation -- exactly the bug class Copyable was meant to
# eliminate. The conformance check is now authoritative for type variables:
# an unbound type param does not conform to any marker protocol whose
# predicate would otherwise default to "conforms".
from tpy import int32, Own
from tplib import Box


class Wrapper[T]:
    box: Box[T]

    def __init__(self, box: Own[Box[T]]) -> None:
        self.box = box

    def dup(self) -> Own[Box[T]]:
        return self.box.clone()  # tpyc: error(/Method 'clone' requires type parameter 'T' to satisfy 'Copyable'/)


# Exercise the diagnostic at both body-analysis time (the .dup body above)
# and at a concrete monomorphization call site so a future refactor that
# defers body checking until instantiation still trips the regression.
def main() -> None:
    w = Wrapper[int32](Box(int32(7)))
    _ = w.dup()


main()
