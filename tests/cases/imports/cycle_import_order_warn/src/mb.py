import ma
from enum import Enum


class EB(Enum):
    X = 2


def fb(y: "ma.EA" = ma.EA.X) -> int:  # tpyc: ok
    return y.value
