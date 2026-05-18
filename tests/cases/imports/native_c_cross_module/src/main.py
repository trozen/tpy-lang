# Test: both `import lib` and `from lib import ...` coexist
# The named imports must not be dropped when bare import also exists.
import lib
from tpy import Int32, Ptr
from lib import Rect, rect_area

def main() -> None:
    r = Rect(Int32(10), Int32(20), Int32(100), Int32(50))
    print(r.w)
    print(rect_area(r))

main()
