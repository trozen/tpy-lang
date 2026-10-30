# A @native record constructor rvalue at a native callee's reference
# parameter: the argument converts at its parameter like any record, the
# construction held for the call (`NR __tmp_N = NR(x); use_nr(__tmp_N)`).
from tpy.extern import native
from tpy import int32


@native('mylog::NR')
class NR:
    @native('mylog::NR')
    def __init__(self, x: int32) -> None: ...


@native('mylog::use_nr')
def use_nr(r: NR) -> int32: ...


def go(x: int32) -> int32:
    return use_nr(NR(x))  # tpyc: ok


def main() -> None:
    print(go(2))


main()
