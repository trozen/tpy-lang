# list() without annotation or arguments cannot infer type
x = list()  # tpyc: error(/Cannot infer element type for list/)
