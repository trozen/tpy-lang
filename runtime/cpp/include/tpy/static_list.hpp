/**
 * TurboPython Runtime - StaticList
 *
 * Fixed-capacity container with std::vector-like interface.
 * No dynamic allocation - elements stored inline.
 */

#pragma once

#include <array>
#include <cstddef>
#include <cstdint>
#include <initializer_list>
#include <iterator>
#include <span>
#include <utility>
#include <vector>

#include "core.hpp"

namespace tpy {

/**
 * StaticList<T, N> - Fixed-capacity container with std::vector-like interface.
 *
 * No dynamic allocation. Elements are stored inline.
 *
 * NOTE: Elements are not destroyed on pop_back()/clear() - they remain alive
 * until the container is destroyed. This is fine for trivial types but diverges
 * from std::vector for types with non-trivial destructors. Future fix: use
 * aligned storage with placement new/destroy.
 */
template <typename T, std::size_t N>
class StaticList {
public:
    StaticList() noexcept : size_(0) {}

    StaticList(std::initializer_list<T> init) : size_(0) {
        if (init.size() > N) {
            tpy_panic("StaticList initializer exceeds capacity");
        }
        for (const auto& val : init) {
            data_[size_++] = val;
        }
    }

    StaticList(std::size_t count, const T& value) : size_(0) {
        if (count > N) {
            tpy_panic("StaticList fill count exceeds capacity");
        }
        for (std::size_t i = 0; i < count; ++i) {
            data_[size_++] = value;
        }
    }

    // Iterator-pair constructor
    template<std::input_iterator InputIt>
        requires std::convertible_to<std::iter_value_t<InputIt>, T>
    StaticList(InputIt first, InputIt last) : size_(0) {
        for (; first != last; ++first) {
            if (size_ >= N) {
                tpy_panic("StaticList capacity exceeded");
            }
            data_[size_++] = *first;
        }
    }

    // Span constructor
    StaticList(std::span<const T> items) : size_(0) {
        if (items.size() > N) {
            tpy_panic("StaticList capacity exceeded");
        }
        for (const auto& val : items) {
            data_[size_++] = val;
        }
    }

    // --- std::vector-compatible interface ---

    void push_back(const T& value) {
        if (size_ >= N) {
            tpy_panic("StaticList capacity exceeded");
        }
        data_[size_++] = value;
    }

    void push_back(T&& value) {
        if (size_ >= N) {
            tpy_panic("StaticList capacity exceeded");
        }
        data_[size_++] = std::move(value);
    }

    T pop_back() {
        if (size_ == 0) {
            tpy_panic("StaticList pop from empty list");
        }
        return std::move(data_[--size_]);
    }

    void clear() noexcept {
        size_ = 0;
    }

    T& operator[](std::size_t i) { return data_[i]; }
    const T& operator[](std::size_t i) const { return data_[i]; }

    int32_t size() const noexcept { return static_cast<int32_t>(size_); }
    static constexpr std::size_t capacity() noexcept { return N; }
    bool empty() const noexcept { return size_ == 0; }

    T* data() noexcept { return data_; }
    const T* data() const noexcept { return data_; }

    // Iterator support
    using iterator = T*;
    using const_iterator = const T*;

    iterator begin() noexcept { return data_; }
    const_iterator begin() const noexcept { return data_; }
    iterator end() noexcept { return data_ + size_; }
    const_iterator end() const noexcept { return data_ + size_; }

    // --- StaticList-specific (noalloc patterns) ---

    T* push_empty() {
        if (size_ >= N) {
            tpy_panic("StaticList capacity exceeded");
        }
        return &data_[size_++];
    }

private:
    T data_[N];
    std::size_t size_;
};

// --- Span helpers ---

template <typename T, std::size_t N>
inline std::span<const T> as_span(const std::array<T, N>& arr) {
    return std::span<const T>(arr);
}

template <typename T, std::size_t N>
inline std::span<const T> as_span(const StaticList<T, N>& list) {
    return std::span<const T>(list.data(), list.size());
}

template <typename T>
inline std::span<const T> as_span(const std::vector<T>& vec) {
    return std::span<const T>(vec.data(), vec.size());
}

template <typename T>
inline std::span<const T> as_span(std::span<const T> span) {
    return span;
}

// Mutable span to const span (implicit const conversion)
template <typename T>
inline std::span<const T> as_span(std::span<T> span) {
    return span;
}

// User types with __span__(): delegate to their method.
// The !contiguous_range guard avoids ambiguity with builtins (vector, array, StaticList)
// which have their own as_span overloads above.
template <typename T>
    requires requires(const T& t) { t.__span__(); }
        && (!std::ranges::contiguous_range<T>)
inline auto as_span(const T& t) {
    return t.__span__();
}

// --- Mutable span helpers ---

template <typename T, std::size_t N>
inline std::span<T> as_mut_span(std::array<T, N>& arr) {
    return std::span<T>(arr);
}

// Rvalue overload: safe when span is consumed within the full-expression (ARG context)
template <typename T, std::size_t N>
inline std::span<T> as_mut_span(std::array<T, N>&& arr) {
    return std::span<T>(arr.data(), arr.size());
}

template <typename T, std::size_t N>
inline std::span<T> as_mut_span(StaticList<T, N>& list) {
    return std::span<T>(list.data(), list.size());
}

template <typename T>
inline std::span<T> as_mut_span(std::vector<T>& vec) {
    return std::span<T>(vec.data(), vec.size());
}

template <typename T>
inline std::span<T> as_mut_span(std::span<T> span) {
    return span;
}

} // namespace tpy
