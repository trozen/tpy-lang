/**
 * TurboPython Runtime - UninitArrayStorage
 *
 * Inline uninitialized storage for N elements of type T.
 * Uses a union so elements are not default-constructed.
 * Caller is responsible for managing element lifetimes via
 * construct() / destroy().
 *
 * Copy/move: not explicitly deleted -- C++ union rules apply naturally:
 *   - Trivially copyable T: copy works (bytes are independent)
 *   - Non-trivially-copyable T: C++ implicitly deletes union copy
 */

#pragma once

#include <cstddef>
#include <cstdint>
#include <cstring>
#include <new>
#include <span>
#include <type_traits>
#include <utility>

#include "core.hpp"

#ifndef NDEBUG
#include <bitset>
#endif

namespace tpy {

template <typename T, std::size_t N>
class UninitArrayStorage {
public:
    UninitArrayStorage() noexcept {
#ifndef NDEBUG
        alive_.reset();
#endif
    }

    UninitArrayStorage(UninitArrayStorage&& other) noexcept {
        std::memcpy(static_cast<void*>(&elems_[0]), static_cast<const void*>(&other.elems_[0]), sizeof(elems_));
#ifndef NDEBUG
        alive_ = other.alive_;
        other.alive_.reset();
#endif
    }

    UninitArrayStorage& operator=(UninitArrayStorage&& other) noexcept {
        if (this != &other) {
            std::memcpy(static_cast<void*>(&elems_[0]), static_cast<const void*>(&other.elems_[0]), sizeof(elems_));
#ifndef NDEBUG
            alive_ = other.alive_;
            other.alive_.reset();
#endif
        }
        return *this;
    }

    ~UninitArrayStorage() {
#ifndef NDEBUG
        if (alive_.any()) {
            tpy_panic("UninitArrayStorage destroyed with live elements");
        }
#endif
    }

    void init(uint32_t i, T&& value) {
#ifndef NDEBUG
        if (i >= N) {
            tpy_panic("UninitArrayStorage::init index out of bounds");
        }
        if (alive_.test(i)) {
            tpy_panic("UninitArrayStorage::init on already-alive slot");
        }
        alive_.set(i);
#endif
        ::new (static_cast<void*>(&elems_[i])) T(std::move(value));
    }

    void init(uint32_t i, const T& value) {
#ifndef NDEBUG
        if (i >= N) {
            tpy_panic("UninitArrayStorage::init index out of bounds");
        }
        if (alive_.test(i)) {
            tpy_panic("UninitArrayStorage::init on already-alive slot");
        }
        alive_.set(i);
#endif
        ::new (static_cast<void*>(&elems_[i])) T(value);
    }

    void init0(T&& value) { init(0, std::move(value)); }
    void init0(const T& value) { init(0, value); }

    void drop(uint32_t i) {
#ifndef NDEBUG
        if (i >= N) {
            tpy_panic("UninitArrayStorage::drop index out of bounds");
        }
        if (!alive_.test(i)) {
            tpy_panic("UninitArrayStorage::drop on dead slot");
        }
        alive_.reset(i);
#endif
        elems_[i].~T();
    }

    void drop0() { drop(0); }

    T& load(uint32_t i) {
#ifndef NDEBUG
        if (i >= N) {
            tpy_panic("UninitArrayStorage::load index out of bounds");
        }
        if (!alive_.test(i)) {
            tpy_panic("UninitArrayStorage::load on dead slot");
        }
#endif
        return elems_[i];
    }

    const T& load(uint32_t i) const {
#ifndef NDEBUG
        if (i >= N) {
            tpy_panic("UninitArrayStorage::load index out of bounds");
        }
        if (!alive_.test(i)) {
            tpy_panic("UninitArrayStorage::load on dead slot");
        }
#endif
        return elems_[i];
    }

    T& load0() { return load(0); }
    const T& load0() const { return load(0); }

    T take(uint32_t i) {
#ifndef NDEBUG
        if (i >= N) {
            tpy_panic("UninitArrayStorage::take index out of bounds");
        }
        if (!alive_.test(i)) {
            tpy_panic("UninitArrayStorage::take on dead slot");
        }
        alive_.reset(i);
#endif
        T result = std::move(elems_[i]);
        elems_[i].~T();
        return result;
    }

    T take0() { return take(0); }

    T* ptr() { return elems_; }
    const T* ptr() const { return elems_; }

    static constexpr uint32_t capacity() { return static_cast<uint32_t>(N); }

    void init_from_span(uint32_t start, std::span<const T> src) {
#ifndef NDEBUG
        if (start + src.size() > N) {
            tpy_panic("UninitArrayStorage::init_from_span: span exceeds capacity");
        }
        for (std::size_t i = 0; i < src.size(); ++i) {
            if (alive_.test(start + i)) {
                tpy_panic("UninitArrayStorage::init_from_span on already-alive slot");
            }
            alive_.set(start + i);
        }
#endif
        if constexpr (std::is_trivially_copyable_v<T>) {
            std::memcpy(static_cast<void*>(&elems_[start]), src.data(), src.size() * sizeof(T));
        } else {
            for (std::size_t i = 0; i < src.size(); ++i) {
                ::new (static_cast<void*>(&elems_[start + i])) T(src[i]);
            }
        }
    }

    void drop_n(uint32_t start, uint32_t count) {
#ifndef NDEBUG
        for (uint32_t i = 0; i < count; ++i) {
            uint32_t idx = start + i;
            if (idx >= N) {
                tpy_panic("UninitArrayStorage::drop_n index out of bounds");
            }
            if (!alive_.test(idx)) {
                tpy_panic("UninitArrayStorage::drop_n on dead slot");
            }
            alive_.reset(idx);
        }
#endif
        if constexpr (!std::is_trivially_destructible_v<T>) {
            for (uint32_t i = 0; i < count; ++i) {
                elems_[start + i].~T();
            }
        }
    }

    void shift(uint32_t src, uint32_t dst, uint32_t count) {
        if (count == 0 || src == dst) return;
#ifndef NDEBUG
        for (uint32_t i = 0; i < count; ++i) {
            if (src + i >= N || dst + i >= N) {
                tpy_panic("UninitArrayStorage::shift index out of bounds");
            }
            if (!alive_.test(src + i)) {
                tpy_panic("UninitArrayStorage::shift on dead source slot");
            }
        }
        for (uint32_t i = 0; i < count; ++i) {
            alive_.reset(src + i);
        }
        for (uint32_t i = 0; i < count; ++i) {
            alive_.set(dst + i);
        }
#endif
        if constexpr (std::is_trivially_copyable_v<T>) {
            std::memmove(static_cast<void*>(&elems_[dst]),
                         static_cast<const void*>(&elems_[src]),
                         static_cast<std::size_t>(count) * sizeof(T));
        } else if (dst < src) {
            for (uint32_t i = 0; i < count; ++i) {
                ::new (static_cast<void*>(&elems_[dst + i]))
                    T(std::move(elems_[src + i]));
                elems_[src + i].~T();
            }
        } else {
            for (uint32_t i = count; i > 0; --i) {
                uint32_t idx = i - 1;
                ::new (static_cast<void*>(&elems_[dst + idx]))
                    T(std::move(elems_[src + idx]));
                elems_[src + idx].~T();
            }
        }
    }

    // TODO: emplace(i, args...) for perfect-forwarding construction

private:
    union { T elems_[N]; };

#ifndef NDEBUG
    std::bitset<N> alive_;
#endif
};

} // namespace tpy
