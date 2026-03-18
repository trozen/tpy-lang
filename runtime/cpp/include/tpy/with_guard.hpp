/**
 * RAII guard for Python `with` statement context managers.
 *
 * Calls __exit__() on the context manager when the guard goes out of scope,
 * ensuring cleanup even on early return or panic.
 */

#pragma once

namespace tpy {

template<typename T>
struct WithGuard {
    T& ctx;
    ~WithGuard() { ctx.__exit__(); }
};

// Deduction guide so WithGuard{ctx} works without explicit template arg
template<typename T>
WithGuard(T&) -> WithGuard<T>;

} // namespace tpy
