/**
 * TurboPython Runtime - Unions at a storage position
 *
 * `A | B` renders as `::tpy::Union<A, B>` wherever the union OWNS its value --
 * a value union everywhere, and a reference union at a field, a container
 * element, an `Own` slot or a return. (A reference union's BORROW form is the
 * bare pointer variant `std::variant<A*, B*>`; see variant_ref.hpp.) It is a
 * `std::variant` that owns Python's comparison rule. The variant's own operators compare the
 * ALTERNATIVE INDEX first, so `Int32 | Float64` holding 1 and holding 1.0 are
 * unequal where Python says they are equal -- and every standard container
 * operator over such an element inherits that answer. Giving the TYPE the
 * right operators fixes the element, the `std::vector` of it, the `std::tuple`
 * of it and the dict value in one place, with no help from the compiler: a
 * value-union compare renders the bare `(a == b)` at every position.
 *
 * Derived from `std::variant` rather than wrapping it, so a hand-written
 * `@native` function taking `const std::variant<Ts...>&` keeps binding. The
 * consequence is that the base's operators stay in the candidate set forever:
 * EVERY comparison operator must be declared here, because an undeclared one
 * silently answers index-first.
 *
 * Depends on: value_compare.hpp (the per-alternative-pair leaf).
 */

#pragma once

#include <concepts>
#include <string>
#include <type_traits>
#include <utility>
#include <variant>

#include "value_compare.hpp"

namespace tpy {

template<class... Ts>
struct Union : std::variant<Ts...> {
    using base_variant = std::variant<Ts...>;
    using base_variant::base_variant;
    using base_variant::operator=;

    Union() = default;

    // Implicit, not explicit: a hand-written function RETURNING
    // `std::variant<Ts...>` is assigned into a `Union` slot by
    // copy-initialization, which an explicit ctor rejects. The sibling hazard
    // that made the buffer types explicit is absent -- one alternative pack
    // has exactly one derived class, and different packs give different
    // bases, so nothing can convert into a neighbour.
    Union(const base_variant& v) : base_variant(v) {}
    Union(base_variant&& v) : base_variant(std::move(v)) {}

    // Python equality, UNCONSTRAINED -- measured, not assumed. Constraining
    // it on `(std::equality_comparable<Ts> && ...)` does NOT pair it with the
    // base's operator, because `std::variant`'s own `==` does not carry that
    // constraint: libstdc++ declares it unconditionally and hard-errors on
    // instantiation, and with the constraint in place both toolchains still
    // answer `std::equality_comparable<Union<NoEq>>` = true. Constraining
    // therefore removes OURS and leaves the base's index-first one viable,
    // and moves an alias-with-no-equality from a 2-line named message to a
    // 14-line failure inside `<variant>`. The pair that has no equality is
    // refused inside the leaf instead, where the message names the shape;
    // BUGS.md#container-compare-record-without-eq covers the missing TPy
    // location, which no spelling here can supply.
    friend bool operator==(const Union& a, const Union& b) {
        return detail::union_compare<detail::UnionOp::Eq>(
            static_cast<const base_variant&>(a),
            static_cast<const base_variant&>(b));
    }

    // Declared rather than left to the C++20 rewrite: the base's own `!=` is
    // a candidate too, and a rewritten `!(a == b)` loses to it.
    friend bool operator!=(const Union& a, const Union& b) {
        return !(a == b);
    }

    // Ordering stays unconstrained even for a pair with no ordering: CPython
    // raises TypeError for `Fixed() < Fixed()` on a type that defines none,
    // so the raise is the parity answer and the leaf performs it.
    friend bool operator<(const Union& a, const Union& b) {
        return detail::union_compare<detail::UnionOp::Lt>(
            static_cast<const base_variant&>(a),
            static_cast<const base_variant&>(b));
    }

    friend bool operator<=(const Union& a, const Union& b) {
        return detail::union_compare<detail::UnionOp::Le>(
            static_cast<const base_variant&>(a),
            static_cast<const base_variant&>(b));
    }

    friend bool operator>(const Union& a, const Union& b) {
        return detail::union_compare<detail::UnionOp::Gt>(
            static_cast<const base_variant&>(a),
            static_cast<const base_variant&>(b));
    }

    friend bool operator>=(const Union& a, const Union& b) {
        return detail::union_compare<detail::UnionOp::Ge>(
            static_cast<const base_variant&>(a),
            static_cast<const base_variant&>(b));
    }
};

namespace detail {

// The invariants generated code relies on, pinned as in-header static_asserts
// over the base-taking constructors -- compiled by every generated TU, so a
// regression shows up at the next build rather than at some use site.
struct union_pins {
    using U = Union<std::int32_t, double>;
    using V = std::variant<std::int32_t, double>;
    // A `@native` signature spelled with the base still binds, and a base
    // rvalue still initializes a Union slot.
    static_assert(std::is_convertible_v<U, V>);
    static_assert(std::is_convertible_v<V, U>);
    // A different alternative ORDER is a different type, not a conversion.
    static_assert(!std::is_convertible_v<U, Union<double, std::int32_t>>);
    // No layout cost for owning the operators.
    static_assert(sizeof(U) == sizeof(V));
    static_assert(std::is_trivially_copyable_v<U>
                  == std::is_trivially_copyable_v<V>);
    // The operators the class must own; an omitted one would answer
    // index-first through the base.
    static_assert(std::equality_comparable<U>);
    static_assert(requires (const U& a, const U& b) { { a < b } -> std::same_as<bool>; });
    static_assert(requires (const U& a, const U& b) { { a <= b } -> std::same_as<bool>; });
    static_assert(requires (const U& a, const U& b) { { a > b } -> std::same_as<bool>; });
    static_assert(requires (const U& a, const U& b) { { a >= b } -> std::same_as<bool>; });
};

// A record alternative is not trivially copyable, and a reference union's
// storage form is spelled `Union` too -- so the no-layout-cost pins above,
// which cover a scalar pack only, are repeated over a pack that owns.
struct union_pin_record {
    union_pin_record() = default;
    union_pin_record(const union_pin_record& o) : x(o.x) {}
    ~union_pin_record() {}
    int x = 0;
};

struct union_record_pins {
    using U = Union<union_pin_record, std::int32_t>;
    using V = std::variant<union_pin_record, std::int32_t>;
    static_assert(sizeof(U) == sizeof(V));
    static_assert(alignof(U) == alignof(V));
    static_assert(!std::is_trivially_copyable_v<V>);
    static_assert(std::is_trivially_copyable_v<U>
                  == std::is_trivially_copyable_v<V>);
    static_assert(std::is_convertible_v<V, U>);
    static_assert(std::is_convertible_v<U, V>);
};

// An alternative that OWNS a heap payload: a container of storage-form unions
// relies on the copy being DEEP (mutating the copy leaves the original alone)
// and on a move round trip preserving the value. Inherited from the base, but
// the derived class is what generated code names.
struct union_pin_owner {
    std::string s;
};

constexpr bool union_owner_roundtrip() {
    Union<union_pin_owner, std::int32_t> a{union_pin_owner{"ab"}};
    auto b = a;
    std::get<union_pin_owner>(b).s += "c";
    if (std::get<union_pin_owner>(a).s != "ab") return false;
    if (std::get<union_pin_owner>(b).s != "abc") return false;
    auto c = std::move(b);
    return std::get<union_pin_owner>(c).s == "abc";
}
static_assert(union_owner_roundtrip());

}  // namespace detail

}  // namespace tpy
