from dataclasses import dataclass
from tpy import int32
import sig


@dataclass
class Gauge:
    n: int32
    # The peer's @native enum as a field type, with a member default.
    level: sig.Level = sig.Level.HIGH  # tpyc: ok
    # The peer's plain enum as a field type, with a member default.
    mode: sig.Mode = sig.Mode.ON  # tpyc: ok


# A header-held body printing the peer's enums and their names.
def show[T](tag: T, g: Gauge) -> None:
    print(tag, g.level, g.level.name, sig.Level.LOW, g.mode, g.mode.name, sig.Mode.OFF)  # tpyc: ok


# The peer's record beside its enums in the forward declarations.
def reading_v(r: sig.Reading) -> int32:
    return r.v  # tpyc: ok
