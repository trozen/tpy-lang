/**
 * TurboPython Runtime - varargs<T>
 *
 * Span-like view for *args parameters.
 *
 * Value types (is_value_type_v<T>): thin wrapper around contiguous T storage.
 *   No indirection, no branches -- equivalent to std::span<T>.
 *
 * Non-value types: dual-mode view.
 *   Direct mode: contiguous T storage (*list unpacking into vector/array).
 *   Indirect mode: T* pointer array (individual args packed by address).
 *   operator[] returns T& in both modes.
 */

#pragma once

#include "type_traits.hpp"

#include <array>
#include <cstdint>
#include <span>

namespace tpy {

// Primary template: non-value types (dual-mode)
template<typename T, bool IsValue = is_value_type_v<T>>
struct varargs {
    T* direct_ = nullptr;
    T* const* indirect_ = nullptr;
    int32_t size_ = 0;

    varargs() = default;

    // Direct mode: contiguous T storage (*list unpacking)
    varargs(std::span<T> s) : direct_(s.data()), size_(static_cast<int32_t>(s.size())) {}

    template<size_t N>
    varargs(std::array<T, N>& arr) : direct_(arr.data()), size_(static_cast<int32_t>(N)) {}

    // Indirect mode: pointer array (individual args)
    template<size_t N>
    varargs(std::array<T*, N>& arr) : indirect_(arr.data()), size_(static_cast<int32_t>(N)) {}

    T& operator[](int32_t i) const {
        return indirect_ ? *indirect_[i] : direct_[i];
    }

    int32_t size() const { return size_; }
    bool empty() const { return size_ == 0; }

    struct iterator {
        T* direct_;
        T* const* indirect_;

        T& operator*() const { return indirect_ ? **indirect_ : *direct_; }

        iterator& operator++() {
            if (indirect_) ++indirect_; else ++direct_;
            return *this;
        }

        bool operator==(const iterator& o) const {
            return indirect_ ? indirect_ == o.indirect_ : direct_ == o.direct_;
        }
        bool operator!=(const iterator& o) const { return !(*this == o); }
    };

    iterator begin() const { return {direct_, indirect_}; }
    iterator end() const {
        return {direct_ ? direct_ + size_ : nullptr,
                indirect_ ? indirect_ + size_ : nullptr};
    }
};

// Value type specialization: direct only, no indirection overhead
template<typename T>
struct varargs<T, true> {
    T* data_ = nullptr;
    int32_t size_ = 0;

    varargs() = default;

    varargs(std::span<T> s) : data_(s.data()), size_(static_cast<int32_t>(s.size())) {}

    template<size_t N>
    varargs(std::array<T, N>& arr) : data_(arr.data()), size_(static_cast<int32_t>(N)) {}

    T& operator[](int32_t i) const { return data_[i]; }
    int32_t size() const { return size_; }
    bool empty() const { return size_ == 0; }
    T* data() const { return data_; }

    using iterator = T*;
    T* begin() const { return data_; }
    T* end() const { return data_ + size_; }
};

template<typename T, bool V>
int32_t __len__(const varargs<T, V>& v) { return v.size(); }

template<typename T, bool V>
T& __getitem__(const varargs<T, V>& v, int32_t i) { return v[i]; }

} // namespace tpy
