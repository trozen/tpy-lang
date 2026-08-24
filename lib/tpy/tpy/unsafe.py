# tpy: native_module
from typing import overload
from tpy.extern import native, cpp_template
from tpy import Ptr, Own, Int8, Int16, Int32, Int64, UInt8, UInt16, UInt32, UInt64, Float32, StrView, String, Char, Array, readonly

# unsafe_ptr: get a raw pointer from a container or string. The `str`
# overload points at view storage that is NOT null-terminated -- use
# unsafe_cstr() when handing a string to a C function.
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
#
# Uses `{cpp}` (the return-type spelling) rather than `{T}*` so the
# PtrType -> void* special-case for `T = None` is preserved -- bare
# `{T}*` substitutes T via to_cpp_stored() and produces
# `std::monostate*`, defeating the opaque-pointer-for-@native-C-interop
# convention of `Ptr[None]`.
@overload
@cpp_template("reinterpret_cast<{cpp}>({0})")
def unsafe_cast[T, U](p: Ptr[U]) -> Ptr[T]: ...

@overload
@cpp_template("reinterpret_cast<{cpp}>({0})")
def unsafe_cast[T, U](p: Ptr[readonly[U]]) -> Ptr[readonly[T]]: ...

# unsafe_load: read a value through a pointer at offset
@overload
@cpp_template("{0}[{1}]")
def unsafe_load[T](p: Ptr[T], offset: UInt32) -> Own[T]: ...

@overload
@cpp_template("{0}[{1}]")
def unsafe_load[T](p: Ptr[readonly[T]], offset: UInt32) -> Own[T]: ...

# unsafe_store: move a value into the pointee at offset. `Own[T]` (not a borrow):
# the pointee is owned storage, so a borrowed reference-type value would be copied
# where the source stays aliased -- consuming it keeps the copy from being silent.
@cpp_template("{0}[{1}] = {2}")
def unsafe_store[T](p: Ptr[T], offset: UInt32, value: Own[T]) -> None: ...

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

# unsafe_take: heap-allocate and move-in a value (combined alloc + init).
# Abstract-T case is handled by C++ overload resolution: heap_take has
# both a T&& primary and a unique_ptr<T> overload.
@native("tpy::heap_take")
def unsafe_take[T](value: Own[T]) -> Ptr[T]: ...

# unsafe_release: destruct and free a heap-allocated T (combined drop + free).
@native("tpy::heap_release")
def unsafe_release[T](p: Ptr[T]) -> None: ...

# unsafe_replace: replace the heap-stored T at p with value, returning the
# live slot pointer. Caller must write the returned pointer back to its
# storage slot -- for abstract @dynamic T the slot changes.
# Precondition: value must not alias *p.
@native("tpy::heap_replace")
def unsafe_replace[T](p: Ptr[T], value: Own[T]) -> Ptr[T]: ...

# unsafe_transfer_ownership: adopt a heap-allocated Ptr[T] as Own[T].
# After the call the input pointer is invalidated.
@native("tpy::transfer_ownership")
def unsafe_transfer_ownership[T](p: Ptr[T]) -> Own[T]: ...

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

# unsafe_cstr: borrow an owned String as a null-terminated C string. The
# outbound half of the unsafe_str_from_cstr pair. The String parameter
# names the form that is actually safe to borrow -- owned, so its buffer
# is null-terminated and outlives the call -- but it does not ENFORCE it:
# a str argument converts, and what the pointer then addresses is the
# conversion's temporary.
#
# The result borrows the argument's buffer and, like every unsafe_ptr
# overload, is not tracked: it stays valid only as long as that buffer
# does. Passing a `str` (or a literal) converts, so the string the pointer
# points into dies with the enclosing expression -- hold the pointer past
# that statement only when the argument was a String the caller owns, and
# only while that String is neither mutated nor reallocated.
@native("tpy::cstr")
def unsafe_cstr(s: String) -> Ptr[readonly[UInt8]]: ...

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
@native("tpy::bytes_from_buf")
def unsafe_bytes_from_buf(p: Ptr[UInt8], size: UInt64) -> bytes: ...
