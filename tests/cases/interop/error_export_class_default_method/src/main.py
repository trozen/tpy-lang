# The unsupported-arg-form rejection also covers exposed-class methods (and
# __init__): a default on a method param is rejected just like on a free
# function. Like the other class-shape rejects, it is reported at the class.
# tpy: ext_module
from tpy import Int64
from tpy.extern import export


@export
class C:  # tpyc: error(/method 'bump': default parameter values are not supported/)
    def __init__(self, a: Int64):
        self.a = a

    def bump(self, by: Int64 = 1) -> Int64:
        self.a += by
        return self.a
