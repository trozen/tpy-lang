# A walrus in a `match` guard runs only when its pattern matched, so a later
# arm, reached also when no earlier pattern matched, may not read it.
from tpy import int32


def f(v: int32) -> None:
    match v:
        case 1 if (m := v * 2) > 5:
            print("big", m)
        case _:
            # The subject: `m` is bound only when `v` matched `1`.
            print("other", m)  # tpyc: error(/variable 'm' may not be assigned at this point/)


f(2)
