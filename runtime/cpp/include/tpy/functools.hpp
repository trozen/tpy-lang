/**
 * TurboPython Runtime - Functional tools
 *
 * C++ implementations of functools module functions.
 * Workaround: TPy codegen emits both an unconstrained forward declaration and a
 * constrained definition for generic+Fn functions in the same header, which GCC
 * resolves as two overload candidates causing ambiguity. Implementing here avoids
 * the issue entirely.
 */

#pragma once

#include <vector>

namespace tpy {

template<typename T, typename U, typename Fn>
U functools_reduce(Fn&& fn, const std::vector<T>& iterable, U initial) {
    U result = std::move(initial);
    for (const auto& item : iterable) {
        result = fn(result, item);
    }
    return result;
}

} // namespace tpy
