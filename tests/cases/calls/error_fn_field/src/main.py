# Fn type cannot be used as a record field.
from tpy import Fn, int32

class Handler:
    callback: Fn[[int32], None]  # tpyc: error(/Fn type is only valid in parameter position/)
