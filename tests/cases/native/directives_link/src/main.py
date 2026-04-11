# Test that # tpy: link directive passes linker flags (uses libm)
# tpy: include("<math.h>")
# tpy: link("m")

from tpy.extern import native
from tpy import Float64

@native(binding="C")
def sqrt(x: Float64) -> Float64: ...

def main() -> None:
    print(sqrt(4.0))
    print(sqrt(9.0))

main()
