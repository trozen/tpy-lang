# The other half of the cycle: it imports `moda` back and calls into it.
from tpy import int32
import moda


def pong(n: int32) -> int32:
    if n <= 0:
        return 1
    return moda.ping(n - 1) + 10
