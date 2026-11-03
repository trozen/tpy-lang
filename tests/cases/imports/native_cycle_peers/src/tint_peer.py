from tpy import int32
import tint


def offset() -> int32:
    return 1


# The peer names the enum through the member's forward declarations.
def paint() -> tint.Col:
    return tint.Col.BLUE  # tpyc: ok
