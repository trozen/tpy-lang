/**
 * None-access host contract self-check.
 *
 * A failed None check raises CPython's exception (AttributeError / TypeError)
 * as a C++-only subclass that also carries the `NoneAccessError` marker, so a
 * C++ host can catch every None access through one type and read its message.
 * The marker must survive `except as e` re-raising, which goes through the
 * Throwable vtable (`clone()` / `__raise__()`).
 *
 * Exits non-zero on failure; the harness treats output as the assertion.
 */
#include <cstdio>
#include <functional>
#include <memory>
#include <optional>
#include <string_view>
#include <utility>

#include "tpy/tpy.hpp"

namespace {

int failures = 0;

void check(bool ok, const char* what) {
    if (!ok) {
        std::printf("FAIL: %s\n", what);
        ++failures;
    }
}

struct P { int x = 1; };

// Through the marker, as a host catches it: the message reads back.
template <typename Raise>
void caught_as_marker(Raise raise, std::string_view expected, const char* what) {
    try {
        raise();
        check(false, what);
    } catch (const tpy::NoneAccessError& e) {
        check(std::string_view(e.what()) == expected, what);
    }
}

void markers() {
    P* p = nullptr;
    caught_as_marker([&] { (void)tpy::none_attr(p, "x"); },
                     "'NoneType' object has no attribute 'x'",
                     "none_attr throws a NoneAccessError with its message");
    std::optional<int> n;
    caught_as_marker([&] { (void)tpy::none_operand(n, "+", "int"); },
                     "unsupported operand type(s) for +: 'NoneType' and 'int'",
                     "none_operand throws a NoneAccessError with its message");
}

void python_classes() {
    P* p = nullptr;
    try {
        (void)tpy::none_attr(p, "x");
        check(false, "none_attr raises");
    } catch (const tpy::AttributeError&) {
    }
    std::optional<int> n;
    try {
        (void)tpy::none_item(n);
        check(false, "none_item raises");
    } catch (const tpy::TypeError&) {
    }
}

// The arguments run before the absent callee raises, CPython's order; a
// field cleared by an argument raises too rather than calling a destroyed
// function.
void none_call() {
    std::optional<std::function<int(int)>> cb;
    int ran = 0;
    try {
        (void)tpy::none_call(cb)((++ran, 1));
        check(false, "none_call on an absent callee raises");
    } catch (const tpy::NoneAccessError& e) {
        check(std::string_view(e.what()) == "'NoneType' object is not callable",
              "none_call throws a NoneAccessError with its message");
    }
    check(ran == 1, "none_call raises after the arguments ran");
    try {
        (void)tpy::none_call(cb)((cb = [](int v) { return v; }, 1));
        check(false, "none_call on a callee installed by an argument raises");
    } catch (const tpy::NoneTypeError&) {
    }
    try {
        (void)tpy::none_call(cb)((cb.reset(), 1));
        check(false, "none_call on a callee cleared by an argument raises");
    } catch (const tpy::NoneTypeError&) {
    }
    cb = [](int v) { return v * 2; };
    check(tpy::none_call(cb)(3) == 6, "none_call calls a present callee");
}

struct Counted {
    static inline int copies = 0;
    static inline int moves = 0;
    Counted() = default;
    Counted(const Counted&) { ++copies; }
    Counted(Counted&&) noexcept { ++moves; }
    static void reset() { copies = moves = 0; }
};

// The forwarding proxy costs what a direct call through the function costs,
// except one move for a prvalue argument.
void none_call_forwarding() {
    std::optional<std::function<void(Counted)>> cb = [](Counted) {};
    Counted c;
    auto direct = [&](auto&& arg) {
        Counted::reset();
        (*cb)(std::forward<decltype(arg)>(arg));
        return std::pair{Counted::copies, Counted::moves};
    };
    auto proxied = [&](auto&& arg) {
        Counted::reset();
        tpy::none_call(cb)(std::forward<decltype(arg)>(arg));
        return std::pair{Counted::copies, Counted::moves};
    };
    check(proxied(c) == direct(c), "none_call lvalue argument costs a direct call");
    check(proxied(std::move(c)) == direct(std::move(c)),
          "none_call xvalue argument costs a direct call");
    Counted::reset();
    (*cb)(Counted{});
    auto direct_pr = std::pair{Counted::copies, Counted::moves};
    Counted::reset();
    tpy::none_call(cb)(Counted{});
    auto proxied_pr = std::pair{Counted::copies, Counted::moves};
    check(proxied_pr.first == direct_pr.first && proxied_pr.second <= direct_pr.second + 1,
          "none_call prvalue argument costs at most one extra move");
}

// `clone()` then `__raise__()` -- the `except as e` / bare `raise` path --
// rethrows the same subclass, marker included.
template <typename E>
void marker_survives_reraise(const char* what) {
    std::unique_ptr<tpy::Throwable> held;
    try {
        throw E("m");
    } catch (const tpy::Throwable& t) {
        held = t.clone();
    }
    try {
        held->__raise__();
    } catch (const tpy::NoneAccessError& e) {
        check(std::string_view(e.what()) == "m", what);
        return;
    } catch (...) {
    }
    check(false, what);
}

}  // namespace

int main() {
    markers();
    python_classes();
    none_call();
    none_call_forwarding();
    marker_survives_reraise<tpy::NoneAttributeError>(
        "NoneAttributeError keeps its marker through clone/__raise__");
    marker_survives_reraise<tpy::NoneTypeError>(
        "NoneTypeError keeps its marker through clone/__raise__");
    if (failures == 0) std::printf("ok\n");
    return failures == 0 ? 0 : 1;
}
