# Error: a Final initializer cannot reference an imported variable from
# another module -- the cross-module Final propagation path emits a
# specific diagnostic that names the source module.
from typing import Final
from tpy import int32
from source import COUNTER

LIMIT: Final[int32] = COUNTER  # tpyc: error(/cross-module Final references are not yet supported/)
