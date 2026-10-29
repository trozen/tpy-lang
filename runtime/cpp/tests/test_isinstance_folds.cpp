/**
 * isinstance fold self-check: `isinstance_any_of`, `holds_any`,
 * `any_isinstance`.
 *
 * Generated code hands each fold its subject exactly as the subject's own
 * expression reads -- an object, a pointer to one, a prvalue, const or not --
 * so the subject is read once inside the test's full expression. The corpus
 * reaches only some of those kinds; each is pinned here, as are both adapter
 * shapes a structural conformer sits in and the two Any states that hold no
 * user value (empty, and holding None).
 *
 * Exits non-zero on failure; the harness treats output as the assertion.
 */
#include <cstdio>
#include <typeinfo>
#include <variant>

#include "tpy/tpy.hpp"

namespace {

int failures = 0;

void check(bool ok, const char* what) {
    if (!ok) {
        std::printf("FAIL: %s\n", what);
        ++failures;
    }
}

struct Base { virtual ~Base() = default; };
struct D : Base {};
struct E : Base {};
// A structural conformer of `Base`: behind a `Base*` it is an adapter.
struct Plain {};

D make_d() { return D{}; }

}  // namespace

namespace tpy {
template<> struct Adapter<Base, Plain> : Base { Plain inner; };
template<> struct RefAdapter<Base, Plain> : Base {
    Plain& inner;
    explicit RefAdapter(Plain& p) : inner(p) {}
};
}  // namespace tpy

int main() {
    D d;
    E e;

    // isinstance_any_of: an object subject is tested by address, a pointer
    // subject as it is.
    Base& b = d;
    const Base& cb = d;
    Base* p = &d;
    const Base* cp = &d;
    check(tpy::isinstance_any_of<D>(b), "an lvalue object");
    check(!tpy::isinstance_any_of<E>(b), "an lvalue object of another type");
    check(tpy::isinstance_any_of<const D, const E>(cb), "a const object");
    check(tpy::isinstance_any_of<E, D>(p), "a pointer, the match not first");
    check(tpy::isinstance_any_of<const D>(cp), "a pointer to const");
    check(!tpy::isinstance_any_of<D, E>(static_cast<Base*>(nullptr)),
          "a null pointer matches no member");
    check(tpy::isinstance_any_of<const D>(make_d()), "a prvalue object");
    check(!tpy::isinstance_any_of<const E>(make_d()),
          "a prvalue object of another type");
    Base* const pc = &e;
    check(tpy::isinstance_any_of<D, E>(pc), "a const pointer variable");

    // Both adapter shapes a structural conformer sits in, as pointer,
    // reference and prvalue subjects.
    using AP = tpy::Adapted<Base, Plain>;
    tpy::Adapter<Base, Plain> owned;
    Plain plain;
    tpy::RefAdapter<Base, Plain> borrowed(plain);
    check(tpy::isinstance_any_of<AP>(static_cast<Base*>(&owned)),
          "an owning adapter behind a pointer");
    check(tpy::isinstance_any_of<AP, const D>(
              static_cast<const Base*>(&owned)),
          "an owning adapter behind a const pointer");
    check(tpy::isinstance_any_of<AP>(static_cast<Base&>(borrowed)),
          "a borrowing adapter behind a reference");
    check(tpy::isinstance_any_of<const D, AP>(
              static_cast<const Base&>(borrowed)),
          "a borrowing adapter behind a const reference");
    check(tpy::isinstance_any_of<AP>(tpy::Adapter<Base, Plain>{}),
          "a prvalue adapter");
    check(!tpy::isinstance_any_of<AP>(b), "a nominal object is no adapter");

    // holds_any: value and pointer variants, const and prvalue.
    tpy::Union<int, double> value{3};
    const tpy::Union<int, double>& cvalue = value;
    check(tpy::holds_any<double, int>(value), "a value variant");
    check(!tpy::holds_any<double>(value), "a value variant, no member held");
    check(tpy::holds_any<int>(cvalue), "a const value variant");
    check(tpy::holds_any<double>(tpy::Union<int, double>{2.5}),
          "a prvalue value variant");
    tpy::Union<D*, E*> ptrs{&d};
    tpy::Union<const D*, const E*> cptrs{&e};
    check(tpy::holds_any<D*>(ptrs), "a pointer variant");
    check(!tpy::holds_any<E*>(ptrs), "a pointer variant, no member held");
    check(tpy::holds_any<const D*, const E*>(cptrs),
          "a const-pointee pointer variant");

    // any_isinstance: empty and None-holding cells, const and prvalue.
    tpy::Any empty;
    check(!tpy::any_isinstance<int, std::monostate>(empty),
          "an empty Any matches no member, not even None");
    check(empty.value.type() == typeid(void),
          "an empty Any reports void, so the one-member test needs no "
          "has_value check");
    tpy::Any none;
    none.value = std::monostate{};
    check(tpy::any_isinstance<std::monostate>(none), "an Any holding None");
    check(!tpy::any_isinstance<int>(none), "an Any holding None is no int");
    tpy::Any i;
    i.value = 1;
    check(tpy::any_isinstance<double, int>(i), "an Any holding an int");
    check(tpy::any_isinstance<int>(static_cast<const tpy::Any&>(i)),
          "a const Any");
    check(!tpy::any_isinstance<double, int>(tpy::Any{}), "a prvalue Any");

    if (failures == 0) std::printf("OK\n");
    return failures == 0 ? 0 : 1;
}
