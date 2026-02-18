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
#include <new>
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
#ifndef NDEBUG
        , capacity_(capacity)
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
#ifndef NDEBUG
        , capacity_(other.capacity_)
        , alive_(std::move(other.alive_))
#endif
    {
        other.data_ = nullptr;
#ifndef NDEBUG
        other.capacity_ = 0;
#endif
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
#ifndef NDEBUG
            capacity_ = other.capacity_;
            alive_ = std::move(other.alive_);
#endif
            other.data_ = nullptr;
#ifndef NDEBUG
            other.capacity_ = 0;
#endif
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

    // TODO: emplace(i, args...) for perfect-forwarding construction

private:
    T* data_;

#ifndef NDEBUG
    std::size_t capacity_;
    std::vector<bool> alive_;
#endif
};

} // namespace tpy
