# Test: both `import lib` and `from lib import ...` coexist
# The named imports must not be dropped when bare import also exists.
import lib
from tpy import int32, Ptr
from lib import Rect, rect_area

def main() -> None:
    r = Rect(int32(10), int32(20), int32(100), int32(50))
    print(r.w)
    print(rect_area(r))

main()
