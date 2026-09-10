/**
 * TurboPython Runtime - The value-union comparison leaf
 *
 * Python's comparison rule at ONE pair of alternative types, which is what
 * `tpy::Union`'s operators visit down to (union_type.hpp). Not a public
 * surface: generated code never names anything here, it spells `(a == b)` on
 * a `::tpy::Union` and the type's own operators land in this file.
 *
 * The leaf is defined as "the answer the monomorphic twin gives": for one
 * pair of alternative types, whatever the compiler emits when the user
 * writes that comparison on two plain variables of those types. That is why
 * a mixed-sign integer pair goes through `std::cmp_*` and an `int` (BigInt)
 * against a float goes through `static_cast<double>` -- those are the twin's
 * renders. A composite alternative (a `std::vector` member of a recursive
 * alias) needs no recursion here: the leaf reaches its own `operator==`,
 * which reaches the element's, which is a `Union` again.
 *
 * Depends on: bigint.hpp (the BigInt legs), type_name.hpp (the TypeError
 * message names TPy types, never C++ ones).
 */

#pragma once

#include <concepts>
#include <cstddef>
#include <cstdint>
#include <type_traits>
#include <utility>
#include <variant>

#include "bigint.hpp"
#include "core.hpp"
#include "type_name.hpp"

namespace tpy {

namespace detail {

// `Char` is C++ `char`; TPy's Int8/UInt8 are signed/unsigned char, so only
// the plain spelling is the character type.
template<typename T>
concept vc_char = std::same_as<std::remove_cv_t<T>, char>;

// The integer types `std::cmp_*` accepts: the mandate excludes bool and
// every character type, which is also the set the compiler's own mixed-sign
// gate keys on (`is_fixed_int_type` covers neither).
template<typename T>
concept vc_cmp_integer =
    std::integral<T>
    && !std::same_as<std::remove_cv_t<T>, bool>
    && !vc_char<T>
    && !std::same_as<std::remove_cv_t<T>, char8_t>
    && !std::same_as<std::remove_cv_t<T>, char16_t>
    && !std::same_as<std::remove_cv_t<T>, char32_t>
    && !std::same_as<std::remove_cv_t<T>, wchar_t>;

template<typename A, typename B>
concept vc_mixed_sign =
    vc_cmp_integer<A> && vc_cmp_integer<B>
    && (std::is_signed_v<A> != std::is_signed_v<B>);

template<typename T>
concept vc_bigint = std::same_as<std::remove_cv_t<T>, BigInt>;

// A `char` opposite a number is Python's `str` opposite `int`/`float`: never
// equal, never ordered. Without this leg the leaf would compare the two
// numerically, since `char` is an integer type in C++ but not in Python.
template<typename A, typename B>
concept vc_char_vs_number =
    (vc_char<A> && !vc_char<B> && (std::is_arithmetic_v<B> || vc_bigint<B>))
    || (vc_char<B> && !vc_char<A> && (std::is_arithmetic_v<A> || vc_bigint<A>));

template<typename T>
concept vc_none = std::same_as<std::remove_cv_t<T>, std::monostate>;

// The visit reached the SAME alternative on both sides. Used by `py_eq`
// only, to withhold its "values of unrelated types are never equal" answer:
// that rule is about DIFFERENT types, and for one type with no `==` Python
// falls back to identity, which a by-value visitor cannot see -- so the
// program must not compile, as the bare operator made it before these
// helpers existed. Ordering needs no such gate: `Fixed() < Fixed()` on a
// type with no ordering raises TypeError in CPython, so `py_cmp` answers a
// same-type pair with that same runtime raise.
template<typename A, typename B>
concept vc_same_alternative =
    std::same_as<std::remove_cvref_t<A>, std::remove_cvref_t<B>>;

// A dependent false, so a static_assert fires only when its `if constexpr`
// branch is actually instantiated.
template<typename...>
inline constexpr bool vc_always_false = false;

// Widen an integer to the exact width BigInt has a constructor for. A bare
// `BigInt(x)` is ambiguous for uint32_t (three candidates, all conversions),
// which is why the widening is spelled here.
template<typename T>
inline BigInt vc_to_bigint(const T& x) {
    if constexpr (std::is_signed_v<T>) {
        return BigInt(static_cast<std::int64_t>(x));
    } else {
        return BigInt(static_cast<std::uint64_t>(x));
    }
}

// Python equality at ONE pair of alternative types.
template<typename A, typename B>
inline bool py_eq(const A& a, const B& b) {
    if constexpr (vc_char_vs_number<A, B>) {
        return false;
    } else if constexpr (vc_mixed_sign<A, B>) {
        return std::cmp_equal(a, b);
    } else if constexpr (vc_bigint<A> && std::is_floating_point_v<B>) {
        return static_cast<double>(a) == b;
    } else if constexpr (std::is_floating_point_v<A> && vc_bigint<B>) {
        return a == static_cast<double>(b);
    } else if constexpr (vc_bigint<A> && vc_cmp_integer<B>) {
        return a == vc_to_bigint(b);
    } else if constexpr (vc_cmp_integer<A> && vc_bigint<B>) {
        return vc_to_bigint(a) == b;
    } else if constexpr (requires { { a == b } -> std::convertible_to<bool>; }) {
        return a == b;
    } else if constexpr (!vc_same_alternative<A, B>) {
        // Python: values of unrelated types are never equal.
        return false;
    } else {
        static_assert(vc_always_false<A, B>,
                      "comparing two values of one TPy type that defines no "
                      "equality: give the type an __eq__ (or @dataclass). "
                      "Answering False here would invent a result Python "
                      "takes from identity.");
        return false;
    }
}

enum class CmpOp { Lt, Le, Gt, Ge };

constexpr const char* cmp_op_name(CmpOp op) {
    switch (op) {
        case CmpOp::Lt: return "<";
        case CmpOp::Le: return "<=";
        case CmpOp::Gt: return ">";
        default:        return ">=";
    }
}

// The operator itself, never a reduction onto `<`: `a <= b` and `!(b < a)`
// disagree when an operand is NaN, and the twin renders the operator.
template<CmpOp Op, typename X, typename Y>
inline bool apply_cmp(const X& x, const Y& y) {
    if constexpr (Op == CmpOp::Lt) return x < y;
    else if constexpr (Op == CmpOp::Le) return x <= y;
    else if constexpr (Op == CmpOp::Gt) return x > y;
    else if constexpr (Op == CmpOp::Ge) return x >= y;
    else static_assert(vc_always_false<X, Y>, "unhandled comparison operator");
}

template<CmpOp Op, typename A, typename B>
concept vc_native_cmp =
    (Op == CmpOp::Lt
     && requires (const A& x, const B& y) { { x < y } -> std::convertible_to<bool>; })
    || (Op == CmpOp::Le
        && requires (const A& x, const B& y) { { x <= y } -> std::convertible_to<bool>; })
    || (Op == CmpOp::Gt
        && requires (const A& x, const B& y) { { x > y } -> std::convertible_to<bool>; })
    || (Op == CmpOp::Ge
        && requires (const A& x, const B& y) { { x >= y } -> std::convertible_to<bool>; });

// The alternative pairs Python can order at all. `None` is excluded on both
// sides: `None < None` is a TypeError in CPython, though monostate has a C++
// `<`.
template<CmpOp Op, typename A, typename B>
concept vc_orderable =
    !vc_char_vs_number<A, B> && !vc_none<A> && !vc_none<B>
    && (vc_mixed_sign<A, B>
        || (vc_bigint<A> && std::is_floating_point_v<B>)
        || (std::is_floating_point_v<A> && vc_bigint<B>)
        || (vc_bigint<A> && vc_cmp_integer<B>)
        || (vc_cmp_integer<A> && vc_bigint<B>)
        || vc_native_cmp<Op, A, B>);

// Python ordering at ONE pair of alternative types, operands in SOURCE
// order so the TypeError names them the way the user wrote them.
template<CmpOp Op, typename A, typename B>
inline bool py_cmp(const A& a, const B& b) {
    // A SAME-type pair with no ordering raises like any other unorderable
    // pair -- deliberately unlike the equality leaf. CPython raises at
    // runtime for `Fixed() < Fixed()` on a type with no ordering, so the
    // raise IS the parity answer; equality has no such answer to give
    // (Python falls back to identity, which the runtime cannot see), which
    // is why only that leaf refuses to compile.
    if constexpr (!vc_orderable<Op, A, B>) {
        raise_type_error("'{}' not supported between instances of '{}' and '{}'",
                         cmp_op_name(Op), type_name<A>(), type_name<B>());
    } else if constexpr (vc_mixed_sign<A, B>) {
        if constexpr (Op == CmpOp::Lt) return std::cmp_less(a, b);
        else if constexpr (Op == CmpOp::Le) return std::cmp_less_equal(a, b);
        else if constexpr (Op == CmpOp::Gt) return std::cmp_greater(a, b);
        else return std::cmp_greater_equal(a, b);
    } else if constexpr (vc_bigint<A> && std::is_floating_point_v<B>) {
        return apply_cmp<Op>(static_cast<double>(a), b);
    } else if constexpr (std::is_floating_point_v<A> && vc_bigint<B>) {
        return apply_cmp<Op>(a, static_cast<double>(b));
    } else if constexpr (vc_bigint<A> && vc_cmp_integer<B>) {
        return apply_cmp<Op>(a, vc_to_bigint(b));
    } else if constexpr (vc_cmp_integer<A> && vc_bigint<B>) {
        return apply_cmp<Op>(vc_to_bigint(a), b);
    } else {
        return apply_cmp<Op>(a, b);
    }
}

// The comparison a union's operator performs, as a template parameter rather
// than a callable -- the dispatch below takes no function object.
enum class UnionOp { Eq, Lt, Le, Gt, Ge };

template<UnionOp Op, class X, class Y>
inline bool union_leaf(const X& x, const Y& y) {
    if constexpr (Op == UnionOp::Eq) return py_eq(x, y);
    else if constexpr (Op == UnionOp::Lt) return py_cmp<CmpOp::Lt>(x, y);
    else if constexpr (Op == UnionOp::Le) return py_cmp<CmpOp::Le>(x, y);
    else if constexpr (Op == UnionOp::Gt) return py_cmp<CmpOp::Gt>(x, y);
    else return py_cmp<CmpOp::Ge>(x, y);
}

// Hand-rolled index dispatch instead of `std::visit`. Visiting two variants
// builds an N x N table of function pointers and calls through it, which the
// optimiser is not obliged to fold -- a two-alternative compare then pays an
// indirect call it does not need. These chains are plain `if` tests over
// `index()` with direct calls at the leaves, and `std::get_if` rather than
// `std::get` so no branch can throw. Instantiation is unchanged: the
// recursion still reaches every (I, J) pair, so the leaf's static_assert for
// a same-type pair with no equality fires exactly where visit made it fire.
// An operand that is valueless by exception -- a throwing copy during an
// assignment leaves the variant holding no alternative -- matches no index
// and falls off the end of a chain, which throws `std::bad_variant_access`.
//
// The throw sits behind a named cold function rather than in the chain:
// spelled inline, the exception machinery grows the chain past what clang
// will inline, and the whole dispatch moves out of line behind a call --
// which is the cost the chain exists to avoid.
[[noreturn]] inline void vc_valueless() { throw std::bad_variant_access{}; }

template<UnionOp Op, std::size_t I, std::size_t J, class... Ts>
inline bool union_dispatch_rhs(const std::variant<Ts...>& a,
                               const std::variant<Ts...>& b) {
    if constexpr (J == sizeof...(Ts)) {
        vc_valueless();
    } else {
        if (b.index() == J) {
            return union_leaf<Op>(*std::get_if<I>(&a), *std::get_if<J>(&b));
        }
        return union_dispatch_rhs<Op, I, J + 1>(a, b);
    }
}

template<UnionOp Op, std::size_t I, class... Ts>
inline bool union_dispatch_lhs(const std::variant<Ts...>& a,
                               const std::variant<Ts...>& b) {
    if constexpr (I == sizeof...(Ts)) {
        vc_valueless();
    } else {
        if (a.index() == I) {
            return union_dispatch_rhs<Op, I, 0>(a, b);
        }
        return union_dispatch_lhs<Op, I + 1>(a, b);
    }
}

template<UnionOp Op, class... Ts>
inline bool union_compare(const std::variant<Ts...>& a,
                          const std::variant<Ts...>& b) {
    return union_dispatch_lhs<Op, 0>(a, b);
}

}  // namespace detail

}  // namespace tpy
