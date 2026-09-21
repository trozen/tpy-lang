/**
 * TurboPython Runtime - Main Header
 *
 * Includes all runtime components. This is the primary header for
 * TurboPython generated code.
 *
 * Requires C++23 for std::ranges concepts.
 */

#pragma once

// Core utilities (no dependencies)
#include "core.hpp"

// Async primitives: CancelledError only. Waker / Awaker / Poll<T> /
// Awaitable<T> are pure-TPy in `lib/tpy/tpy/coro/__init__.py` (Waker,
// Awaker, Awaitable concept) and `lib/tpy/tpy/_core/_types.py` (Poll).
#include "async.hpp"

// Enum utility trait (primary template; specializations in generated code)
#include "enum.hpp"

// Dynamic protocol adapters (primary templates; specializations in generated code)
#include "dynamic.hpp"

// Formatting (no dependencies)
#include "format.hpp"

// Fixed-width integer checked arithmetic (depends on core)
#include "fixed_int.hpp"

// Type traits (no dependencies)
#include "type_traits.hpp"

// The owning str/bytes buffer classes and their trait rows (depends on
// type_traits); must precede every header that names them.
#include "buffer_types.hpp"

// Range utilities (no dependencies)
#include "ranges.hpp"

// Range iterator (depends on core, fixed_int)
#include "range.hpp"

// Uninitialized storage (depends on core)
#include "uninit_array_storage.hpp"
#include "uninit_heap_storage.hpp"
#include "frame_slot.hpp"
#include "uninit_storage.hpp"

// BigInt arbitrary precision (depends on core, fixed_int, type_traits)
#include "bigint.hpp"

// Unions at every position: `::tpy::Union<Ts...>`, a std::variant that owns
// Python's comparison rule (and the leaf it visits down to); the alternative
// pack says whether it owns or borrows. Pulls core, bigint and type_name in
// itself (type_name is self-contained: it only needs core, and
// forward-declares BigInt), so it does not wait for the type_name.hpp line
// further down.
#include "union_type.hpp"

// Builtin function helpers (depends on core, fixed_int, bigint)
#include "builtins.hpp"

// Slice type for user-defined __getitem__ overloads (no dependencies)
#include "slice.hpp"

// Key comparison/hashing for container lookups (no runtime dependencies)
#include "lookup_key.hpp"

// Container operations (depends on core, type_traits, lookup_key)
#include "container_ops.hpp"

// Protocols and concepts (depends on ranges)
#include "protocols.hpp"

// next_iter adapter: begin()/end() for __next__()-based iterators
#include "next_iter.hpp"

// Resumable-frame iteration helpers (depends on next_iter, frame_slot, dunder)
#include "generator.hpp"

// SpanIter: lightweight iterator over contiguous span (depends on <span>, error_return)
#include "span_iter.hpp"

// varargs<T>: dual-mode span for *args (direct contiguous or indirect pointer array)
#include "varargs.hpp"

// OwnIter: drain iterator for std::vector (depends on core)
#include "own_iter.hpp"

// CopyIter: copying iterator adapter (depends on dunder for __iter__)
#include "copy_iter.hpp"

// Iterator builtins: enumerate, reversed (depends on next_iter, dunder)
#include "itertools.hpp"

// Non-range overloads for container ops (depends on dunder, container_ops)
#include "iterable_ops.hpp"

// Collection printing (depends on bigint)
#include "printing.hpp"

// Ordered map (no runtime dependencies beyond standard library)
#include "ordered_map.hpp"

// Ordered set (no runtime dependencies beyond standard library)
#include "ordered_set.hpp"

// Dict operations and printing (depends on ordered_map, core, printing)
#include "dict_ops.hpp"

// Set operations and printing (depends on ordered_set, core, printing)
#include "set_ops.hpp"

// Bytes operations and printing (depends on core, container_ops)
#include "bytes_ops.hpp"

// System utilities (depends on core)
#include "system.hpp"

// Pointer-variant utilities for non-value union types (depends on <variant>)
#include "variant_ref.hpp"

// File I/O: TextFile for open() builtin (depends on core)
#include "file.hpp"

// User-facing TPy type names (depends on core, plus every type it
// specializes for -- so it must come after the type headers above)
#include "type_name.hpp"

// Any: type-erased value cell (depends on dunder, builtins, core, type_name)
#include "any.hpp"

// Output-sink dispatch for print(file=...) (depends on system, file)
#include "as_ostream.hpp"

// repr_of: user-facing repr dispatch helper. Defined here, after every
// header that contributes a tpy::__repr__ overload, so unqualified
// `__repr__(x)` inside its body sees the full overload set. Goes through
// here (not direct `::tpy::__repr__`) so ADL into the argument's
// namespace can find per-record overrides; qualified callers from inside
// runtime templates would freeze the candidate set at template-definition
// time and miss them.
namespace tpy {
template<typename T>
inline auto repr_of(const T& x) {
    using ::tpy::__repr__;
    return __repr__(x);
}

// Pointer-repr Optional[Record] (`A | None` lowers to nullable `A*`):
// emit "None" for null, otherwise repr the pointee. Restricted to class
// pointers so raw `int*`/`char*` don't get caught (the latter has its
// own __repr__ for string literals).
template<typename T>
    requires std::is_class_v<T>
inline std::string repr_of(T* p) {
    if (!p) return "None";
    using ::tpy::__repr__;
    return std::string(__repr__(*p));
}
} // namespace tpy

// Expose types in global namespace for TurboPython generated code
using ::tpy::UninitArrayStorage;
using ::tpy::UninitHeapStorage;
using ::tpy::tpy_panic;
using ::tpy::BigInt;
using ::tpy::BaseException;
using ::tpy::Exception;
using ::tpy::StopIteration;
