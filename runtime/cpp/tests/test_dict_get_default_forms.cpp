/**
 * dict.get(key, default) result form self-check.
 *
 * CPython hands back the stored object or the default itself, so over a
 * reference-type value the helper returns a reference: `V&` when both the
 * map and the default are mutable (a temporary default counts, read within
 * its full expression), `const V&` when either is const. A default of a
 * type derived from V binds as V. A value-type V keeps the by-value helper,
 * which also converts a default of another type (a literal) into V, and an
 * Optional V takes a default in its pointer borrow form.
 *
 * Exits non-zero on failure; the harness treats output as the assertion.
 */
#include <cstdio>
#include <optional>
#include <string>
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

struct D : P {
    explicit D(int n) : P{n} {}
};

// Converts to P without being one: no object to hand back by reference.
struct Conv {
    int n;
    operator P() const { return P{n}; }
};

}  // namespace

int main() {
    ::tpy::ordered_map<std::string, P> m;
    m.insert_or_assign(std::string("a"), P{1});
    const auto& cm = m;
    P fb{9};
    const P& cfb = fb;

    // Mutable map and default: the mutable overload.
    static_assert(std::is_same_v<decltype(::tpy::dict_get_default(m, "a", fb)), P&>);
    static_assert(std::is_same_v<decltype(::tpy::dict_get_default(m, "a", P{5})), P&>);
    // Either one const: the const overload.
    static_assert(std::is_same_v<decltype(::tpy::dict_get_default(cm, "a", fb)), const P&>);
    static_assert(std::is_same_v<decltype(::tpy::dict_get_default(m, "a", cfb)), const P&>);
    static_assert(std::is_same_v<decltype(::tpy::dict_get_default(cm, "a", P{5})), const P&>);

    ::tpy::dict_get_default(m, "a", fb).v = 10;
    check((*m.find("a")).second.v == 10, "hit: the write reaches the stored value");
    ::tpy::dict_get_default(m, "zz", fb).v = 20;
    check(fb.v == 20, "miss: the write reaches the default");
    check(&::tpy::dict_get_default(cm, "zz", fb) == &fb, "const map: the default itself");
    check(&::tpy::dict_get_default(m, "a", cfb) == &(*m.find("a")).second,
          "const default: the stored value itself");
    check(::tpy::dict_get_default(m, "zz", P{5}).v == 5, "temporary default read in place");
    P copied = ::tpy::dict_get_default(m, "zz", P{6});
    check(copied.v == 6, "temporary default copies out within the full expression");

    // A derived default binds as the base.
    D d(7);
    static_assert(std::is_same_v<decltype(::tpy::dict_get_default(m, "zz", d)), P&>);
    check(&::tpy::dict_get_default(m, "zz", d) == static_cast<P*>(&d),
          "derived default: the object itself");

    // A const temporary default is const, like a const lvalue one.
    static_assert(std::is_same_v<decltype(::tpy::dict_get_default(m, "a", std::move(cfb))),
                                 const P&>);
    // A derived temporary binds as the base, mutable within its expression.
    static_assert(std::is_same_v<decltype(::tpy::dict_get_default(m, "zz", D{7})), P&>);
    check(::tpy::dict_get_default(m, "zz", D{8}).v == 8, "derived temporary read in place");

    // A const map with a derived default: the const overload, as the base.
    static_assert(std::is_same_v<decltype(::tpy::dict_get_default(cm, "zz", d)), const P&>);
    check(&::tpy::dict_get_default(cm, "zz", d) == static_cast<const P*>(&d),
          "const map, derived default: the object itself");

    // An rvalue map binds the const overload; the reference into the
    // temporary map lives for the full expression only.
    static_assert(std::is_same_v<decltype(::tpy::dict_get_default(std::move(m), "a", fb)),
                                 const P&>);
    {
        auto tmp = m;
        check(::tpy::dict_get_default(std::move(tmp), "a", fb).v == 10,
              "rvalue map: the stored value read in place");
    }

    // A default that only converts to V has no object to lend: by value.
    static_assert(std::is_same_v<decltype(::tpy::dict_get_default(m, "zz", Conv{3})), P>);
    check(::tpy::dict_get_default(m, "zz", Conv{3}).v == 3, "converting default: by value");
    check(::tpy::dict_get_default(m, "a", Conv{3}).v == 10, "converting default: hit copies");

    // Value types keep the by-value helper, converting the default.
    ::tpy::ordered_map<std::string, int> c;
    c.insert_or_assign(std::string("a"), 1);
    static_assert(std::is_same_v<decltype(::tpy::dict_get_default(c, "a", 7)), int>);
    check(::tpy::dict_get_default(c, "a", 7) == 1, "int: hit");
    check(::tpy::dict_get_default(c, "z", 7) == 7, "int: miss");
    ::tpy::ordered_map<std::string, std::string> s;
    s.insert_or_assign(std::string("a"), std::string("x"));
    static_assert(std::is_same_v<decltype(::tpy::dict_get_default(s, "z", "zz")),
                                 std::string>);
    check(::tpy::dict_get_default(s, "z", "zz") == "zz", "str: a literal default converts");

    // An Optional value with a default in its borrow form (`P*`, the
    // generic `V | None` parameter at a class V): the pointee is copied
    // into the by-value optional, null meaning None.
    ::tpy::ordered_map<std::string, std::optional<P>> o;
    o.insert_or_assign(std::string("a"), std::optional<P>(P{4}));
    const P* opt_fb = &fb;
    const P* opt_none = nullptr;
    static_assert(std::is_same_v<decltype(::tpy::dict_get_default(o, "a", opt_fb)),
                                 std::optional<P>>);
    check(::tpy::dict_get_default(o, "a", opt_none)->v == 4, "optional: hit");
    check(::tpy::dict_get_default(o, "z", opt_fb)->v == fb.v, "optional: pointer default");
    check(!::tpy::dict_get_default(o, "z", opt_none).has_value(), "optional: null default");
    check(::tpy::dict_get_default(o, "z", std::optional<P>(P{3}))->v == 3,
          "optional: storage default");
    // A `None` literal default in a generic body renders `nullptr`.
    check(::tpy::dict_get_default(o, "a", nullptr)->v == 4, "optional: nullptr hit");
    check(!::tpy::dict_get_default(o, "z", nullptr).has_value(),
          "optional: nullptr default");
    ::tpy::ordered_map<std::string, std::optional<int>> oi;
    oi.insert_or_assign(std::string("a"), std::optional<int>(1));
    check(!::tpy::dict_get_default(oi, "z", nullptr).has_value(),
          "optional value: nullptr default");

    if (failures == 0) std::printf("OK\n");
    return failures == 0 ? 0 : 1;
}
