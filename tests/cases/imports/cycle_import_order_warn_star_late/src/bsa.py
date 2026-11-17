# `bsa` defines a class named like the builtin `zip` after its cycle import:
# under CPython, `zip` in `bsb` is still the builtin there.
from enum import Enum
import bsb


class zip(Enum):
    X = 7
