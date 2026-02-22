from tpy.extern import native_c_global
from tpy import Int32

# C global import with rename
frame_count: Int32 = native_c_global("g_frame_count")

# C global import without rename (Python name = C name)
tick: Int32 = native_c_global()

def main() -> None:
    print(frame_count)
    print(tick)

main()
