/**
 * TurboPython Runtime Header
 *
 * Provides the core types and utilities for TurboPython compiled code:
 * - StaticList<T, N>: Fixed-capacity container
 * - tpy_panic(): Abort on fatal error
 */

#pragma once

#include <cstdint>
#include <cstdlib>
#include <cstdio>

namespace tpy {

/**
 * Panic and abort - called on fatal runtime errors.
 */
[[noreturn]] inline void tpy_panic(const char* msg) {
    std::fprintf(stderr, "TurboPython panic: %s\n", msg);
    std::abort();
}

/**
 * StaticList<T, N> - Fixed-capacity container.
 *
 * No dynamic allocation. Elements are stored inline.
 * Provides append, push_empty, get, get_mut, set, and size operations.
 */
template <typename T, std::size_t N>
class StaticList {
public:
    StaticList() noexcept : size_(0) {}

    /**
     * Append a value to the list.
     * Panics if capacity is exceeded.
     */
    void append(const T& value) {
        if (size_ >= N) {
            tpy_panic("StaticList capacity exceeded in append()");
        }
        data_[size_++] = value;
    }

    /**
     * Push an empty element and return a pointer to it.
     * Panics if capacity is exceeded.
     */
    T* push_empty() {
        if (size_ >= N) {
            tpy_panic("StaticList capacity exceeded in push_empty()");
        }
        return &data_[size_++];
    }

    /**
     * Get a const pointer to element at index.
     * Panics if index is out of bounds.
     */
    const T* get(int32_t index) const {
        auto i = static_cast<std::size_t>(index);
        if (i >= size_) {
            tpy_panic("StaticList index out of bounds in get()");
        }
        return &data_[i];
    }

    /**
     * Get a mutable pointer to element at index.
     * Panics if index is out of bounds.
     */
    T* get_mut(int32_t index) {
        auto i = static_cast<std::size_t>(index);
        if (i >= size_) {
            tpy_panic("StaticList index out of bounds in get_mut()");
        }
        return &data_[i];
    }

    /**
     * Set element at index to value.
     * Panics if index is out of bounds.
     */
    void set(int32_t index, const T& value) {
        auto i = static_cast<std::size_t>(index);
        if (i >= size_) {
            tpy_panic("StaticList index out of bounds in set()");
        }
        data_[i] = value;
    }

    /**
     * Return current size.
     */
    int32_t size() const noexcept {
        return static_cast<int32_t>(size_);
    }

    /**
     * Return maximum capacity.
     */
    static constexpr std::size_t capacity() noexcept {
        return N;
    }

private:
    T data_[N];
    std::size_t size_;
};

} // namespace tpy

// Expose types in global namespace for TurboPython generated code
using tpy::StaticList;
using tpy::tpy_panic;
