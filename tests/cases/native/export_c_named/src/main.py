# Test @export(binding="C") with explicit symbol name
from tpy.extern import export
from tpy import Int32

@export("my_compute", binding="C")
def compute(x: Int32) -> Int32:
    return x + Int32(1)

print(compute(Int32(41)))
