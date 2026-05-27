# *unpack into a NON-variadic method must be rejected cleanly, not crash
# with "Unknown expression type: TpyStarUnpack" (the method pre-analysis
# paths route star-unpack through analyze_call_arg; the non-variadic
# typecheck gate in _check_args_or_pack_varargs rejects it).
from tpy import Int32


class Sink:
    def push(self, x: Int32) -> None:
        pass


def main() -> None:
    s = Sink()
    xs: list[Int32] = [1, 2, 3]
    s.push(*xs)  # tpyc: error(/does not accept \*args/)


main()
