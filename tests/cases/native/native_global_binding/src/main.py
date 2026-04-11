# Test native_global() with all variants: C/C++ binding, rename, bare name, array
# tpy: include("native_types.hpp")
from tpy import Int32, Ptr, deref
from tpy.extern import native_global

# C global with explicit name
frame_count: Int32 = native_global("DG_FrameCount", binding="C")

# C global with bare name (Python name = C name)
tick: Int32 = native_global(binding="C")

# C++ global with namespace-qualified name
score: Int32 = native_global("engine::score")

# C++ global with bare name (Python name = C++ name)
lives: Int32 = native_global()

# C global array
data: Ptr[Int32] = native_global("shared_data", binding="C", array=True)

def main() -> None:
    print(frame_count)
    print(tick)
    print(score)
    print(lives)
    print(deref(data))

main()
