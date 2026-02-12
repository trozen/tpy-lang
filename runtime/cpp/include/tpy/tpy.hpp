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

// Formatting (no dependencies)
#include "format.hpp"

// Int32 checked arithmetic (depends on core)
#include "int32.hpp"

// Type traits (no dependencies)
#include "type_traits.hpp"

// Range utilities (no dependencies)
#include "ranges.hpp"

// Range iterator (depends on core, int32)
#include "range.hpp"

// StaticList (depends on core)
#include "static_list.hpp"

// BigInt arbitrary precision (depends on core, int32, type_traits)
#include "bigint.hpp"

// Container operations (depends on core, type_traits, static_list)
#include "container_ops.hpp"

// Protocols and concepts (depends on static_list, ranges)
#include "protocols.hpp"

// Collection printing (depends on static_list, bigint)
#include "printing.hpp"

// System utilities (depends on core)
#include "system.hpp"

// Expose types in global namespace for TurboPython generated code
using tpy::StaticList;
using tpy::tpy_panic;
using tpy::BigInt;
