/**
 * assert_lent self-check.
 *
 * Generated code wraps every call the compiler binds as a borrow in
 * `tpy::assert_lent`, so the wrapper must hand back the SAME object in the
 * same const-ness: a mutable reference stays mutable, a const one const, a
 * base reference to a derived object the base subobject. A value result is a
 * compile error, pinned by `test_assert_lent_rejects_a_value` in
 * tests/test_runtime_cpp.py.
 *
 * Exits non-zero on failure; the harness treats output as the assertion.
 */
#include <cstdio>
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

struct Base { int v = 1; };
struct Derived : Base { int w = 2; };

Base& as_base(Derived& d) { return d; }
const Base& as_const(const Base& b) { return b; }

void identity() {
    Base b;
    Base& m = tpy::assert_lent(b);
    static_assert(std::is_same_v<decltype(tpy::assert_lent(b)), Base&>);
    check(&m == &b, "a mutable lvalue: the same object");
    m.v = 5;
    check(b.v == 5, "a write through the result reaches the source");

    const Base& c = tpy::assert_lent(as_const(b));
    static_assert(std::is_same_v<decltype(tpy::assert_lent(as_const(b))),
                                 const Base&>);
    check(&c == &b, "a const lvalue: the same object, still const");

    Derived d;
    Base& db = tpy::assert_lent(as_base(d));
    check(&db == static_cast<Base*>(&d),
          "a base reference to a derived object: the base subobject");
}

// Usable where the wrapped call is: a constant expression, a noexcept one.
constexpr int folded() {
    int x = 3;
    tpy::assert_lent(x) += 1;
    return x;
}
static_assert(folded() == 4);
static_assert(noexcept(tpy::assert_lent(std::declval<Base&>())));

}  // namespace

int main() {
    identity();
    return failures == 0 ? 0 : 1;
}
