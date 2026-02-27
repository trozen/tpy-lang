"""Test type inference with Ptr[T] and ReadOnlyPtr[T] parameters."""
from tpy import Int32, Ptr, ReadOnlyPtr


class PtrHolder[T]:
    ptr: Ptr[T]

    def __init__(self, ptr: Ptr[T]) -> None:
        self.ptr = ptr


class ReadOnlyPtrHolder[T]:
    ptr: ReadOnlyPtr[T]

    def __init__(self, ptr: ReadOnlyPtr[T]) -> None:
        self.ptr = ptr


class Point:
    x: Int32
    y: Int32


pt: Point = Point()
pt.x = 10
pt.y = 20

# Create explicitly-typed pointer variables
ptr: Ptr[Point] = pt
cptr: ReadOnlyPtr[Point] = pt

# Inference from Ptr[Point] -> PtrHolder[Point]
holder = PtrHolder(ptr)
holder.ptr.x = 100
print(pt.x)

# Inference from ReadOnlyPtr[Point] -> ReadOnlyPtrHolder[Point]
const_holder = ReadOnlyPtrHolder(cptr)
print(const_holder.ptr.y)

# Inference from Ptr[Point] -> ReadOnlyPtrHolder[Point] (Ptr coerces to ReadOnlyPtr)
const_holder2 = ReadOnlyPtrHolder(ptr)
print(const_holder2.ptr.x)
