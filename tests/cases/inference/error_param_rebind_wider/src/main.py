# A declared int32 parameter rebound to a wider value is a type mismatch, as for
# an annotated local: the annotation declares the parameter's one type.
from tpy import int32, int64

def shift(n: int32, big: int64) -> None:
    n = big  # tpyc: error(/Type mismatch in reassignment to 'n': expected int32, got int64/)
    print(n)

shift(1, 5000000000)
