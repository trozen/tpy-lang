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

// Iterator adapter: __next_opt__() -> C++ begin/end (depends on <optional>)
#include "iter_adapt.hpp"

// SpanIter: lightweight iterator over contiguous span (depends on <span>, <optional>)
#include "span_iter.hpp"

// Non-range overloads for container ops (depends on iter_adapt, container_ops)
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

// System utilities (depends on core)
#include "system.hpp"

// Math helpers for lib/stdlib/math.py @native declarations (log_base wrapper)
#include "math_ops.hpp"

// Expose types in global namespace for TurboPython generated code
using ::tpy::UninitArrayStorage;
using ::tpy::UninitHeapStorage;
using ::tpy::tpy_panic;
using ::tpy::BigInt;
