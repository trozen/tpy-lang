/**
 * TurboPython Runtime - synchronization primitives (tpy.sync)
 *
 * `tpy.sync._RawMutex` maps to `tpy::MovableMutex` and `_RawSharedMutex` to
 * `tpy::MovableSharedMutex`. `std::mutex` / `std::shared_mutex` delete their
 * move ctors, so a `Mutex[T]` / `RwLock[T]` cell holding one inline would be
 * non-movable -- but the cell is move-constructed once into heap storage at
 * `Mutex.__init__` (via `unsafe_take`) while still exclusively owned and
 * unlocked. We add that single move ctor here, by inheritance, exactly like
 * `tpy::MovableAtomic`: it default-constructs a fresh lock (a moved-from,
 * unlocked, unshared mutex carries no state worth relocating), leaving the
 * source in a valid unlocked state. Copy stays deleted.
 *
 * lock/unlock (and shared variants) are `std::mutex` / `std::shared_mutex`'s
 * own inherited members; the `_Raw*` stubs call only the named methods.
 *
 * Pulled in only by modules importing `tpy.sync` (via `# tpy: include`).
 */

#pragma once

#include <mutex>
#include <shared_mutex>

namespace tpy {

struct MovableMutex : std::mutex {
    using std::mutex::mutex;
    // Sound only because a TPy move implies exclusive ownership of the source:
    // no lock is held and no other thread can observe the mutex, so a fresh
    // lock is an exact stand-in for the (stateless-when-unlocked) source.
    MovableMutex(MovableMutex&&) noexcept : std::mutex() {}
    MovableMutex(const MovableMutex&) = delete;
    MovableMutex& operator=(const MovableMutex&) = delete;
};

struct MovableSharedMutex : std::shared_mutex {
    using std::shared_mutex::shared_mutex;
    // noexcept, matching MovableMutex. std::shared_mutex()'s default ctor may
    // in principle throw std::system_error (OS rwlock-init failure), unlike
    // std::mutex's guaranteed-noexcept one -- so this move can terminate on that
    // (essentially-impossible) failure. Marking it non-noexcept would NOT let
    // the throw propagate: the sole caller is `_RwLockCell`'s codegen-generated
    // move ctor, which is itself unconditionally noexcept, so an escaping throw
    // terminates one frame up regardless. Keep the contract honest (noexcept ==
    // the actual terminate-on-throw reality). The move runs once, at
    // heap construction via unsafe_take, from an unlocked/unshared temporary.
    MovableSharedMutex(MovableSharedMutex&&) noexcept : std::shared_mutex() {}
    MovableSharedMutex(const MovableSharedMutex&) = delete;
    MovableSharedMutex& operator=(const MovableSharedMutex&) = delete;
};

}  // namespace tpy
