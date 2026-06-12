/**
 * TurboPython Runtime - frame_slot
 *
 * Uninitialized aligned storage for a single T, used as a generator /
 * coroutine state-machine frame field. Defers T's construction until
 * the source code's first assignment (vs. C++'s default behavior of
 * default-constructing every non-static member of a struct).
 *
 * TPy sema enforces no-read-before-write, so the slot only needs to
 * worry about destruction correctness: a runtime `alive_` flag tracks
 * whether the payload exists, and the slot's destructor runs `~T()`
 * iff alive.
 *
 * Not copyable; move-constructible (required so coro structs can be
 * wrapped into `Adapter<...>` via `make_unique<Adapter<...>>(std::move(coro))`);
 * not move-assignable. Move-construct transfers the alive payload and
 * leaves the source dead.
 */

#pragma once

#include <cstdint>
#include <new>
#include <type_traits>
#include <utility>

#include "core.hpp"

namespace tpy {

// State discriminant for resumable frames that carry a cleanup
// destructor (pending finally / with.__exit__ on abandonment). A plain
// int32_t state would survive memberwise move, so the moved-from frame's
// destructor would re-run cleanup; this wrapper's move resets the source
// to MOVED_FROM (no state enumerator uses negative values), letting the
// frame keep `F(F&&) = default` without codegen enumerating its fields.
class frame_state {
public:
    static constexpr int32_t MOVED_FROM = -1;

    explicit frame_state(int32_t v) noexcept : v_(v) {}

    frame_state(const frame_state&) = delete;
    frame_state& operator=(const frame_state&) = delete;

    frame_state(frame_state&& other) noexcept : v_(other.v_) {
        other.v_ = MOVED_FROM;
    }
    frame_state& operator=(frame_state&& other) noexcept {
        v_ = other.v_;
        if (this != &other) {
            other.v_ = MOVED_FROM;
        }
        return *this;
    }

    frame_state& operator=(int32_t v) noexcept {
        v_ = v;
        return *this;
    }
    operator int32_t() const noexcept { return v_; }

private:
    int32_t v_;
};

template <typename T>
class frame_slot {
public:
    frame_slot() noexcept = default;

    frame_slot(const frame_slot&) = delete;
    frame_slot& operator=(const frame_slot&) = delete;
    frame_slot& operator=(frame_slot&&) = delete;

    // Move ctor is required: async coro structs get wrapped into an
    // `Adapter<...>` via `make_unique<Adapter<...>>(std::move(coro))`
    // which forwards through inner's move ctor; if any frame field's
    // move ctor is deleted the whole coro struct becomes immovable.
    // Transfers the live payload and leaves the source dead.
    frame_slot(frame_slot&& other)
        noexcept(std::is_nothrow_move_constructible_v<T>) {
        if (other.alive_) {
            ::new (static_cast<void*>(&storage_)) T(std::move(*other.ptr()));
            alive_ = true;
            other.ptr()->~T();
            other.alive_ = false;
        }
    }

    ~frame_slot() {
        if (alive_) {
            ptr()->~T();
        }
    }

    template <typename... Args>
    T& emplace(Args&&... args) {
        if (alive_) {
            ptr()->~T();
            // Mark dead between destroy and placement-new so a throwing
            // T ctor doesn't leave alive_=true over destroyed storage
            // (would cause the dtor to double-destroy).
            alive_ = false;
        }
        ::new (static_cast<void*>(&storage_)) T(std::forward<Args>(args)...);
        alive_ = true;
        return *ptr();
    }

    // No operator= for arbitrary T values. Codegen emits explicit
    // `name.emplace(expr)` for every assignment to a frame slot --
    // makes the first-init vs rebind invariant visible in the source,
    // and avoids the `name = {}` ambiguity that plagues template
    // overload resolution against deleted copy/move assigns.

    void reset() {
        if (alive_) {
            ptr()->~T();
            alive_ = false;
        }
    }

    bool has_value() const noexcept { return alive_; }
    explicit operator bool() const noexcept { return alive_; }

    T& get() & {
#ifndef NDEBUG
        if (!alive_) {
            tpy_panic("frame_slot::get on dead slot");
        }
#endif
        return *ptr();
    }
    const T& get() const & {
#ifndef NDEBUG
        if (!alive_) {
            tpy_panic("frame_slot::get on dead slot");
        }
#endif
        return *ptr();
    }

    T& operator*() & { return get(); }
    const T& operator*() const & { return get(); }
    T* operator->() { return &get(); }
    const T* operator->() const { return &get(); }

private:
    T* ptr() noexcept { return std::launder(reinterpret_cast<T*>(&storage_)); }
    const T* ptr() const noexcept { return std::launder(reinterpret_cast<const T*>(&storage_)); }

    alignas(T) unsigned char storage_[sizeof(T)];
    bool alive_ = false;
};

} // namespace tpy
