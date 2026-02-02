"""Test type inference with Ptr[T] and ConstPtr[T] parameters."""
from tpy import Int32, Ptr, ConstPtr


class PtrHolder[T]:
    ptr: Ptr[T]

    def __init__(self, ptr: Ptr[T]) -> None:
        self.ptr = ptr


class ConstPtrHolder[T]:
    ptr: ConstPtr[T]

    def __init__(self, ptr: ConstPtr[T]) -> None:
        self.ptr = ptr


class Point:
    x: Int32
    y: Int32


pt: Point = Point()
pt.x = 10
pt.y = 20

# Create explicitly-typed pointer variables
ptr: Ptr[Point] = pt
cptr: ConstPtr[Point] = pt

# Inference from Ptr[Point] -> PtrHolder[Point]
holder = PtrHolder(ptr)
holder.ptr.x = 100
print(pt.x)

# Inference from ConstPtr[Point] -> ConstPtrHolder[Point]
const_holder = ConstPtrHolder(cptr)
print(const_holder.ptr.y)

# Inference from Ptr[Point] -> ConstPtrHolder[Point] (Ptr coerces to ConstPtr)
const_holder2 = ConstPtrHolder(ptr)
print(const_holder2.ptr.x)
