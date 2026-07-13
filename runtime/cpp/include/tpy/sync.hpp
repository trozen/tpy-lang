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
#include <condition_variable>

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

// `tpy.sync._RawCondvar` maps here. Plain `std::condition_variable` (not
// `condition_variable_any`): the wait target is always a `std::mutex`
// (MovableMutex), so a trivial `unique_lock<std::mutex>` adapter in `wait`
// suffices and the cv itself allocates nothing -- whereas condition_variable_any
// heap-allocates a shared_ptr<mutex> per instance in libstdc++. `condition_
// variable()` is noexcept, so the move ctor (default-constructs a fresh cv, per
// the MovableSharedMutex convention) has no real throw path. The move runs once
// at heap construction via unsafe_take from a fresh, waiter-less temporary.
struct MovableConditionVariable : std::condition_variable {
    using std::condition_variable::condition_variable;
    MovableConditionVariable(MovableConditionVariable&&) noexcept
        : std::condition_variable() {}
    MovableConditionVariable(const MovableConditionVariable&) = delete;
    MovableConditionVariable& operator=(const MovableConditionVariable&) = delete;

    // Concrete non-template `wait` over a MovableMutex pointer (hides the
    // inherited `wait` overloads so @native _RawCondvar.wait maps to one member;
    // takes a pointer so the TPy side threads a mutable Ptr through). The caller
    // holds `*m` locked via a live guard: adopt that ownership into a unique_lock,
    // wait (unlocks/relocks around the block), then RELEASE it without unlocking
    // so the lock stays held on return. Spurious wakeups possible -> callers loop.
    void wait(MovableMutex* m) {
        std::unique_lock<std::mutex> lk(*m, std::adopt_lock);
        std::condition_variable::wait(lk);
        lk.release();
    }
};

}  // namespace tpy
