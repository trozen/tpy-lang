# StaticList() without annotation cannot infer type
x = StaticList()  # tpyc: error(/Cannot infer element type for StaticList/)
