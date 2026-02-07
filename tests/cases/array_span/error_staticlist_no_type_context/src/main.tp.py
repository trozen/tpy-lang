# StaticList() without annotation cannot infer type
from tpy import StaticList
x = StaticList()  # tpyc: error(/Cannot infer element type for StaticList/)
