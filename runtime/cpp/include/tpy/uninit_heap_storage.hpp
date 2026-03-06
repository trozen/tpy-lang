/**
 * TurboPython Runtime - UninitHeapStorage
 *
 * Heap-allocated uninitialized storage for T elements.
 * Allocates raw memory for `capacity` elements via ::operator new.
 * Caller is responsible for managing element lifetimes via
 * construct() / destroy().
 *
 * Non-copyable but movable: copy would alias heap memory, move transfers ownership.
 * To grow: allocate new storage, move elements over, destroy old, move-assign.
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
#include <vector>
#endif

namespace tpy {

template <typename T>
class UninitHeapStorage {
public:
    UninitHeapStorage(uint32_t capacity)
        : data_(static_cast<T*>(::operator new(sizeof(T) * capacity, std::align_val_t(alignof(T)))))
        , capacity_(capacity)
#ifndef NDEBUG
        , alive_(capacity, false)
#endif
    {}

    ~UninitHeapStorage() {
#ifndef NDEBUG
        for (std::size_t i = 0; i < capacity_; ++i) {
            if (alive_[i]) {
                tpy_panic("UninitHeapStorage destroyed with live elements");
            }
        }
#endif
        if (data_) {
            ::operator delete(data_, std::align_val_t(alignof(T)));
        }
    }

    // Non-copyable
    UninitHeapStorage(const UninitHeapStorage&) = delete;
    UninitHeapStorage& operator=(const UninitHeapStorage&) = delete;

    // Movable (transfers ownership, source becomes empty)
    UninitHeapStorage(UninitHeapStorage&& other) noexcept
        : data_(other.data_)
        , capacity_(other.capacity_)
#ifndef NDEBUG
        , alive_(std::move(other.alive_))
#endif
    {
        other.data_ = nullptr;
        other.capacity_ = 0;
    }

    UninitHeapStorage& operator=(UninitHeapStorage&& other) noexcept {
        if (this != &other) {
#ifndef NDEBUG
            for (std::size_t i = 0; i < capacity_; ++i) {
                if (alive_[i]) {
                    tpy_panic("UninitHeapStorage move-assigned with live elements");
                }
            }
#endif
            if (data_) {
                ::operator delete(data_, std::align_val_t(alignof(T)));
            }
            data_ = other.data_;
            capacity_ = other.capacity_;
#ifndef NDEBUG
            alive_ = std::move(other.alive_);
#endif
            other.data_ = nullptr;
            other.capacity_ = 0;
        }
        return *this;
    }

    void init(uint32_t i, T&& value) {
#ifndef NDEBUG
        if (i >= capacity_) {
            tpy_panic("UninitHeapStorage::init index out of bounds");
        }
        if (alive_[i]) {
            tpy_panic("UninitHeapStorage::init on already-alive slot");
        }
        alive_[i] = true;
#endif
        ::new (static_cast<void*>(&data_[i])) T(std::move(value));
    }

    void init(uint32_t i, const T& value) {
#ifndef NDEBUG
        if (i >= capacity_) {
            tpy_panic("UninitHeapStorage::init index out of bounds");
        }
        if (alive_[i]) {
            tpy_panic("UninitHeapStorage::init on already-alive slot");
        }
        alive_[i] = true;
#endif
        ::new (static_cast<void*>(&data_[i])) T(value);
    }

    void init0(T&& value) { init(0, std::move(value)); }
    void init0(const T& value) { init(0, value); }

    void drop(uint32_t i) {
#ifndef NDEBUG
        if (i >= capacity_) {
            tpy_panic("UninitHeapStorage::drop index out of bounds");
        }
        if (!alive_[i]) {
            tpy_panic("UninitHeapStorage::drop on dead slot");
        }
        alive_[i] = false;
#endif
        data_[i].~T();
    }

    void drop0() { drop(0); }

    T& load(uint32_t i) {
#ifndef NDEBUG
        if (i >= capacity_) {
            tpy_panic("UninitHeapStorage::load index out of bounds");
        }
        if (!alive_[i]) {
            tpy_panic("UninitHeapStorage::load on dead slot");
        }
#endif
        return data_[i];
    }

    const T& load(uint32_t i) const {
#ifndef NDEBUG
        if (i >= capacity_) {
            tpy_panic("UninitHeapStorage::load index out of bounds");
        }
        if (!alive_[i]) {
            tpy_panic("UninitHeapStorage::load on dead slot");
        }
#endif
        return data_[i];
    }

    T& load0() { return load(0); }
    const T& load0() const { return load(0); }

    T take(uint32_t i) {
#ifndef NDEBUG
        if (i >= capacity_) {
            tpy_panic("UninitHeapStorage::take index out of bounds");
        }
        if (!alive_[i]) {
            tpy_panic("UninitHeapStorage::take on dead slot");
        }
        alive_[i] = false;
#endif
        T result = std::move(data_[i]);
        data_[i].~T();
        return result;
    }

    T take0() { return take(0); }

    T* ptr() { return data_; }
    const T* ptr() const { return data_; }

    uint32_t capacity() const { return capacity_; }

    void init_from_span(std::span<const T> src) {
#ifndef NDEBUG
        if (src.size() > capacity_) {
            tpy_panic("UninitHeapStorage::init_from_span: span exceeds capacity");
        }
        for (std::size_t i = 0; i < src.size(); ++i) {
            if (alive_[i]) {
                tpy_panic("UninitHeapStorage::init_from_span on already-alive slot");
            }
            alive_[i] = true;
        }
#endif
        if constexpr (std::is_trivially_copyable_v<T>) {
            std::memcpy(static_cast<void*>(&data_[0]), src.data(), src.size() * sizeof(T));
        } else {
            for (std::size_t i = 0; i < src.size(); ++i) {
                ::new (static_cast<void*>(&data_[i])) T(src[i]);
            }
        }
    }

    void drop_n(uint32_t start, uint32_t count) {
#ifndef NDEBUG
        for (uint32_t i = 0; i < count; ++i) {
            uint32_t idx = start + i;
            if (idx >= capacity_) {
                tpy_panic("UninitHeapStorage::drop_n index out of bounds");
            }
            if (!alive_[idx]) {
                tpy_panic("UninitHeapStorage::drop_n on dead slot");
            }
            alive_[idx] = false;
        }
#endif
        if constexpr (!std::is_trivially_destructible_v<T>) {
            for (uint32_t i = 0; i < count; ++i) {
                data_[start + i].~T();
            }
        }
    }

    void shift(uint32_t src, uint32_t dst, uint32_t count) {
        if (count == 0 || src == dst) return;
#ifndef NDEBUG
        for (uint32_t i = 0; i < count; ++i) {
            if (src + i >= capacity_ || dst + i >= capacity_) {
                tpy_panic("UninitHeapStorage::shift index out of bounds");
            }
            if (!alive_[src + i]) {
                tpy_panic("UninitHeapStorage::shift on dead source slot");
            }
        }
        for (uint32_t i = 0; i < count; ++i) {
            alive_[src + i] = false;
        }
        for (uint32_t i = 0; i < count; ++i) {
            alive_[dst + i] = true;
        }
#endif
        if constexpr (std::is_trivially_copyable_v<T>) {
            std::memmove(static_cast<void*>(&data_[dst]),
                         static_cast<const void*>(&data_[src]),
                         static_cast<std::size_t>(count) * sizeof(T));
        } else if (dst < src) {
            for (uint32_t i = 0; i < count; ++i) {
                ::new (static_cast<void*>(&data_[dst + i]))
                    T(std::move(data_[src + i]));
                data_[src + i].~T();
            }
        } else {
            for (uint32_t i = count; i > 0; --i) {
                uint32_t idx = i - 1;
                ::new (static_cast<void*>(&data_[dst + idx]))
                    T(std::move(data_[src + idx]));
                data_[src + idx].~T();
            }
        }
    }

    // TODO: emplace(i, args...) for perfect-forwarding construction

private:
    T* data_;
    uint32_t capacity_;

#ifndef NDEBUG
    std::vector<bool> alive_;
#endif
};

} // namespace tpy
