from tpy import int32
import cyc_a


def scale(n: int32) -> int32:
    return n * 10


def total_countdown(n: int32) -> int32:
    return sum(cyc_a.countdown(n))
