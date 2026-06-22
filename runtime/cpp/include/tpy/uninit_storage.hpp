/**
 * TurboPython Runtime - UninitStorage<T>
 *
 * Owning storage for a single optional T, with its liveness tracked in one
 * `alive_` bool. Unlike UninitArrayStorage (raw, owner-managed liveness, no
 * generic move), UninitStorage owns its liveness, so it MOVES CORRECTLY element-wise:
 * the move ctor/assign transfer the live payload and leave the source dead.
 * That makes it safe for a non-trivially-relocatable payload (e.g. an SSO
 * std::string, whose data pointer aliases its own inline buffer and so cannot
 * be memcpy-moved).
 *
 * Use UninitStorage for owners that are a single optional value (Poll, channel send
 * slot, Rc payload). The `alive_` bool IS the owner's liveness -- there is no
 * separate flag to keep in sync, so no redundancy. Distinct from frame_slot
 * (which is coroutine-frame-specific: no-read-before-write contract, emplace-
 * only assignment); UninitStorage is a general core/tplib storage primitive with an
 * explicit construct / has / get / take / reset surface.
 */

#pragma once

#include <new>
#include <type_traits>
#include <utility>

#include "core.hpp"

namespace tpy {

template <typename T>
class UninitStorage {
    // Every payload stored here is move-constructed by an owner whose own
    // move ctor is noexcept (Poll / Rc cell / Task / Future / channel send),
    // so a throwing T move would std::terminate at the owner boundary. Enforce
    // the assumption the move ops' noexcept-spec leans on, rather than leaving
    // it to a comment.
    static_assert(std::is_nothrow_move_constructible_v<T>,
                  "UninitStorage<T> requires a noexcept-movable T");

public:
    UninitStorage() noexcept = default;

    UninitStorage(const UninitStorage&) = delete;
    UninitStorage& operator=(const UninitStorage&) = delete;

    // Transfers the live payload element-wise (never a byte copy) and leaves
    // the source dead -- correct for non-trivially-relocatable T. Plain
    // noexcept: the static_assert above guarantees T is nothrow-movable.
    UninitStorage(UninitStorage&& other) noexcept {
        if (other.alive_) {
            ::new (static_cast<void*>(&storage_)) T(std::move(*other.ptr()));
            alive_ = true;
            other.ptr()->~T();
            other.alive_ = false;
        }
    }

    UninitStorage& operator=(UninitStorage&& other) noexcept {
        if (this != &other) {
            reset();
            if (other.alive_) {
                ::new (static_cast<void*>(&storage_)) T(std::move(*other.ptr()));
                alive_ = true;
                other.ptr()->~T();
                other.alive_ = false;
            }
        }
        return *this;
    }

    ~UninitStorage() { reset(); }

    // Construct the payload (precondition: empty). Two overloads mirror
    // UninitArrayStorage::init0 so an Own[T] source binds by move or copy.
    void construct(T&& value) {
#ifndef NDEBUG
        if (alive_) tpy_panic("UninitStorage::construct on a live slot");
#endif
        ::new (static_cast<void*>(&storage_)) T(std::move(value));
        alive_ = true;
    }
    void construct(const T& value) {
#ifndef NDEBUG
        if (alive_) tpy_panic("UninitStorage::construct on a live slot");
#endif
        ::new (static_cast<void*>(&storage_)) T(value);
        alive_ = true;
    }

    bool has() const noexcept { return alive_; }

    T& get() & {
#ifndef NDEBUG
        if (!alive_) tpy_panic("UninitStorage::get on a dead slot");
#endif
        return *ptr();
    }
    const T& get() const & {
#ifndef NDEBUG
        if (!alive_) tpy_panic("UninitStorage::get on a dead slot");
#endif
        return *ptr();
    }

    // Consuming read: unlike get() (which leaves the payload in place), this
    // empties the slot, so a second take() hits the dead-slot check.
    T take() {
#ifndef NDEBUG
        if (!alive_) tpy_panic("UninitStorage::take on a dead slot");
#endif
        T result = std::move(*ptr());
        ptr()->~T();
        alive_ = false;
        return result;
    }

    void reset() {
        if (alive_) {
            ptr()->~T();
            alive_ = false;
        }
    }

    // Raw address of the payload slot (live or not) -- for an owner that
    // caches a stable Ptr into a heap-pinned payload (e.g. Rc).
    T* ptr() noexcept { return std::launder(reinterpret_cast<T*>(&storage_)); }
    const T* ptr() const noexcept { return std::launder(reinterpret_cast<const T*>(&storage_)); }

private:
    alignas(T) unsigned char storage_[sizeof(T)];
    bool alive_ = false;
};

} // namespace tpy
