# Iterating a dict view off an UNPROVEN Optional receiver: the bare view render
# would skip the receiver's None check, so the loop head rejects it. The proven
# receiver is pinned by tests/cases/protocols/dyn_inherit_ref_param.
from tpy import int32


def sum_values(d: dict[int32, int32] | None) -> int32:
    s = 0
    for v in d.values():  # tpyc: error(/iter\.method_call_shape/)
        s = s + v
    return s


def main() -> None:
    print(sum_values({1: 10}))


main()
