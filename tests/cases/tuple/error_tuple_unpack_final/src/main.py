# Tuple unpack should not reassign Final variables at module level.
from typing import Final

X: Final[int] = 10
Y = 20

_, X = 1, 2  # tpyc: error(/Cannot reassign Final/)
