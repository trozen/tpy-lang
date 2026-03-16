// Error return types for @error_return decorator.
// These are zero-size sentinel types used with std::expected<T, E>.

#pragma once

#include <expected>

namespace tpy {

// Python exception base classes (empty in TPy, used for CPython compatibility)
struct BaseException {};
struct Exception : BaseException {};

struct StopIteration {};

}  // namespace tpy
