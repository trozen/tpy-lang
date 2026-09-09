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

    // Readonly view of a mutable varargs<U> -- forwarding `*xs` where `xs` is a
    // mutable `*args` param into a `readonly[...]` vararg. Present only when T
    // is const. The pointer-array conversion (`U* const*` -> `const U* const*`,
    // i.e. `T* const*`) is a plain implicit qualification conversion -- legal
    // because the enclosing pointer level is already const -- not a reinterpret.
    template<typename U = T, std::enable_if_t<std::is_const_v<U>, int> = 0>
    varargs(const varargs<std::remove_const_t<U>>& o)
        : direct_(o.direct_),
          indirect_(o.indirect_),
          size_(o.size_) {}

    T& operator[](int32_t i) const {
        return indirect_ ? *indirect_[i] : direct_[i];
    }

    int32_t size() const { return size_; }
    bool empty() const { return size_ == 0; }

    // Python-style slice preserving direct-vs-indirect mode. Returns another
    // varargs of the same instantiation with adjusted pointers + size -- the
    // only correct shape, since indirect-mode storage (`T* const*`) cannot be
    // exposed as `std::span<T>`. Indices follow Python clamp semantics.
    varargs slice(int32_t start, int32_t stop) const {
        int32_t i = start;
        int32_t j = stop;
        if (i < 0) i += size_;
        if (j < 0) j += size_;
        if (i < 0) i = 0;
        if (j < 0) j = 0;
        if (i > size_) i = size_;
        if (j > size_) j = size_;
        varargs r;
        if (i >= j) return r;
        r.size_ = j - i;
        if (direct_) r.direct_ = direct_ + i;
        if (indirect_) r.indirect_ = indirect_ + i;
        return r;
    }

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

    // Readonly view of a mutable value-type varargs<U> (forwarding into a
    // `readonly[...]` value-type vararg). Present only when T is const.
    template<typename U = T, std::enable_if_t<std::is_const_v<U>, int> = 0>
    varargs(const varargs<std::remove_const_t<U>>& o)
        : data_(o.data_), size_(o.size_) {}

    T& operator[](int32_t i) const { return data_[i]; }
    int32_t size() const { return size_; }
    bool empty() const { return size_ == 0; }
    T* data() const { return data_; }

    // Python-style slice -- returns another value-type varargs (not a
    // std::span) so the *args body view stays a varargs across slicing,
    // mirroring the non-value specialization.
    varargs slice(int32_t start, int32_t stop) const {
        int32_t i = start;
        int32_t j = stop;
        if (i < 0) i += size_;
        if (j < 0) j += size_;
        if (i < 0) i = 0;
        if (j < 0) j = 0;
        if (i > size_) i = size_;
        if (j > size_) j = size_;
        varargs r;
        if (i >= j) return r;
        r.data_ = data_ + i;
        r.size_ = j - i;
        return r;
    }

    using iterator = T*;
    T* begin() const { return data_; }
    T* end() const { return data_ + size_; }
};

// A value type (the body view of *args is copied, not aliased, at a generic
// slot) that nonetheless borrows the caller's backing storage, so it is never
// Send; Sync mirrors span -- a read-only element view is Sync if the element is.
template<typename T, bool V> struct is_value_type<varargs<T, V>> : std::true_type {};
template<typename T, bool V> struct is_send<varargs<T, V>> : std::false_type {};
template<typename T, bool V> struct is_sync<varargs<T, V>> : std::false_type {};
template<typename T, bool V> struct is_sync<varargs<const T, V>> : is_sync<T> {};

template<typename T, bool V>
int32_t __len__(const varargs<T, V>& v) { return v.size(); }

template<typename T, bool V>
T& __getitem__(const varargs<T, V>& v, int32_t i) { return v[i]; }

} // namespace tpy

#include "container_ops.hpp"

namespace tpy {

// list_slice on a non-value varargs returns another varargs (preserving
// direct-vs-indirect mode), not std::span<T> -- the generic Container
// list_slice in container_ops.hpp would call `c.data()` which non-value
// varargs intentionally doesn't expose (indirect storage is `T* const*`,
// not contiguous T). The value-type varargs path stays on the generic
// template via its own `data()` (defined in the value-type specialization).
template<typename T>
varargs<T, false> list_slice(varargs<T, false>& c, int32_t start, int32_t stop) {
    return c.slice(start, stop);
}

template<typename T>
varargs<T, false> list_slice(const varargs<T, false>& c, int32_t start, int32_t stop) {
    return c.slice(start, stop);
}

template<typename T>
varargs<T, false> list_slice(varargs<T, false>& c, BasicSlice sl) {
    return c.slice(sl.start.value_or(0), sl.stop.value_or(SLICE_END));
}

template<typename T>
varargs<T, false> list_slice(const varargs<T, false>& c, BasicSlice sl) {
    return c.slice(sl.start.value_or(0), sl.stop.value_or(SLICE_END));
}

// Value-type varargs slice also returns a varargs (not std::span), so a
// sliced *args stays a body view -- overrides the generic Container list_slice
// that would otherwise produce std::span<T> via the value specialization's
// data().
template<typename T>
varargs<T, true> list_slice(varargs<T, true>& c, int32_t start, int32_t stop) {
    return c.slice(start, stop);
}

template<typename T>
varargs<T, true> list_slice(const varargs<T, true>& c, int32_t start, int32_t stop) {
    return c.slice(start, stop);
}

template<typename T>
varargs<T, true> list_slice(varargs<T, true>& c, BasicSlice sl) {
    return c.slice(sl.start.value_or(0), sl.stop.value_or(SLICE_END));
}

template<typename T>
varargs<T, true> list_slice(const varargs<T, true>& c, BasicSlice sl) {
    return c.slice(sl.start.value_or(0), sl.stop.value_or(SLICE_END));
}

} // namespace tpy
