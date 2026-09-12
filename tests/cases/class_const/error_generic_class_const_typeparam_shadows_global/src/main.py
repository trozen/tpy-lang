# A type-parameter name that shadows a module-level Final still triggers
# the Phase 9 rejection -- not because TPy's sema can't distinguish, but
# because in C++ the template parameter shadows the surrounding namespace
# inside the template body, so emitting `BASE` would resolve to the type
# parameter (a type) and fail to compile. Workaround: rename the type param.
from typing import Final
from tpy import int32


BASE: Final[int32] = 100


class Container[BASE]:
    THRESHOLD: Final[int32] = BASE  # tpyc: error(/class constant 'THRESHOLD' on generic class 'Container' references type parameter 'BASE' in its initializer/)
