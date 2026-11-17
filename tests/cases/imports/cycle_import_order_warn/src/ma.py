# `mb` (imported first) starts `ma`, which reads `mb.EB` -- and `mb` reads
# `ma.EA`, so importing `ma` first would only move the failure: the warning
# offers moving the shared definitions out instead.
import mb
from enum import Enum


class EA(Enum):
    X = 1


def fa(x: "mb.EB" = mb.EB.X) -> int:  # tpyc: warning(/'mb.EB' is read while 'mb'.*Define 'EB' above that import in mb.py, or move the shared definitions into a module outside the cycle\./)
    return x.value


def main() -> None:
    print("mutual:", fa(), mb.fb())
