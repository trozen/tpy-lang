# The adjacent shape to return_container_ifexpr_borrow: one ternary arm is a
# local TEMPORARY, so the `T&` slot would bind a dying object -- the return
# stays rejected even though the lvalue-ternary arm now admits containers.
from tpy import int32


def unsafe_arm(seed: list[int32], flag: bool) -> list[int32]:
    local: list[int32] = [0]
    return seed if flag else local  # tpyc: error(/Cannot return local or temporary/)
