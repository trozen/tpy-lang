# Chained comparisons reject 'is', 'is not', 'in', 'not in' operators
x = 1
y = 2
z = 3
print(x is y is z)  # tpyc: error(/'is' cannot be used in chained/)
