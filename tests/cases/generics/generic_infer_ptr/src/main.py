"""Test type inference with Ptr[T] and Ptr[readonly[T]] parameters."""
from tpy import Int32, Ptr, readonly


class PtrHolder[T]:
    ptr: Ptr[T]

    def __init__(self, ptr: Ptr[T]) -> None:
        self.ptr = ptr


class ReadOnlyPtrHolder[T]:
    ptr: Ptr[readonly[T]]

    def __init__(self, ptr: Ptr[readonly[T]]) -> None:
        self.ptr = ptr


class Point:
    x: Int32
    y: Int32


pt: Point = Point()
pt.x = 10
pt.y = 20

# Create explicitly-typed pointer variables
ptr: Ptr[Point] = pt
cptr: Ptr[readonly[Point]] = pt

# Inference from Ptr[Point] -> PtrHolder[Point]
holder = PtrHolder(ptr)
holder.ptr.x = 100
print(pt.x)

# Inference from Ptr[readonly[Point]] -> ReadOnlyPtrHolder[Point]
const_holder = ReadOnlyPtrHolder(cptr)
print(const_holder.ptr.y)

# Inference from Ptr[Point] -> ReadOnlyPtrHolder[Point] (Ptr coerces to Ptr[readonly[...]])
const_holder2 = ReadOnlyPtrHolder(ptr)
print(const_holder2.ptr.x)
