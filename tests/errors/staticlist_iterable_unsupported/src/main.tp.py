# StaticList(iterable) is not yet supported - needs both T and N inference
from tpy import StaticList
x = StaticList([1, 2, 3])  # tpyc: error(/Cannot infer element type for StaticList/)
