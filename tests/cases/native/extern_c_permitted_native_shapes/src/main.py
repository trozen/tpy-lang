# The permitted side of the C-ABI gate for the two shapes that need a C
# companion: a nullable @native(binding="C") struct (pointer repr, in return
# AND param position) and a C-struct array global (the element names a struct
# the C header declares). Narrowing the gate again has to fail here. The
# companion-free shapes live in extern_c_permitted_types, which keeps its
# CPython run.
# tpy: include("native_types.hpp")
from tpy.extern import native, native_global, export
from tpy import int32, Ptr, deref

@native("Widget", binding="C")
class Widget:
    n: int32

@native("Point", binding="C")
class Point:
    x: int32
    y: int32

# Return position: a C function handing back a struct pointer or NULL.
@native("find_widget", binding="C")
def find_widget(k: int32) -> Widget | None: ...  # tpyc: ok

# Param position: the same nullable handle, on the export side.
@export(binding="C")
def widget_value(w: Widget | None) -> int32:  # tpyc: ok
    if w is None:
        return -1
    return w.n

# Array global whose element is the C struct itself.
pts: Ptr[Point] = native_global("g_pts", binding="C", array=True)  # tpyc: ok

def main() -> None:
    w = find_widget(1)
    print("nullable:", widget_value(w))
    missing = find_widget(0)
    print("nullable:", widget_value(missing))
    # The handle aliases the C-side struct rather than copying it: the write
    # here is what the next call reads back.
    if w is not None:
        w.n = 99
    again = find_widget(1)
    print("nullable:", widget_value(again))
    p = deref(pts)
    print("array:", p.x, p.y)

main()
