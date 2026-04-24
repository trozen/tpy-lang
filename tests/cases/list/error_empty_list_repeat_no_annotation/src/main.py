# Empty list repetition without annotation should error
x = [] * 3  # tpyc: error(/Cannot infer element type for list/)
