# Ext-only: behaviors where the compiled extension's boundary diverges from
# the TPy source (where the Span annotation is not enforced and a buffer arg
# is aliased, not copied). All of these are checked only against the .so.
import array
import span_numeric as m

# Strict-by-format/itemsize IN: a non-buffer-protocol arg is a TypeError,
# where the source ignores the annotation and would accept any iterable.
try:
    m.sum_floats([1.0, 2.0])
    raise AssertionError("expected TypeError for a non-buffer arg")
except TypeError:
    pass

# A buffer whose element format/itemsize does not match Span[readonly[T]]'s T
# is a TypeError -- no coercion across width/signedness/int-vs-float (same
# strict-by-kind family as containers/enums).
try:
    m.sum_floats(array.array("f", [1.0]))  # float32, not float64
    raise AssertionError("expected TypeError for a float32 buffer")
except TypeError:
    pass
try:
    m.sum_int32(array.array("q", [1]))  # int64, not int32
    raise AssertionError("expected TypeError for an int64 buffer")
except TypeError:
    pass

# A non-contiguous buffer (a strided memoryview slice) is rejected -- v1
# requests a C-contiguous 1-D view (PyBUF_ND, no strides).
strided = memoryview(array.array("d", [1.0, 2.0, 3.0, 4.0]))[::2]
try:
    m.sum_floats(strided)
    raise AssertionError("expected TypeError/BufferError for a strided buffer")
except (TypeError, BufferError):
    pass

# Copy-in: a mutation through a Span[T] param is NOT visible to the caller
# (the boundary marshalled an owned copy). The source aliases (array.array's
# buffer is mutable in place), so this is ext-only.
caller = array.array("i", [1, 2, 3])
m.scale_in_place(caller, 10)
assert list(caller) == [1, 2, 3]  # unchanged -- the copy was scaled, not this

print("ext-only span checks: PASS")
