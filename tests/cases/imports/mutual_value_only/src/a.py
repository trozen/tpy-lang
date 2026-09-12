from b import H
from tpy import int32

def K() -> int32:
    return 42

# Calls back into the cycle peer at value level.
def K_then_H() -> int32:
    return K() + H()
