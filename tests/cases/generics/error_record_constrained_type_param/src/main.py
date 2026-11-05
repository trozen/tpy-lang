# A constrained type parameter is refused on a record as on a protocol.
from tpy import int32


class R[T: (int32, float)]:  # tpyc: error(/Type parameter 'T': constrained type parameters \(`T: \(int32, float\)`\) are not supported/)
    pass
