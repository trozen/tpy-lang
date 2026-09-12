# A @native record constructor rvalue at a NESTED argument position: its
# construction and temp semantics are outside the ctor-rvalue argument row.
# Concretely, `use_nr(NR(x))` constructs the native record inline as the
# call argument, and TPy rejects that shape today.
from tpy.extern import native
from tpy import int32


@native('mylog::NR')
class NR:
    @native('mylog::NR')
    def __init__(self, x: int32) -> None: ...


@native('mylog::use_nr')
def use_nr(r: NR) -> int32: ...


def go(x: int32) -> int32:
    return use_nr(NR(x))  # tpyc: error(/call.native_arg.call_rvalue/)


def main() -> None:
    print(go(2))


main()
