#pragma once
#include <iostream>
#include <string>
#include <string_view>
#include <tuple>

namespace mylog {

// Native logger handle -- simulates a real async logger
struct LogHandle {
    std::string name;
    explicit LogHandle(std::string_view n) : name(n) {}
};

// Deferred-copy string: holds a string_view, copies only when written to
// the logger buffer. In a real logger this wraps wrapToLog() semantics.
struct DeferredStr {
    std::string_view view;

    friend std::ostream& operator<<(std::ostream& os, const DeferredStr& d) {
        return os << "[C]" << d.view;
    }
};

inline DeferredStr defer_str(std::string_view s) { return DeferredStr{s}; }

// Static string: pointer-only, no copy at all. In a real logger the
// consumer thread reads directly from static storage.
struct StaticStr {
    std::string_view view;

    friend std::ostream& operator<<(std::ostream& os, const StaticStr& d) {
        return os << "[S]" << d.view;
    }
};

inline StaticStr static_str(std::string_view s) { return StaticStr{s}; }

// TPy tuple borrow ABI: non-value elements arrive as pointers
// (std::tuple<DeferredStr*, ...>); deref them, pass values through.
template<typename A>
decltype(auto) log_arg(A&& a) {
    if constexpr (std::is_pointer_v<std::remove_cvref_t<A>>) {
        return (*a);
    } else {
        return std::forward<A>(a);
    }
}

// Generic log dispatch: receives format string + tuple of typed args
template<typename T>
void log_dispatch(const LogHandle& handle, std::string_view fmt, T&& args) {
    std::cout << handle.name << ": " << fmt << "\n";
    std::apply([](auto&&... a) {
        ((std::cout << "  " << log_arg(a) << "\n"), ...);
    }, std::forward<T>(args));
}

} // namespace mylog
