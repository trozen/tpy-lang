# native_global(binding="C") is emitted verbatim as `extern "C" <type> <name>;`,
# so its type goes through the same C-ABI allow-list as a C-linkage signature.
from tpy.extern import native_global
from tpy import Int32, StrView

# The supported spelling, kept next to the rejected one for contrast.
frame_count: Int32 = native_global("DG_FrameCount", binding="C")

banner: StrView = native_global("g_banner", binding="C")  # tpyc: error(/native_global 'banner': type 'StrView' is not representable in the C ABI; use Ptr\[readonly\[UInt8\]\] and convert with/)
