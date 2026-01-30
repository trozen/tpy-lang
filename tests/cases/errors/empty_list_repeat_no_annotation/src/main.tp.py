# Empty list repetition without annotation should error
x = [] * 3  # tpyc: error(/Empty array literal requires explicit type annotation/)
