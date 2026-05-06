from b import H
from tpy import Int32

def K() -> Int32:
    return 42

# Calls back into the cycle peer at value level.
def K_then_H() -> Int32:
    return K() + H()
