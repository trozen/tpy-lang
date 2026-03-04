# Self type error: cannot use Self as a field type
from typing import Self

class Node:
    value: int
    next: Self  # tpyc: error(/Self cannot be used as a field type/)
