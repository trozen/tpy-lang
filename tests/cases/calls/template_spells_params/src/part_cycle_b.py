# The other member of part_cycle_a's import cycle.
from enum import Enum
from tpy import int32
import part_cycle_a


class EB(Enum):
    B = 2


def fb(x: "part_cycle_a.EA") -> int32:
    return x.value
