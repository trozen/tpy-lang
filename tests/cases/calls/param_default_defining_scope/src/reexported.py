# A dataclass whose default_factory is `list` imported through a module
# that re-exports it (factories.py).
from dataclasses import dataclass, field
from tpy import int32
from factories import list


@dataclass
class MadeReexport:
    xs: list[int32] = field(default_factory=list)
    n: int32 = 0
