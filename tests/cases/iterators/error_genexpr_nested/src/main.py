# Error: nested generator expressions not supported
result = (x + y for x in range(3) for y in range(3))  # tpyc: error(/[Nn]ested/)
