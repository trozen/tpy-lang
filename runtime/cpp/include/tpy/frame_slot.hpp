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

/**
 * frame_slot<T> for a trivial payload - stores T as a real member.
 *
 * The primary template's aligned-storage + placement-new exists to defer T's
 * construction past frame creation. That buys nothing when default-constructing
 * T is itself a no-op, so a trivial payload skips the indirection: no
 * reinterpret_cast, no launder, and `emplace` is an assignment. The field also
 * shows up as a T in a debugger rather than a byte array.
 *
 * BOTH constraints are load-bearing, and each rules out a payload the primary
 * template handles correctly:
 *
 *   - Trivially DEFAULT-constructible: what makes a bare member safe at all.
 *     For any other T, declaring `T v_;` runs a constructor at frame creation --
 *     the behaviour the primary template exists to avoid -- and is ill-formed
 *     for a T with no default ctor.
 *   - Trivially MOVE-ASSIGNABLE and MOVE-CONSTRUCTIBLE: this form assigns
 *     (`v_ = T(...)`) where the primary placement-constructs, and move-
 *     constructs its member where the primary moves the payload out of raw
 *     storage. Requiring both trivial is what makes those substitutions
 *     unobservable. Note `is_trivially_copyable` would NOT be enough here: it
 *     holds for a move-only type (deleted copy ops are ignored when the move
 *     ops are trivial), so it admits payloads whose copy ctor is deleted.
 *   - Trivially DESTRUCTIBLE: alive_ is then needed only to answer
 *     has_value()/reset(), never for destruction correctness.
 *
 * Together these admit scalars, enums, pointers and trivial aggregates -- and
 * nothing whose construction, assignment or destruction is observable.
 *
 * Excludes references explicitly so it cannot compete with frame_slot<T&>.
 */
template <typename T>
    requires (!std::is_reference_v<T>
              && std::is_trivially_default_constructible_v<T>
              && std::is_trivially_destructible_v<T>
              && std::is_trivially_move_constructible_v<T>
              && std::is_trivially_move_assignable_v<T>)
class frame_slot<T> {
public:
    frame_slot() noexcept = default;

    frame_slot(const frame_slot&) = delete;
    frame_slot& operator=(const frame_slot&) = delete;
    frame_slot& operator=(frame_slot&&) = delete;

    // Moves, mirroring the primary template -- a copy here would reject a
    // move-only payload that is otherwise perfectly at home in this form.
    // Guarded on `alive_` for the same reason the primary is: `v_` is
    // deliberately left uninitialized until the first emplace, so touching it
    // on a dead slot reads an indeterminate value. The dead case is the common
    // one, not a corner: a coro struct is moved into its heap wrapper before
    // any of its frame fields has been written.
    frame_slot(frame_slot&& other) noexcept {
        if (other.alive_) {
            v_ = std::move(other.v_);
            alive_ = true;
            other.alive_ = false;
        }
    }

    template <typename... Args>
    T& emplace(Args&&... args) {
        v_ = T(std::forward<Args>(args)...);
        alive_ = true;
        return v_;
    }

    void reset() noexcept { alive_ = false; }

    bool has_value() const noexcept { return alive_; }
    explicit operator bool() const noexcept { return alive_; }

    T& get() & {
#ifndef NDEBUG
        if (!alive_) {
            tpy_panic("frame_slot::get on dead slot");
        }
#endif
        return v_;
    }
    const T& get() const & {
#ifndef NDEBUG
        if (!alive_) {
            tpy_panic("frame_slot::get on dead slot");
        }
#endif
        return v_;
    }

    T& operator*() & { return get(); }
    const T& operator*() const & { return get(); }
    T* operator->() { return &get(); }
    const T* operator->() const { return &get(); }

private:
    T v_;
    bool alive_ = false;
};

/**
 * frame_slot<T&> - the BORROW form of a frame field: it stores the address of
 * storage that outlives the slot instead of owning a copy.
 *
 * Same surface as the primary template, so codegen has one spelling for a frame
 * field whether it owns or aliases: `emplace(x)` binds, `(*f)` reads. That is
 * the point -- whether a for-loop element is a borrow of the source or a fresh
 * value is often only decidable at instantiation (see for_elem_next_t), so the
 * choice cannot be spelled at the declaration site.
 *
 * `emplace` takes `T&`, so binding a prvalue is ill-formed rather than a
 * dangling pointer. Move leaves the source null; destruction is trivial.
 */
template <typename T>
class frame_slot<T&> {
public:
    frame_slot() noexcept = default;

    frame_slot(const frame_slot&) = delete;
    frame_slot& operator=(const frame_slot&) = delete;
    frame_slot& operator=(frame_slot&&) = delete;

    frame_slot(frame_slot&& other) noexcept : p_(other.p_) { other.p_ = nullptr; }

    T& emplace(T& target) noexcept {
        p_ = &target;
        return target;
    }
    // A prvalue dies at the end of the full expression, so storing its address
    // is always a dangle. `emplace(T&)` alone does not reject one when T is
    // const-qualified -- `const T&` binds a temporary happily -- and a const
    // payload is exactly what a readonly element produces, so reject it here.
    void emplace(std::remove_const_t<T>&&) = delete;

    void reset() noexcept { p_ = nullptr; }

    bool has_value() const noexcept { return p_ != nullptr; }
    explicit operator bool() const noexcept { return p_ != nullptr; }

    T& get() & {
#ifndef NDEBUG
        if (p_ == nullptr) {
            tpy_panic("frame_slot::get on dead slot");
        }
#endif
        return *p_;
    }
    const T& get() const & {
#ifndef NDEBUG
        if (p_ == nullptr) {
            tpy_panic("frame_slot::get on dead slot");
        }
#endif
        return *p_;
    }

    T& operator*() & { return get(); }
    const T& operator*() const & { return get(); }
    T* operator->() { return &get(); }
    const T* operator->() const { return &get(); }

private:
    T* p_ = nullptr;
};

} // namespace tpy
