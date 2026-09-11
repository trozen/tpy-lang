# The other half of the cycle: it imports `moda` back and calls into it.
from tpy import Int32
import moda


def pong(n: Int32) -> Int32:
    if n <= 0:
        return 1
    return moda.ping(n - 1) + 10
