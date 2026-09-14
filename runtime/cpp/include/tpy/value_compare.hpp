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
 * a mixed-sign integer pair goes through `std::cmp_*` -- the twin's render.
 * The pairs where the leaf is CPython's answer rather than the twin's are an
 * integer against a float -- `int` (BigInt) and every fixed width alike: the
 * twin still rounds the int to a double
 * (`BUGS.md#int-float-compare-rounds-bigint`), the leaf compares exactly.
 * That is deliberate and must not be "fixed" toward the twin. A composite
 * alternative (a `std::vector` member of a recursive
 * alias) needs no recursion here: the leaf reaches its own `operator==`,
 * which reaches the element's, which is a `Union` again.
 *
 * Depends on: bigint.hpp (the BigInt legs), type_name.hpp (the TypeError
 * message names TPy types, never C++ ones).
 */

#pragma once

#include <cmath>
#include <concepts>
#include <cstddef>
#include <cstdint>
#include <string>
#include <type_traits>
#include <utility>
#include <variant>

#include "bigint.hpp"
#include "core.hpp"
#include "type_name.hpp"

namespace tpy {

namespace detail {

// `char` is C++ `char`; TPy's int8/uint8 are signed/unsigned char, so only
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

// Python's numeric tower on the integer side: every fixed-width int plus
// `bool` (Python's bool IS an int, so `True == 1.0`), minus the character
// types, which are `str` in Python and never compare numerically.
template<typename T>
concept vc_py_integer =
    vc_cmp_integer<T> || std::same_as<std::remove_cv_t<T>, bool>;

// A `char` opposite a number is Python's `str` opposite `int`/`float`: never
// equal, never ordered. Without this leg the leaf would compare the two
// numerically, since `char` is an integer type in C++ but not in Python.
template<typename A, typename B>
concept vc_char_vs_number =
    (vc_char<A> && !vc_char<B> && (std::is_arithmetic_v<B> || vc_bigint<B>))
    || (vc_char<B> && !vc_char<A> && (std::is_arithmetic_v<A> || vc_bigint<A>));

template<typename T>
concept vc_none = std::same_as<std::remove_cv_t<T>, std::monostate>;

// Whether a union's alternative pack is the BORROW form: every alternative
// a pointer, plus a monostate for a nullable union's None. One type spells
// both forms, so this is the fact that separates them -- the read-borrow
// conversion is offered for this pack only, and a consumer asking "is this
// the borrow form" asks the PACK rather than guessing at a type name.
// Per-PAIR leaf selection uses `vc_ptr_alt` below instead: the leaf sees two
// alternatives, not the pack.
template<class... Ts>
inline constexpr bool vc_borrow_pack =
    sizeof...(Ts) > 0
    && ((std::is_pointer_v<Ts> || std::same_as<Ts, std::monostate>) && ...)
    && (std::is_pointer_v<Ts> || ...);

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

// The four outcomes of comparing two numbers Python's way. `Unordered` is
// the NaN row: every operator but `!=` answers False on it, which no
// three-valued sign can express.
enum class NumOrder { Less, Equal, Greater, Unordered };

// An integer against a float compares EXACTLY, as CPython's
// float_richcompare does: the float is decomposed, never the int rounded to
// a double (which would make `2**53 + 1 == 2.0**53` True). Written as ONE
// three-way answer because a comparison must not allocate: the six operators
// are derived from it, and the whole int64 range is settled without
// constructing a BigInt.
//
// `2^63` and `-2^63` are exactly representable as doubles, so the two range
// guards are exact and everything they let through truncates into int64
// without overflow. A float with a fractional part equals no int and sits
// strictly between the two ints it truncates toward, so once `a == trunc(b)`
// the fraction's sign is the answer.
inline NumOrder vc_i64_cmp_float(std::int64_t a, double b) noexcept {
    if (std::isnan(b)) return NumOrder::Unordered;
    if (b >= 9223372036854775808.0) return NumOrder::Less;      // +inf too
    if (b < -9223372036854775808.0) return NumOrder::Greater;   // -inf too
    const double t = std::trunc(b);
    const std::int64_t ti = static_cast<std::int64_t>(t);
    if (a < ti) return NumOrder::Less;
    if (a > ti) return NumOrder::Greater;
    if (b > t) return NumOrder::Less;
    if (b < t) return NumOrder::Greater;
    return NumOrder::Equal;
}

// The unsigned twin, for a `uint64` above INT64_MAX that the signed form
// could not hold. `2^64` is exactly representable, so the upper guard is
// exact as well.
inline NumOrder vc_u64_cmp_float(std::uint64_t a, double b) noexcept {
    if (std::isnan(b)) return NumOrder::Unordered;
    if (b >= 18446744073709551616.0) return NumOrder::Less;     // +inf too
    if (b < 0.0) return NumOrder::Greater;                      // -inf too
    const double t = std::trunc(b);
    const std::uint64_t tu = static_cast<std::uint64_t>(t);
    if (a < tu) return NumOrder::Less;
    if (a > tu) return NumOrder::Greater;
    if (b > t) return NumOrder::Less;
    if (b < t) return NumOrder::Greater;
    return NumOrder::Equal;
}

// Every integral width reaches one of the two cores: only a `uint64` can
// hold a value int64 cannot, so it alone needs the unsigned form.
template<typename T>
inline NumOrder vc_int_cmp_float(T a, double b) noexcept {
    if constexpr (std::is_signed_v<T> || sizeof(T) < sizeof(std::uint64_t)) {
        return vc_i64_cmp_float(static_cast<std::int64_t>(a), b);
    } else {
        return vc_u64_cmp_float(static_cast<std::uint64_t>(a), b);
    }
}

template<CmpOp Op>
inline bool vc_order_to_bool(NumOrder o) noexcept {
    if (o == NumOrder::Unordered) return false;
    if constexpr (Op == CmpOp::Lt) return o == NumOrder::Less;
    else if constexpr (Op == CmpOp::Le) return o != NumOrder::Greater;
    else if constexpr (Op == CmpOp::Gt) return o == NumOrder::Greater;
    else return o != NumOrder::Less;
}

// `int` (BigInt) against a float. A BigInt inside int64 range -- which is
// every ordinary program value -- answers through the allocation-free core;
// only a genuinely big one falls back to decomposing the float into limbs.
// `BigInt::from_float` is exact for every integral double, and `<` / `>=`
// need the float's ceiling where `<=` / `>` need its floor.
inline bool vc_bigint_eq_float(const BigInt& a, double b) {
    std::int64_t ai;
    if (a.to_i64_checked(ai)) {
        return vc_i64_cmp_float(ai, b) == NumOrder::Equal;
    }
    if (!std::isfinite(b) || std::trunc(b) != b) return false;
    return a == BigInt::from_float(b);
}

template<CmpOp Op>
inline bool vc_bigint_cmp_float(const BigInt& a, double b) {
    std::int64_t ai;
    if (a.to_i64_checked(ai)) {
        return vc_order_to_bool<Op>(vc_i64_cmp_float(ai, b));
    }
    if (std::isnan(b)) return false;
    if (std::isinf(b)) {
        return (Op == CmpOp::Lt || Op == CmpOp::Le) ? b > 0 : b < 0;
    }
    if constexpr (Op == CmpOp::Lt || Op == CmpOp::Ge) {
        return apply_cmp<Op>(a, BigInt::from_ceil(b));
    } else {
        return apply_cmp<Op>(a, BigInt::from_floor(b));
    }
}

// The float-on-the-left spelling of the same comparison, with the operator
// mirrored.
template<CmpOp Op>
constexpr CmpOp vc_mirror_op() {
    if constexpr (Op == CmpOp::Lt) return CmpOp::Gt;
    else if constexpr (Op == CmpOp::Le) return CmpOp::Ge;
    else if constexpr (Op == CmpOp::Gt) return CmpOp::Lt;
    else return CmpOp::Le;
}

// Python equality at ONE pair of alternative types.
template<typename A, typename B>
inline bool py_eq(const A& a, const B& b) {
    if constexpr (vc_char_vs_number<A, B>) {
        return false;
    } else if constexpr (vc_mixed_sign<A, B>) {
        return std::cmp_equal(a, b);
    } else if constexpr (vc_bigint<A> && std::is_floating_point_v<B>) {
        return vc_bigint_eq_float(a, static_cast<double>(b));
    } else if constexpr (std::is_floating_point_v<A> && vc_bigint<B>) {
        return vc_bigint_eq_float(b, static_cast<double>(a));
    } else if constexpr (vc_bigint<A> && vc_cmp_integer<B>) {
        return a == vc_to_bigint(b);
    } else if constexpr (vc_cmp_integer<A> && vc_bigint<B>) {
        return vc_to_bigint(a) == b;
    } else if constexpr (vc_py_integer<A> && std::is_floating_point_v<B>) {
        return vc_int_cmp_float(a, static_cast<double>(b)) == NumOrder::Equal;
    } else if constexpr (std::is_floating_point_v<A> && vc_py_integer<B>) {
        return vc_int_cmp_float(b, static_cast<double>(a)) == NumOrder::Equal;
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

// Python INEQUALITY at ONE pair of alternative types -- not `!py_eq`.
// CPython derives `!=` from `__eq__` only when the type declares no
// `__ne__`; a declared one is called instead and need not be the negation.
// C++ mirrors that exactly: a TPy `__ne__` renders `operator!=`, and a type
// with only `__eq__` gets the C++20 rewritten `!=`, which IS the derivation
// CPython performs -- so asking for the twin's `!=` gets both answers right,
// where negating equality got the second one right and the first one wrong.
// The numeric legs repeat py_eq's widening rather than route through the
// operator, for the reason py_eq has them at all (a mixed-sign `!=` compares
// as C++ does, not as Python does); the final fallback is `!py_eq` for the
// rows where no `!=` exists to ask -- the cross-type row, and the same-type
// row with no equality at all, whose static_assert must still fire.
template<typename A, typename B>
inline bool py_ne(const A& a, const B& b) {
    if constexpr (vc_char_vs_number<A, B>) {
        return true;
    } else if constexpr (vc_mixed_sign<A, B>) {
        return !std::cmp_equal(a, b);
    } else if constexpr (vc_bigint<A> && std::is_floating_point_v<B>) {
        return !vc_bigint_eq_float(a, static_cast<double>(b));
    } else if constexpr (std::is_floating_point_v<A> && vc_bigint<B>) {
        return !vc_bigint_eq_float(b, static_cast<double>(a));
    } else if constexpr (vc_bigint<A> && vc_cmp_integer<B>) {
        return a != vc_to_bigint(b);
    } else if constexpr (vc_cmp_integer<A> && vc_bigint<B>) {
        return vc_to_bigint(a) != b;
    } else if constexpr (vc_py_integer<A> && std::is_floating_point_v<B>) {
        return vc_int_cmp_float(a, static_cast<double>(b)) != NumOrder::Equal;
    } else if constexpr (std::is_floating_point_v<A> && vc_py_integer<B>) {
        return vc_int_cmp_float(b, static_cast<double>(a)) != NumOrder::Equal;
    } else if constexpr (requires { { a != b } -> std::convertible_to<bool>; }) {
        return a != b;
    } else {
        return !py_eq(a, b);
    }
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
        return vc_bigint_cmp_float<Op>(a, static_cast<double>(b));
    } else if constexpr (std::is_floating_point_v<A> && vc_bigint<B>) {
        return vc_bigint_cmp_float<vc_mirror_op<Op>()>(b, static_cast<double>(a));
    } else if constexpr (vc_bigint<A> && vc_cmp_integer<B>) {
        return apply_cmp<Op>(a, vc_to_bigint(b));
    } else if constexpr (vc_cmp_integer<A> && vc_bigint<B>) {
        return apply_cmp<Op>(vc_to_bigint(a), b);
    } else if constexpr (vc_py_integer<A> && std::is_floating_point_v<B>) {
        return vc_order_to_bool<Op>(vc_int_cmp_float(a, static_cast<double>(b)));
    } else if constexpr (std::is_floating_point_v<A> && vc_py_integer<B>) {
        return vc_order_to_bool<vc_mirror_op<Op>()>(
            vc_int_cmp_float(b, static_cast<double>(a)));
    } else {
        return apply_cmp<Op>(a, b);
    }
}

// A BORROWED alternative: the union holds a pointer to the object rather
// than the object. `std::monostate` is not one -- it is None on either form
// -- so a pair is borrowed when EITHER side is a pointer.
template<typename T>
concept vc_ptr_alt = std::is_pointer_v<std::remove_cv_t<T>>;

// A borrowed alternative's TPy name is the POINTEE's: a diagnostic must
// never name a C++ pointer.
template<typename T>
inline std::string vc_ptr_name() {
    if constexpr (vc_none<T>) {
        return type_name<std::monostate>();
    } else {
        return type_name<std::remove_cv_t<std::remove_pointer_t<T>>>();
    }
}

// Python equality at ONE pair of BORROWED alternatives. Identity is the
// answer only a borrow position can give: there the pointer IS the object,
// which is what CPython falls back to for a type that defines no `__eq__`.
// (At a storage position TPy has copied the object into the slot, so the
// slot's address is not its identity -- which is why the value leaf refuses
// to compile for that pair instead of inventing an answer.)
template<typename A, typename B>
inline bool py_eq_ptr(const A& a, const B& b) {
    if constexpr (vc_none<A> && vc_none<B>) {
        return true;                       // None == None
    } else if constexpr (vc_none<A> || vc_none<B>) {
        return false;                      // None == <object>
    } else {
        using PA = std::remove_cv_t<std::remove_pointer_t<A>>;
        using PB = std::remove_cv_t<std::remove_pointer_t<B>>;
        if constexpr (std::same_as<PA, PB>
                      && !requires (const PA& x, const PB& y) {
                             { x == y } -> std::convertible_to<bool>; }) {
            return static_cast<const void*>(a) == static_cast<const void*>(b);
        } else {
            // The VALUE legs, on the pointees -- a borrow form can hold
            // pointers to value members (`bytearray | int32`), and `1` vs
            // `1.0` through such a union is Python-equal.
            return py_eq(*a, *b);
        }
    }
}

// Python inequality at ONE pair of BORROWED alternatives -- the pointee's
// own `!=` (see `py_ne`), not the negation of `py_eq_ptr`.
template<typename A, typename B>
inline bool py_ne_ptr(const A& a, const B& b) {
    if constexpr (vc_none<A> && vc_none<B>) {
        return false;                      // None != None
    } else if constexpr (vc_none<A> || vc_none<B>) {
        return true;                       // None != <object>
    } else {
        using PA = std::remove_cv_t<std::remove_pointer_t<A>>;
        using PB = std::remove_cv_t<std::remove_pointer_t<B>>;
        // Identity only when the pair has NEITHER operator. Gating on `==`
        // alone would take identity for a record that declares `__ne__` and
        // no `__eq__`, which CPython answers by calling that `__ne__`.
        if constexpr (std::same_as<PA, PB>
                      && !requires (const PA& x, const PB& y) {
                             { x != y } -> std::convertible_to<bool>; }
                      && !requires (const PA& x, const PB& y) {
                             { x == y } -> std::convertible_to<bool>; }) {
            return static_cast<const void*>(a) != static_cast<const void*>(b);
        } else {
            return py_ne(*a, *b);
        }
    }
}

// Python ordering at ONE pair of BORROWED alternatives.
template<CmpOp Op, typename A, typename B>
inline bool py_cmp_ptr(const A& a, const B& b) {
    if constexpr (vc_none<A> || vc_none<B>) {
        // `None < x` is a TypeError in CPython. Raised here rather than
        // through `py_cmp` so the message names the pointees, not `Dog*`.
        raise_type_error("'{}' not supported between instances of '{}' and '{}'",
                         cmp_op_name(Op), vc_ptr_name<A>(), vc_ptr_name<B>());
    } else {
        return py_cmp<Op>(*a, *b);
    }
}

// The comparison a union's operator performs, as a template parameter rather
// than a callable -- the dispatch below takes no function object.
enum class UnionOp { Eq, Ne, Lt, Le, Gt, Ge };

// ONE leaf for both forms of a union, choosing per alternative PAIR: a pair
// with a pointer on either side is a borrow and answers through the pointee
// (identity included), everything else answers by value. Keyed on the
// alternative TYPES the dispatch reached, not on the pack and not on a guess
// about the union's spelling -- a mixed pack would still get the right leg
// per pair. A monostate/monostate pair is None on both forms and the two
// legs agree on it, so it costs nothing to leave with the value legs.
template<UnionOp Op, class X, class Y>
inline bool union_leaf(const X& x, const Y& y) {
    if constexpr (vc_ptr_alt<X> || vc_ptr_alt<Y>) {
        if constexpr (Op == UnionOp::Eq) return py_eq_ptr(x, y);
        else if constexpr (Op == UnionOp::Ne) return py_ne_ptr(x, y);
        else if constexpr (Op == UnionOp::Lt) return py_cmp_ptr<CmpOp::Lt>(x, y);
        else if constexpr (Op == UnionOp::Le) return py_cmp_ptr<CmpOp::Le>(x, y);
        else if constexpr (Op == UnionOp::Gt) return py_cmp_ptr<CmpOp::Gt>(x, y);
        else return py_cmp_ptr<CmpOp::Ge>(x, y);
    } else {
        if constexpr (Op == UnionOp::Eq) return py_eq(x, y);
        else if constexpr (Op == UnionOp::Ne) return py_ne(x, y);
        else if constexpr (Op == UnionOp::Lt) return py_cmp<CmpOp::Lt>(x, y);
        else if constexpr (Op == UnionOp::Le) return py_cmp<CmpOp::Le>(x, y);
        else if constexpr (Op == UnionOp::Gt) return py_cmp<CmpOp::Gt>(x, y);
        else return py_cmp<CmpOp::Ge>(x, y);
    }
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
