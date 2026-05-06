# Cycle peer `a` defines a non-generic record `A` with an
# @overload-dispatched method `merge`. Each overload variant is
# emitted inline in the struct under a different mangled name, so
# the .hpp body of the impl needs B's complete layout. The gate
# pins this via `id(method) in compiled.analyzer.overload_groups`,
# the same source codegen consults to drive its inline decision.
from a import A
from b import B

def main() -> None:
    a = A(7)
    print(a.merge(B(3)))
    print(a.merge(5))

main()
