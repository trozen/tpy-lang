/**
 * TurboPython Runtime - Core Utilities
 *
 * Panic handling and pointer operations.
 */

#pragma once

#include <cstdio>
#include <cstdlib>
#include <optional>

namespace tpy {

/**
 * Panic and abort - called on fatal runtime errors.
 */
[[noreturn]] inline void tpy_panic(const char* msg) {
    std::fprintf(stderr, "TurboPython panic: %s\n", msg);
    std::exit(1);
}

/**
 * Checked pointer dereference - panics if pointer is null.
 * Used for implicit Ptr[T] -> T coercion.
 */
template <typename T>
T& deref_ptr(T* ptr) {
    if (ptr == nullptr) {
        tpy_panic("null pointer dereference");
    }
    return *ptr;
}

template <typename T>
const T& deref_ptr(const T* ptr) {
    if (ptr == nullptr) {
        tpy_panic("null pointer dereference");
    }
    return *ptr;
}

/**
 * Checked optional dereference - panics if optional is empty.
 */
template <typename T>
T& deref_optional(std::optional<T>& opt) {
    if (!opt.has_value()) {
        tpy_panic("null optional dereference");
    }
    return *opt;
}

template <typename T>
const T& deref_optional(const std::optional<T>& opt) {
    if (!opt.has_value()) {
        tpy_panic("null optional dereference");
    }
    return *opt;
}

} // namespace tpy
