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
#include <new>
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

    // TODO: emplace(i, args...) for perfect-forwarding construction

private:
    union { T elems_[N]; };

#ifndef NDEBUG
    std::bitset<N> alive_;
#endif
};

} // namespace tpy
