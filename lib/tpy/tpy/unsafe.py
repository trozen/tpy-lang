# tpy: native_module
from typing import overload
from tpy.extern import native, cpp_template
from tpy import Ptr, Own, UInt32, Int64, StrView, Char, Array, readonly

# unsafe_ptr: get a raw pointer from a container or string
@overload
@cpp_template("{0}.data()")
def unsafe_ptr(s: str) -> Ptr[readonly[Char]]: ...

@overload
@cpp_template("{0}.data()")
def unsafe_ptr[T, N: int](a: Array[T, N]) -> Ptr[T]: ...

@overload
@cpp_template("{0}.data()")
def unsafe_ptr[T](l: list[T]) -> Ptr[T]: ...

# unsafe_cast: reinterpret a pointer as a different pointee type
# TODO: add @compiler_check decorator to make sema validation visible here
# (currently keyed on qualified_name in calls.py: const-safety, type hint checks)
@overload
@cpp_template("reinterpret_cast<{T}*>({0})")
def unsafe_cast[T, U](p: Ptr[U]) -> Ptr[T]: ...

@overload
@cpp_template("reinterpret_cast<const {T}*>({0})")
def unsafe_cast[T, U](p: Ptr[readonly[U]]) -> Ptr[readonly[T]]: ...

# unsafe_load: read a value through a pointer at offset
@overload
@cpp_template("{0}[{1}]")
def unsafe_load[T](p: Ptr[T], offset: UInt32) -> T: ...

@overload
@cpp_template("{0}[{1}]")
def unsafe_load[T](p: Ptr[readonly[T]], offset: UInt32) -> T: ...

# unsafe_store: write a value through a pointer at offset
@cpp_template("{0}[{1}] = {2}")
def unsafe_store[T](p: Ptr[T], offset: UInt32, value: T) -> None: ...

# unsafe_copy_n: copy N elements between pointers
@overload
@cpp_template("std::copy_n({1}, {2}, {0})")
def unsafe_copy_n[T](dest: Ptr[T], src: Ptr[T], count: UInt32) -> None: ...

@overload
@cpp_template("std::copy_n({1}, {2}, {0})")
def unsafe_copy_n[T](dest: Ptr[T], src: Ptr[readonly[T]], count: UInt32) -> None: ...

# unsafe_const_cast: remove const from a pointer
@cpp_template("const_cast<{T}*>({0})")
def unsafe_const_cast[T](p: Ptr[readonly[T]]) -> Ptr[T]: ...

# unsafe_ptr_add: advance a pointer by a signed element offset
@overload
@cpp_template("({0} + {1})")
def unsafe_ptr_add[T](p: Ptr[T], delta: Int64) -> Ptr[T]: ...

@overload
@cpp_template("({0} + {1})")
def unsafe_ptr_add[T](p: Ptr[readonly[T]], delta: Int64) -> Ptr[readonly[T]]: ...

# unsafe_ptr_diff: distance between two pointers in elements
@overload
@cpp_template("static_cast<int64_t>({0} - {1})")
def unsafe_ptr_diff[T](p1: Ptr[T], p2: Ptr[T]) -> Int64: ...

@overload
@cpp_template("static_cast<int64_t>({0} - {1})")
def unsafe_ptr_diff[T](p1: Ptr[readonly[T]], p2: Ptr[readonly[T]]) -> Int64: ...

# unsafe_alloc: allocate raw memory for a single element
@cpp_template("static_cast<{T}*>(::operator new(sizeof({T}), std::align_val_t(alignof({T}))))")
def unsafe_alloc[T]() -> Ptr[T]: ...

# unsafe_alloc_n: allocate raw memory for N elements
@cpp_template("static_cast<{T}*>(::operator new(sizeof({T}) * {0}, std::align_val_t(alignof({T}))))")
def unsafe_alloc_n[T](count: UInt32) -> Ptr[T]: ...

# unsafe_free: free raw memory
@cpp_template("::operator delete({0}, std::align_val_t(alignof({T})))")
def unsafe_free[T](p: Ptr[T]) -> None: ...

# unsafe_init: placement-new construct an object
@cpp_template("::new(static_cast<void*>({0})) {T}(std::move({1}))")
def unsafe_init[T](p: Ptr[T], value: Own[T]) -> None: ...

# unsafe_drop: call destructor
@native("tpy::destroy_at")
def unsafe_drop[T](p: Ptr[T]) -> None: ...

# unsafe_move_out: move a value out of a pointer location
@cpp_template("std::move(*{0})")
def unsafe_move_out[T](p: Ptr[T]) -> Own[T]: ...

# unsafe_str_view: create a StrView from a pointer and length
@overload
@cpp_template("std::string_view({0}, {1})")
def unsafe_str_view(p: Ptr[Char], size: UInt32) -> StrView: ...

@overload
@cpp_template("std::string_view({0}, {1})")
def unsafe_str_view(p: Ptr[readonly[Char]], size: UInt32) -> StrView: ...
