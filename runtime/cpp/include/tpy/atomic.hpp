/**
 * TurboPython Runtime - atomic support (tpy.atomic)
 *
 * `tpy.atomic._RawAtomic[T]` maps to `tpy::MovableAtomic<T>`, which is just
 * `std::atomic<T>` made movable. `std::atomic` deletes its move ctor, so the
 * `Atomic` field would otherwise be non-movable -- forcing a custom relocating
 * move (and its drop flag) onto the wrapper. Instead we add the move ctor once
 * here, by inheritance, so `Atomic` stays member-wise movable and exactly
 * `std::atomic`-sized. The move relocates via a relaxed load + reconstruct,
 * race-free because a TPy move implies exclusive ownership of the source; copy
 * stays deleted (forking a shared counter is a bug).
 *
 * Everything else is `std::atomic`'s own: load/store/exchange/fetch_* are
 * inherited, and compare-exchange goes through the tuple helpers below (which
 * bind the base `std::atomic&` via inheritance) to adapt CAS's reference
 * out-param into a `tuple[bool, T]`. Public inheritance also exposes the rest of
 * `std::atomic`'s surface (implicit `operator T()`, `operator=(T)`,
 * `wait`/`notify`); the `_RawAtomic` stub calls only the named methods, so that
 * surface stays unused by generated code. Memory orderings cross the boundary as
 * `std::memory_order` itself (a `@native` enum). Precondition (same as
 * `std::atomic`'s own): `load` accepts relaxed/consume/acquire/seq_cst; a CAS
 * `failure` order must not be release/acq_rel. An illegal order is UB.
 *
 * Pulled in only by modules importing `tpy.atomic` (via `# tpy: include`).
 */

#pragma once

#include <atomic>
#include <tuple>

namespace tpy {

template <typename T>
struct MovableAtomic : std::atomic<T> {
    using std::atomic<T>::atomic;  // inherit the value / default constructors
    // A non-atomic load-and-construct: sound only because a TPy move implies
    // exclusive ownership of the source (no concurrent access to race with).
    MovableAtomic(MovableAtomic&& other) noexcept
        : std::atomic<T>(other.load(std::memory_order_relaxed)) {
        // Self-check the "adds only a move ctor, stays std::atomic-sized" claim
        // that keeps `Atomic` drop-flag-free (fires on any moved instantiation).
        static_assert(sizeof(MovableAtomic) == sizeof(std::atomic<T>),
                      "MovableAtomic must add no storage over std::atomic<T>");
    }
    MovableAtomic(const MovableAtomic&) = delete;
    MovableAtomic& operator=(const MovableAtomic&) = delete;
};

// Returns (succeeded, observed): on success `observed` is the swapped-from value
// (== expected); on failure the current value, so a CAS loop retries without a
// separate re-load.
template <typename T>
std::tuple<bool, T> atomic_cas(std::atomic<T>& a, T expected, T desired,
                               std::memory_order success, std::memory_order failure) {
    T exp = expected;
    bool ok = a.compare_exchange_strong(exp, desired, success, failure);
    return {ok, exp};
}

// The `weak` form may fail spuriously (use in a loop) for cheaper codegen on
// LL/SC architectures.
template <typename T>
std::tuple<bool, T> atomic_cas_weak(std::atomic<T>& a, T expected, T desired,
                                    std::memory_order success, std::memory_order failure) {
    T exp = expected;
    bool ok = a.compare_exchange_weak(exp, desired, success, failure);
    return {ok, exp};
}

inline void atomic_fence(std::memory_order order) { std::atomic_thread_fence(order); }

}  // namespace tpy
