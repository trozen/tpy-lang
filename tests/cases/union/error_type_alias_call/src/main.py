# Calling a type alias as a constructor is not allowed (matches CPython)
from tpy import int32, Array

type Arr = Array[int32, 8]

a = Arr()  # tpyc: error(/not callable/)
