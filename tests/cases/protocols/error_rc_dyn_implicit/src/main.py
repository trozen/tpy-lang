# Rc[Pet] (Pet @dynamic) -- implicit inference path fails.
#
# The same shape works for Box (`box: Box[Pet] = Box(Parrot(...))` is fine)
# but fails for Rc. Two independent reasons, both tracked as follow-ups in
# TODO.md (Rc[P] entry):
#
# 1. INFERENCE PATH DIFFERS. Box(...) is a record constructor call -- sema
#    routes through `infer_type_params_for_record` (sema/type_ops.py) which
#    has an LHS-hint preference for @dynamic protocol type-args. That
#    preference can switch the arg-inferred T=Parrot to T=Pet when the
#    structural-conform case demands it. Rc.new(...) is a static method
#    call -- routes through the function-call inference path which has no
#    LHS-hint preference. So Rc.new infers T=Parrot from the argument
#    unconditionally; the LHS `r: Rc[Pet]` annotation doesn't influence the
#    inference. Result: `Rc.new(Parrot(...))` returns Own[Rc[Parrot]].
#
# 2. CONVERTIBILITY DIFFERS. Once T is inferred, the resulting Rc[Parrot]
#    needs to convert to Rc[Pet]. Box has working Covariant[T] -- its
#    converting move ctor `Box(Box<U>&& o) requires is_base_of_v<T, U>`
#    just transfers `_ptr` (Parrot* implicitly upcasts to Pet*). Rc also
#    declares Covariant[T] in source but it doesn't actually work: Rc's
#    field is `_cell: Ptr[_RcCell[T]]` -- `_RcCell<Parrot>*` does not
#    implicitly convert to `_RcCell<Pet>*` because _RcCell<Parrot> doesn't
#    publicly inherit _RcCell<Pet>. Tracked in BUGS.md as the
#    "Covariant[T] converting constructor codegen assumes direct
#    field-pointer covariance" entry.
#
# To make `r: Rc[Pet] = Rc.new(Parrot(...))` work would need fixing both:
# extend LHS-hint preference into the function-call inference path AND fix
# the Covariant-through-wrapping-layer bug (OR wire LHS-hint + Adapter
# wrapping for Rc analogous to Box's structural path). See TODO.md.
#
# Workaround today: wrap in Box first -- see rc_dyn_box_workaround test.
from typing import Protocol
from tpy import dynamic
from tplib import Rc


@dynamic
class Pet(Protocol):
    def name(self) -> str: ...


class Parrot(Pet):
    label: str
    def __init__(self, label: str) -> None:
        self.label = label
    def name(self) -> str:
        return self.label


def main() -> None:
    r: Rc[Pet] = Rc.new(Parrot("Polly"))  # tpyc: error(/Type mismatch.*Rc\[Pet\].*Rc\[Parrot\]/)
    print(r.get().name())


main()
