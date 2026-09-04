# A function-local that SHADOWS a native global, first declared in both arms of
# an if. Python scopes the local to the function, so `return frame_count` must
# read the local -- emitting the extern read instead would silently return the
# C++ value, so the shape stays rejected until the shadow is modelled.
from tpy.extern import native_global
from tpy import Int32

frame_count: Int32 = native_global("DG_FrameCount", binding="C")


def pick(c: bool) -> Int32:
    if c:  # tpyc: error(/not yet supported.*if.hoist_native_global/)
        frame_count = 1
    else:
        frame_count = 2
    return frame_count


def main() -> None:
    print(pick(True))


main()
