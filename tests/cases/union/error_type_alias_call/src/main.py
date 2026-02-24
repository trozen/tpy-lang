# Calling a type alias as a constructor is not allowed (matches CPython)
from tpy import Int32, Array

type Arr = Array[Int32, 8]

a = Arr()  # tpyc: error(/not callable/)
