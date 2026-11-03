# Section native_enum: a @native enum, a plain enum and a record of a cycle
# member, named by its peer.
__tpy__ = {"includes": ["cycle_types.hpp"]}
from enum import Enum
from tpy import int32
from tpy.extern import native
import gauge


@native("signs::Level")
class Level(Enum):
    LOW = 1
    HIGH = 2


class Mode(Enum):
    OFF = 0
    ON = 1


class Reading:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


def gauge_n() -> int32:
    return gauge.Gauge(4).n
