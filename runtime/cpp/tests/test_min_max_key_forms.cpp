/**
 * min/max-with-key result form self-check.
 *
 * `min(a, b, key=f)` returns the operand itself in Python, so the key
 * helpers return a reference into one of their arguments. Which reference
 * the overload set hands back is the contract the compiler binds against:
 * all-mutable lvalues give `T&` (a write through the result reaches the
 * operand), while one const operand or one temporary must fall to `const T&`
 * -- a `T&` into a temporary would dangle, and one into a const object would
 * not compile. Value types keep working unchanged.
 *
 * Exits non-zero on failure; the harness treats output as the assertion.
 */
#include <cstdio>
#include <string_view>
#include <type_traits>
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

struct P {
    int v;
};

int key_of(const P& p) { return p.v; }

}  // namespace

int main() {
    P a{1}, b{2}, c{1};
    const P& ca = a;

    // Mutable lvalues select the mutable overload.
    static_assert(std::is_same_v<decltype(::tpy::min_key(a, b, key_of)), P&>);
    static_assert(std::is_same_v<decltype(::tpy::max_key(a, b, key_of)), P&>);
    static_assert(std::is_same_v<decltype(::tpy::min3_key(a, b, c, key_of)), P&>);
    static_assert(std::is_same_v<decltype(::tpy::max3_key(a, b, c, key_of)), P&>);
    // One const operand selects the const one.
    static_assert(std::is_same_v<decltype(::tpy::min_key(ca, b, key_of)), const P&>);
    static_assert(std::is_same_v<decltype(::tpy::max3_key(a, ca, c, key_of)), const P&>);
    // A temporary operand lives to the end of the full expression, so a
    // write through the result within it is well-formed (a callee writing
    // its `P&` parameter): still the mutable overload.
    static_assert(std::is_same_v<decltype(::tpy::min_key(a, P{0}, key_of)), P&>);
    static_assert(std::is_same_v<decltype(::tpy::max3_key(a, b, P{9}, key_of)), P&>);
    static_assert(std::is_same_v<decltype(::tpy::min_key(ca, P{0}, key_of)), const P&>);

    ::tpy::min_key(a, b, key_of).v = 10;
    check(a.v == 10 && b.v == 2, "min_key: the write reaches the lesser operand");
    ::tpy::max3_key(a, b, c, key_of).v = 20;
    check(a.v == 20, "max3_key: the write reaches the greatest operand");
    check(&::tpy::min_key(ca, b, key_of) == &b, "min_key: const overload returns the operand");

    // Ties keep the FIRST minimal / maximal operand, as Python does.
    P t1{5}, t2{5}, t3{5};
    check(&::tpy::min_key(t1, t2, key_of) == &t1, "min_key: tie keeps the first");
    check(&::tpy::max_key(t1, t2, key_of) == &t1, "max_key: tie keeps the first");
    check(&::tpy::min3_key(t1, t2, t3, key_of) == &t1, "min3_key: tie keeps the first");
    check(&::tpy::max3_key(t1, t2, t3, key_of) == &t1, "max3_key: tie keeps the first");

    // An xvalue operand is a temporary too.
    static_assert(std::is_same_v<decltype(::tpy::min_key(std::move(a), b, key_of)),
                                 P&>);
    P lo{1};
    ::tpy::min_key(lo, P{7}, key_of).v = 3;
    check(lo.v == 3, "min_key: a write reaches the lvalue beside a temporary");
    check(::tpy::max_key(lo, P{7}, key_of).v == 7, "max_key: the temporary itself");
    // An explicit `const P` argument names the const overload's result.
    static_assert(std::is_same_v<decltype(::tpy::max_key<const P>(a, b, key_of)),
                                 const P&>);
    check(&::tpy::max_key<const P>(a, b, key_of) == &a, "explicit const P: the operand");
    // Two borrow slots: the result is the winning slot, which names the
    // winning object.
    ::tpy::val_or_ref<P> sa(a), sb(b);
    auto slot_key = [](const ::tpy::val_or_ref<P>& s) { return s.get().v; };
    static_assert(std::is_same_v<decltype(::tpy::min_key(sa, sb, slot_key)),
                                 ::tpy::val_or_ref<P>&>);
    check(&::tpy::min_key(sa, sb, slot_key).get() == &b, "val_or_ref slots: the winning object");
    // A BigInt lvalue beside a prvalue: the const overload, copied out.
    ::tpy::BigInt big(5);
    auto ident = [](const ::tpy::BigInt& n) { return n; };
    ::tpy::BigInt low = ::tpy::min_key(big, ::tpy::BigInt(3), ident);
    check(low == ::tpy::BigInt(3), "BigInt lvalue + prvalue: the smaller, copied out");

    // A temporary operand compiles and the result copies out within the
    // full expression.
    P copied = ::tpy::min_key(a, P{-1}, key_of);
    check(copied.v == -1, "min_key: a temporary operand copies out");

    // Value types are unchanged.
    auto sq = [](int n) { return n * n; };
    check(::tpy::min_key(3, -4, sq) == 3, "min_key over ints");
    int x = 3, y = -4;
    check(::tpy::max_key(x, y, sq) == -4, "max_key over int lvalues");
    std::string_view s1 = "aa", s2 = "b";
    auto len = [](std::string_view s) { return s.size(); };
    check(::tpy::min_key(s1, s2, len) == "b", "min_key over string_view");

    if (failures == 0) std::printf("OK\n");
    return failures == 0 ? 0 : 1;
}
