from tpy.extern import native_global
from tpy import int32
frame_count: int32 = native_global("DG_FrameCount", binding="C")
def pick(c: bool) -> int32:
    if c:
        frame_count = 1
    else:
        frame_count = 2
    return frame_count
def main() -> None:
    print(pick(True))
main()
