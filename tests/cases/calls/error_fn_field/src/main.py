# Fn type cannot be used as a record field.
from tpy import Fn, Int32

class Handler:
    callback: Fn[[Int32], None]  # tpyc: error(/Fn type is only valid in parameter position/)
