# @native function with *args: no body to analyze, so mutated_params is None
# and the sync pass leaves the slot as declared. The signature is the source
# of truth (the C++ binding decides const-ness, not TPy inference).
from tpy import Int32
from tpy.extern import native


@native("__user_sum_ints")
def user_sum_ints(*xs: Int32) -> Int32: ...  # tpyc: ok


def main() -> None:
    print(user_sum_ints(1, 2, 3))
