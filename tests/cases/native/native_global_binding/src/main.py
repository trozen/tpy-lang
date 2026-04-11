# Test unified native_global() with binding="C" and array=True kwargs
# tpy: include("native_types.hpp")
from tpy import Int32, Ptr, deref
from tpy.extern import native_global

# C global (equivalent to native_c_global)
frame_count: Int32 = native_global("DG_FrameCount", binding="C")

# C++ global (default, no binding)
score: Int32 = native_global("engine::score")

# C global array (equivalent to native_c_global_array)
data: Ptr[Int32] = native_global("shared_data", binding="C", array=True)

def main() -> None:
    print(frame_count)
    print(score)
    print(deref(data))

main()
