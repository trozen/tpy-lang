x: int = 0
global x  # tpyc: error(/only allowed inside a function/)
print(x)
