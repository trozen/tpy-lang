/**
 * TurboPython Runtime - the union type
 *
 * `A | B` renders as `::tpy::Union<...>` at EVERY position. The alternatives
 * carry the borrow/storage duality: `Union<A, B>` owns its value (a value
 * union everywhere, a reference union at a field, a container element, an
 * `Own` slot or a return), `Union<A*, B*>` borrows it (a parameter, a local,
 * a comprehension or frame slot, a tuple borrow element) and
 * `Union<const A*, const B*>` is the read borrow. The pack is the fact --
 * `detail::vc_borrow_pack` -- so a consumer asks it rather than guessing at
 * a type name, and the two forms cannot be different types that silently
 * convert into each other's slots.
 *
 * It is a `std::variant` that owns Python's comparison rule. The variant's own operators compare the
 * ALTERNATIVE INDEX first, so `int32 | float64` holding 1 and holding 1.0 are
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
#include <cstddef>
#include <cstdint>
#include <string>
#include <type_traits>
#include <utility>
#include <variant>

#include "value_compare.hpp"

namespace tpy {

template<class... Ts>
struct Union;

namespace detail {

template<typename T> struct u_add_const {
    using type = const std::remove_pointer_t<T>*;
};
template<> struct u_add_const<std::monostate> { using type = std::monostate; };

// `Union::const_form` for a BORROW pack, and nothing for any other -- a pack
// that owns has no read-borrow sibling, and naming `void` keeps a value
// union's `const_form` from being a well-formed but meaningless type.
template<class... Ts>
struct u_const_form { using type = void; };

template<class... Ts> requires vc_borrow_pack<Ts...>
struct u_const_form<Ts...> {
    using type = Union<typename u_add_const<Ts>::type...>;
};

}  // namespace detail

template<class... Ts>
struct Union : std::variant<Ts...> {
    using base_variant = std::variant<Ts...>;
    using base_variant::base_variant;
    using base_variant::operator=;

    Union() = default;

    // Implicit, not explicit: a hand-written function RETURNING
    // `std::variant<Ts...>` is assigned into a `Union` slot by
    // copy-initialization, which an explicit ctor rejects. There is no
    // sibling to convert into by accident -- one template, and a different
    // pack is a different base.
    constexpr Union(const base_variant& v) : base_variant(v) {}
    constexpr Union(base_variant&& v) : base_variant(std::move(v)) {}

    // The read borrow of a BORROW pack: `Union<const Dog*, const Cat*>`.
    // The type names it so no caller has to spell it, and both are
    // constrained to the borrow pack -- there is nothing to add const to on
    // a pack that owns, and a `Union<int32, float64>::as_const()` would be a
    // question about a form that does not exist.
    using const_form = typename detail::u_const_form<Ts...>::type;

    constexpr const_form as_const() const
        requires detail::vc_borrow_pack<Ts...>
    {
        return u_const_fill<0>();
    }

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
    // a candidate too, and a rewritten `!(a == b)` loses to it. It visits its
    // OWN leaf rather than negating equality -- an alternative that declares
    // `__ne__` answers `!=` itself in CPython, and need not agree with the
    // negation of its `__eq__`.
    friend bool operator!=(const Union& a, const Union& b) {
        return detail::union_compare<detail::UnionOp::Ne>(
            static_cast<const base_variant&>(a),
            static_cast<const base_variant&>(b));
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

private:
    template<std::size_t I>
    constexpr const_form u_const_fill() const {
        if constexpr (I == sizeof...(Ts)) {
            detail::vc_valueless();
        } else {
            if (this->index() == I) {
                using Alt = std::variant_alternative_t<I, base_variant>;
                const_form r;
                if constexpr (std::same_as<Alt, std::monostate>) {
                    r.template emplace<I>(std::monostate{});
                } else {
                    r.template emplace<I>(*std::get_if<I>(this));
                }
                return r;
            }
            return u_const_fill<I + 1>();
        }
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
    // A pack that owns is not the borrow form, and has no read borrow.
    static_assert(!vc_borrow_pack<std::int32_t, double>);
    static_assert(std::is_same_v<U::const_form, void>);
};

// Whether `as_const()` is callable at all -- asked through a detection
// idiom rather than a `requires` expression, because the constrained member
// of a class template reports its unsatisfied constraint as a diagnostic
// there rather than as a false.
template<typename T, typename = void>
struct u_has_as_const : std::false_type {};
template<typename T>
struct u_has_as_const<T, std::void_t<
    decltype(std::declval<const T&>().as_const())>> : std::true_type {};

// The BORROW pack: one template, so everything pinned above has to hold for
// a union of pointers too, and the read-borrow conversion has to hold ONLY
// there.
struct union_borrow_pins {
    struct A { int x; };
    struct B { int y; };
    using PU = Union<A*, B*>;
    using PV = std::variant<A*, B*>;
    static_assert(std::is_convertible_v<PU, PV>);
    static_assert(std::is_convertible_v<PV, PU>);
    // A different alternative ORDER is a different type, not a conversion.
    static_assert(!std::is_convertible_v<PU, Union<B*, A*>>);
    // No layout cost for owning the operators.
    static_assert(sizeof(PU) == sizeof(PV));
    static_assert(alignof(PU) == alignof(PV));
    static_assert(std::is_trivially_copyable_v<PU>
                  == std::is_trivially_copyable_v<PV>);
    // The operators the class must own; an omitted one would compare
    // POINTER VALUES through the base.
    static_assert(std::equality_comparable<PU>);
    static_assert(requires (const PU& a, const PU& b) { { a < b } -> std::same_as<bool>; });
    static_assert(requires (const PU& a, const PU& b) { { a <= b } -> std::same_as<bool>; });
    static_assert(requires (const PU& a, const PU& b) { { a > b } -> std::same_as<bool>; });
    static_assert(requires (const PU& a, const PU& b) { { a >= b } -> std::same_as<bool>; });
    // The read borrow is a ONE-WAY door: it must not slide back into the
    // mutable form, which is the whole point of the const being on the
    // pointees.
    static_assert(std::is_same_v<PU::const_form, Union<const A*, const B*>>);
    static_assert(!std::is_convertible_v<PU::const_form, PU>);
    // The nullable pack, and the packs that are not borrows.
    static_assert(vc_borrow_pack<std::monostate, A*>);
    static_assert(!vc_borrow_pack<A, B>);
    static_assert(!vc_borrow_pack<std::monostate>);
    // The read borrow is offered for this pack and for no other.
    static_assert(u_has_as_const<PU>::value);
    static_assert(!u_has_as_const<Union<std::int32_t, double>>::value);
};

// `as_const()` is a fold over the alternatives, so a pack whose legs no
// generated call site happens to cover compiles only here. Running the
// roundtrip at compile time keeps both the pointer and the monostate leg
// instantiated, and keeps the result usable in a constant expression --
// which also proves it is safe on a prvalue: the result copies the
// pointers, it does not borrow the operand.
struct u_pin_rec { int x; };

constexpr bool union_as_const_roundtrip() {
    u_pin_rec a{7};
    Union<std::monostate, u_pin_rec*> u{&a};
    auto c = u.as_const();
    if (c.index() != 1 || std::get<1>(c)->x != 7) return false;
    Union<std::monostate, u_pin_rec*> n{std::monostate{}};
    if (n.as_const().index() != 0) return false;
    return Union<std::monostate, u_pin_rec*>{&a}.as_const().index() == 1;
}
static_assert(union_as_const_roundtrip());

// The BUFFER alternatives. `bytes` / `String` are their own C++ classes
// derived from std::vector<uint8_t> / std::string, so the leaf reaches an
// inherited operator through a derived-to-base deduction and the printer must
// resolve the buffer's own `print_element` rather than the unconstrained
// generic. They are VALUE alternatives, so the one leaf's per-pair choice
// (`vc_ptr_alt`) sends them down the value legs -- these pins are what say
// the derived class does not break that. No TPy program can witness either today -- a compare over such a
// union rejects in sema (BUGS.md#bytes-value-union-boundary-rejects) -- so the
// pin is what keeps the runtime half correct until it can.
struct union_buffer_pins {
    using UB = Union<std::int32_t, Bytes>;
    using US = Union<std::int32_t, String>;
    static_assert(std::equality_comparable<UB>);
    static_assert(std::equality_comparable<US>);
    static_assert(requires (const UB& a, const UB& b) { { a < b } -> std::same_as<bool>; });
    static_assert(requires (const US& a, const US& b) { { a < b } -> std::same_as<bool>; });
    // The leaf at each alternative PAIR, which is what a derived buffer class
    // puts at risk: the same-type legs must find the base's inherited
    // operator through a derived-to-base deduction, and the mixed legs must
    // resolve at all rather than fall into the numeric arms. Well-formedness
    // only -- `py_eq` is not constexpr, so the ANSWERS cannot be asserted
    // here; the union's own operators are what carry them.
    static_assert(requires (const Bytes& x, const std::int32_t& n) {
        { detail::py_eq(x, x) } -> std::same_as<bool>;
        { detail::py_eq(n, x) } -> std::same_as<bool>;
        { detail::py_eq(x, n) } -> std::same_as<bool>;
    });
    static_assert(requires (const String& x, const std::int32_t& n) {
        { detail::py_eq(x, x) } -> std::same_as<bool>;
        { detail::py_eq(n, x) } -> std::same_as<bool>;
        { detail::py_eq(x, n) } -> std::same_as<bool>;
    });
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
