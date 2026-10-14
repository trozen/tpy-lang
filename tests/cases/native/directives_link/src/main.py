# Test that `__tpy__` "links" pass linker flags (uses libm); a
# platform-filtered entry for another OS stays out of the link.
__tpy__ = {"includes": ["<math.h>"],
           "links": ["m",
                     {"lib": "tpy_nonexistent_lib", "platform": "win32"}]}

from tpy.extern import native
from tpy import float64

@native(binding="C")
def sqrt(x: float64) -> float64: ...

def main() -> None:
    print(sqrt(4.0))
    print(sqrt(9.0))

main()
