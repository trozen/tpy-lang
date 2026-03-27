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

// Range utilities (no dependencies)
#include "ranges.hpp"

// Range iterator (depends on core, fixed_int)
#include "range.hpp"

// Uninitialized storage (depends on core)
#include "uninit_array_storage.hpp"
#include "uninit_heap_storage.hpp"

// BigInt arbitrary precision (depends on core, fixed_int, type_traits)
#include "bigint.hpp"

// Builtin function helpers (depends on core, fixed_int, bigint)
#include "builtins.hpp"

// Slice type for user-defined __getitem__ overloads (no dependencies)
#include "slice.hpp"

// Container operations (depends on core, type_traits)
#include "container_ops.hpp"

// Protocols and concepts (depends on ranges)
#include "protocols.hpp"

// next_iter adapter: begin()/end() for __next__()-based iterators
#include "next_iter.hpp"

// Generator expression wrapper (depends on next_iter, <optional>, <expected>)
#include "generator.hpp"

// SpanIter: lightweight iterator over contiguous span (depends on <span>, error_return)
#include "span_iter.hpp"

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

// Math helpers for lib/stdlib/math.py @native declarations (log_base wrapper)
#include "math_ops.hpp"

// Pointer-variant utilities for non-value union types (depends on <variant>)
#include "variant_ref.hpp"

// RAII guard for `with` statement context managers (no dependencies)
#include "with_guard.hpp"

// File I/O: TextFile for open() builtin (depends on core)
#include "file.hpp"

// Expose types in global namespace for TurboPython generated code
using ::tpy::UninitArrayStorage;
using ::tpy::UninitHeapStorage;
using ::tpy::tpy_panic;
using ::tpy::BigInt;
using ::tpy::BaseException;
using ::tpy::Exception;
using ::tpy::StopIteration;
