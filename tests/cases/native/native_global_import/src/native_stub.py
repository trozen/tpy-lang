# Native-module facade declaring C/C++ globals imported by main.py
# tpy: native_module
# tpy: cpp_namespace("mylib::core")
# tpy: include("native_types.hpp")

from tpy import int32
from tpy.extern import native_global

# C++ global with namespace-qualified rename
score: int32 = native_global("engine::score")

# C++ global with bare name
lives: int32 = native_global()

# C global with explicit rename
frame_count: int32 = native_global("DG_FrameCount", binding="C")

# C global with bare name
tick: int32 = native_global(binding="C")
