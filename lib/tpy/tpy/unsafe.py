# tpy: native_module
from typing import overload
from tpy.extern import native, cpp_template
from tpy import Ptr, Own, Int8, Int16, Int32, Int64, UInt8, UInt16, UInt32, UInt64, Float32, StrView, Char, Array, readonly

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

@overload
@cpp_template("{0}.data()")
def unsafe_ptr(b: bytes) -> Ptr[readonly[UInt8]]: ...

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
def unsafe_load[T](p: Ptr[T], offset: UInt32) -> Own[T]: ...

@overload
@cpp_template("{0}[{1}]")
def unsafe_load[T](p: Ptr[readonly[T]], offset: UInt32) -> Own[T]: ...

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
@cpp_template("::new(static_cast<void*>({0})) {T}({1})")
def unsafe_init[T](p: Ptr[T], value: Own[T]) -> None: ...

# unsafe_drop: call destructor
@native("tpy::destroy_at")
def unsafe_drop[T](p: Ptr[T]) -> None: ...

# unsafe_move_out: move a value out of a pointer location
@cpp_template("std::move(*{0})")
def unsafe_move_out[T](p: Ptr[T]) -> Own[T]: ...

# unsafe_read_*: read typed values from a byte buffer at a byte offset.
# Uses reinterpret_cast -- safe on little-endian targets (x86, ARM).
@cpp_template("*reinterpret_cast<const int8_t*>({0}.data() + {1})")
def unsafe_read_i8(data: bytes, offset: Int32) -> Int8: ...

@cpp_template("({0})[{1}]")
def unsafe_read_u8(data: bytes, offset: Int32) -> UInt8: ...

@cpp_template("*reinterpret_cast<const int16_t*>({0}.data() + {1})")
def unsafe_read_i16(data: bytes, offset: Int32) -> Int16: ...

@cpp_template("*reinterpret_cast<const uint16_t*>({0}.data() + {1})")
def unsafe_read_u16(data: bytes, offset: Int32) -> UInt16: ...

@cpp_template("*reinterpret_cast<const int32_t*>({0}.data() + {1})")
def unsafe_read_i32(data: bytes, offset: Int32) -> Int32: ...

@cpp_template("*reinterpret_cast<const uint32_t*>({0}.data() + {1})")
def unsafe_read_u32(data: bytes, offset: Int32) -> UInt32: ...

@cpp_template("*reinterpret_cast<const int64_t*>({0}.data() + {1})")
def unsafe_read_i64(data: bytes, offset: Int32) -> Int64: ...

@cpp_template("*reinterpret_cast<const uint64_t*>({0}.data() + {1})")
def unsafe_read_u64(data: bytes, offset: Int32) -> UInt64: ...

@cpp_template("*reinterpret_cast<const float*>({0}.data() + {1})")
def unsafe_read_f32(data: bytes, offset: Int32) -> Float32: ...

@cpp_template("*reinterpret_cast<const double*>({0}.data() + {1})")
def unsafe_read_f64(data: bytes, offset: Int32) -> float: ...

@native("tpy::bytes_read_sub")
def unsafe_read_bytes(data: bytes, offset: Int32, count: Int32) -> bytes: ...

# unsafe_str_view: create a StrView from a pointer and length
@overload
@cpp_template("std::string_view({0}, {1})")
def unsafe_str_view(p: Ptr[Char], size: UInt32) -> StrView: ...

@overload
@cpp_template("std::string_view({0}, {1})")
def unsafe_str_view(p: Ptr[readonly[Char]], size: UInt32) -> StrView: ...

# unsafe_str_from_cstr: read a null-terminated C string into an owned TPy
# str. Caller ensures the pointer is valid and there is a \0 within the
# intended range. Typical use: wrap a `const char*` returned by a libc
# call (strerror, inet_ntop, gai_strerror, ...).
@cpp_template("std::string(reinterpret_cast<const char*>({0}))")
def unsafe_str_from_cstr(p: Ptr[readonly[UInt8]]) -> str: ...

# unsafe_str_from_buf: build an owned TPy str from a raw byte buffer of
# `size` bytes. No null terminator required. Typical use: decode the
# written portion of a pre-sized output buffer (pcre2 substitute, iconv,
# ...). Companion to unsafe_str_view above -- that one borrows, this one
# copies into owned storage.
@cpp_template("std::string(reinterpret_cast<const char*>({0}), static_cast<size_t>({1}))")
def unsafe_str_from_buf(p: Ptr[readonly[UInt8]], size: UInt64) -> str: ...

# unsafe_bytes_from_buf: construct an owned bytes from a raw byte buffer
# of `size` bytes. Caller ensures the pointer + range is valid for read.
# CPython's `bytes()` does not accept raw pointers (no buffer protocol
# exposure at that layer), so this can't be a bytes constructor overload;
# it lives here alongside other raw-memory constructors.
@cpp_template("std::vector<uint8_t>({0}, {0} + {1})")
def unsafe_bytes_from_buf(p: Ptr[UInt8], size: UInt64) -> bytes: ...
