# A single-yield generator iterating a module-global container: the lambda
# peephole would capture the bare pointer slot and call `.begin()` on it, so
# the head rejects rather than emitting that
# (BUGS.md#simple-gen-over-global-container). Aliasing the global to a local
# first does not help -- that binding is itself a `decl.slot_type` reject, as
# tests/cases/globals/error_module_var_record_local_binding pins. The aliased
# local a simple generator CAN iterate is a param, pinned by
# tests/cases/iterators/gen_proto_param_aliased_local.
from typing import Iterator
from tpy import int32

xs = [1, 2, 3]


def over_global() -> Iterator[int32]:  # tpyc: error(/sgen\.iterable_global_slot/)
    for v in xs:
        yield v


def main() -> None:
    total = 0
    for v in over_global():
        total = total + v
    print(total)


main()
