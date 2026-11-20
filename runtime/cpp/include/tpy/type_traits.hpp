/**
 * TurboPython Runtime - Type Traits
 *
 * Compile-time type classification for value vs reference semantics.
 */

#pragma once

#include <algorithm>
#include <array>
#include <concepts>
#include <cstdint>
#include <iterator>
#include <memory>
#include <optional>
#include <ranges>
#include <span>
#include <string>
#include <string_view>
#include <tuple>
#include <type_traits>
#include <utility>
#include <variant>
#include <vector>

namespace tpy {

// Forward declaration for BigInt specialization
class BigInt;

// A pointer to one object, which every runtime overload reads through: its
// pointee's text, hash or length, `None` when null -- never its address. A
// `char*` included: a string literal reaches the runtime as a `const
// char[N]` array (or the compiler's `std::string_view`), never as a bare
// pointer. Never an opaque `void*`.
template<typename T>
concept object_pointer = std::is_pointer_v<T>
                         && !std::is_void_v<std::remove_pointer_t<T>>;

// --- Type trait for value vs reference semantics ---

/**
 * is_value_type - Type trait for determining copy vs reference semantics.
 *
 * Value types (primitives) are returned by copy when accessed from containers.
 * Object types (records, nested containers) are returned by reference.
 */
// Enums are decided by kind, not one specialization per enum: every TPy enum
// (and every @native enum binding a C/C++ one) is a value type in
// `tpyc/type_def_registry.py`, and no TPy reference type renders as a C++ enum.
template<typename T> struct is_value_type : std::bool_constant<std::is_enum_v<T>> {};

// const-qualified types share their unqualified value-ness, so a readonly
// `*args` param lowered to `varargs<const T>` selects the same specialization
// (value vs dual-mode) as the mutable `varargs<T>`.
template<typename T> struct is_value_type<const T> : is_value_type<T> {};

// The value-type rows below are the ones whose type has no defining header of
// ours: the std:: and builtin types, plus val_or_ref, defined here. A TPy
// runtime type declares its own value-ness -- and the Send / Sync overrides
// that go with it -- next to its definition: span_iter.hpp, slice.hpp,
// range.hpp, varargs.hpp, dict_ops.hpp. (The is_send / is_sync rows for
// ordered_map / ordered_set are still here behind forward declarations; they
// are reference types, so they have no value-type row to travel with.)

// Primitive value types
template<> struct is_value_type<int8_t> : std::true_type {};
template<> struct is_value_type<int16_t> : std::true_type {};
template<> struct is_value_type<int32_t> : std::true_type {};
template<> struct is_value_type<int64_t> : std::true_type {};
template<> struct is_value_type<uint8_t> : std::true_type {};
template<> struct is_value_type<uint16_t> : std::true_type {};
template<> struct is_value_type<uint32_t> : std::true_type {};
template<> struct is_value_type<uint64_t> : std::true_type {};
template<> struct is_value_type<bool> : std::true_type {};
template<> struct is_value_type<char> : std::true_type {};
template<> struct is_value_type<double> : std::true_type {};
template<> struct is_value_type<float> : std::true_type {};
template<> struct is_value_type<std::string> : std::true_type {};
template<> struct is_value_type<std::string_view> : std::true_type {};
template<> struct is_value_type<BigInt> : std::true_type {};
// std::monostate -- TPy NoneType at value-bearing positions (e.g. Future[None]).
// Pure unit type, trivially copyable; passes through containers and generics
// the same way primitives do.
template<> struct is_value_type<std::monostate> : std::true_type {};

// A borrowed view is copied, not aliased, at a generic slot. One row covers
// two TypeDefs -- Span[T] and Span[readonly[T]]; BytesView is its own type
// (buffer_types.hpp). `tests/test_runtime_value_type_parity.py` holds every
// such row (here and in the defining headers) against the registry, with the
// two C++ types that cannot be decided at all.
template<typename T, std::size_t E> struct is_value_type<std::span<T, E>> : std::true_type {};

// Tuples are value types (immutable in Python, always copied/moved)
template<typename... Ts> struct is_value_type<std::tuple<Ts...>> : std::true_type {};

// val_or_ref<T> is a lightweight pointer/value wrapper, always passed by value
template<typename T> struct val_or_ref;
template<typename T> struct is_value_type<val_or_ref<T>> : std::true_type {};

// own_arg<X> -- a generic's type argument `Own[X]` (`take[Own[P]]`,
// `app(mk, q)` binding `R = Own[P]`): the instantiation owns where its text
// says `T`. Never a C++ object type, so it stays incomplete; each form trait
// below gives it the owned arm the hand-written `Own[X]` twin spells.
// Sema writes the tag only where `Own[X]` is not plain `X` (a by-value
// scalar such as `int32` owns either way).
template<typename X> struct own_arg;

namespace detail {
    template<typename T> struct own_arg_peel { using type = T; };
    template<typename X> struct own_arg_peel<own_arg<X>> { using type = X; };
    template<typename X> struct own_arg_peel<const own_arg<X>> { using type = const X; };
    template<typename T> inline constexpr bool is_own_arg = false;
    template<typename X> inline constexpr bool is_own_arg<own_arg<X>> = true;
    template<typename X> inline constexpr bool is_own_arg<const own_arg<X>> = true;
    template<typename T> inline constexpr bool always_false = false;
}

// The storage form of a generic `T`: what a container element, a field or
// a concept test holds -- `X` at `own_arg<X>`, `T` otherwise.
template<typename T> using storage_t = typename detail::own_arg_peel<T>::type;

template<typename T>
inline constexpr bool is_own_arg_v = detail::is_own_arg<T>;

// A trait that asks the value-ness of the owned tag itself has no owned arm:
// fail its instantiation here rather than answer `false` and form an
// `own_arg<X>*` or `own_arg<X>&` downstream.
template<typename X> struct is_value_type<own_arg<X>> {
    static_assert(detail::always_false<X>,
                  "own_arg<X> reached a form trait without an owned arm; "
                  "ask storage_t<T> or add the arm");
};

template<typename T>
inline constexpr bool is_value_type_v = is_value_type<T>::value;

// A generic argument may arrive in its borrow slot (`val_or_ref<X>`, itself a
// value), so the two marker concepts answer by the slot's referent -- which
// keeps them mutually exclusive.
namespace detail {
    template<typename T> struct slot_referent { using type = T; };
    template<typename T> struct slot_referent<val_or_ref<T>> { using type = T; };
}

// C++ concept for the ValueType marker protocol
template<typename T>
concept ValueType =
    is_value_type_v<typename detail::slot_referent<
        std::remove_cv_t<storage_t<std::remove_cv_t<T>>>>::type>;

// C++ concept for the ReferenceType marker protocol. Sema decides which types
// conform; this refuses only a value type.
template<typename T>
concept ReferenceType = !ValueType<T>;

// C++ concept for the Copyable marker protocol. Maps to copy-constructibility,
// which is the operation a generic method like Box[T].clone() actually needs.
// std::copy_constructible would also require move-constructibility; we want
// just the copy step here, so check the trait directly. Two theoretical edge
// cases the trait admits but std::copy_constructible would reject: types with
// an `explicit`-only copy ctor (direct-init works, copy-init doesn't) and
// types that are copy-constructible but not destructible. Neither pattern is
// generated by TPy today; if @native ever introduces one, switch to the
// standard concept or add a static_assert in the requires-clause emit path.
template<typename T>
concept Copyable = std::is_copy_constructible_v<storage_t<T>>;

// C++ concept for the Default marker protocol: zero-argument construction
// of the type `T` holds (`X` at `T = Own[X]`).
template<typename T>
concept Default = std::default_initializable<storage_t<T>>;

// A call through an `Fn[[...], R]` parameter returns what the slot takes:
// convertible to its storage form (`X` at `R = Own[X]`).
template<typename Got, typename R>
concept returns_form = std::convertible_to<Got, storage_t<R>>;

// --- Ranges a consumer iterates directly ---

// A standard input range a consumer (sum, list(), str.join, extend, ...) may
// iterate through begin()/end(). A class declaring `tpy_iter_dunder` iterates
// by its own `__iter__` although it inherits a range -- the compiler declares
// it on a `class L(list[T])` that overrides `__iter__`, whose override every
// Python consumer calls.
template<typename R>
concept iterates_by_dunder = requires { typename std::remove_cvref_t<R>::tpy_iter_dunder; };

template<typename R>
concept native_input_range = std::ranges::input_range<R> && !iterates_by_dunder<R>;

// --- Lossless entry conversion ---

namespace detail {
template<typename From, typename To>
constexpr bool widens_scalar() {
    if constexpr (std::is_same_v<From, To>) {
        return true;
    } else if constexpr (!std::is_arithmetic_v<From>
                         || !std::is_arithmetic_v<To>
                         || std::is_same_v<From, bool>
                         || std::is_same_v<To, bool>) {
        return false;
    } else if constexpr (std::is_floating_point_v<From>
                         || std::is_floating_point_v<To>) {
        return std::is_floating_point_v<From> && std::is_floating_point_v<To>
            && sizeof(To) >= sizeof(From);
    } else if constexpr (std::is_signed_v<From> == std::is_signed_v<To>) {
        return sizeof(To) >= sizeof(From);
    } else {
        return std::is_unsigned_v<From> && sizeof(To) > sizeof(From);
    }
}
}  // namespace detail

/**
 * widens_to - whether every value of `From` converts to `To` unchanged: the
 * same type, an integer to one of its signedness at least as wide, an
 * unsigned integer to a strictly wider signed one, a floating type to one at
 * least as wide, and a tuple member by member. An integer to a floating type
 * is not one (int64 rounds in a double), nor is any container, `bool` or
 * class conversion: an update between two containers converts its entries
 * only where sema decided the source narrower than the target.
 */
template<typename From, typename To>
struct widens_to : std::bool_constant<detail::widens_scalar<From, To>()> {};

template<typename... From, typename... To>
    requires (sizeof...(From) == sizeof...(To))
struct widens_to<std::tuple<From...>, std::tuple<To...>>
    : std::bool_constant<(widens_to<From, To>::value && ...)> {};

template<typename From, typename To>
inline constexpr bool widens_to_v = widens_to<From, To>::value;

// --- Thread safety markers ---

/**
 * is_send - Type trait for thread-safe ownership transfer.
 *
 * Send types can be safely moved to another thread. All value types are Send
 * by default. Raw pointers and non-owning views are not Send.
 * Sema owns the authoritative per-type answer; codegen-emitted per-record
 * specializations mirroring it are deferred until hand-written C++ needs
 * to constrain on user records.
 */
template<typename T> struct is_send : is_value_type<T> {};

// Pointers are not Send (raw pointer, no ownership guarantee)
template<typename T> struct is_send<T*> : std::false_type {};
template<typename T> struct is_send<const T*> : std::false_type {};

// string_view is not Send (borrows from another string)
template<> struct is_send<std::string_view> : std::false_type {};

// span is not Send (non-owning view of another container's storage)
template<typename T, std::size_t E> struct is_send<std::span<T, E>> : std::false_type {};

// Containers: Send if elements are Send
template<typename T, typename A> struct is_send<std::vector<T, A>> : is_send<T> {};
template<typename T, std::size_t N> struct is_send<std::array<T, N>> : is_send<T> {};

// Forward declarations for container specializations
template<typename K, typename V> class ordered_map;
template<typename T> class ordered_set;

// ordered_map: Send if both key and value are Send
template<typename K, typename V>
struct is_send<ordered_map<K, V>>
    : std::bool_constant<is_send<K>::value && is_send<V>::value> {};

// ordered_set: Send if element is Send
template<typename T>
struct is_send<ordered_set<T>> : is_send<T> {};

template<typename T>
concept Send = is_send<storage_t<T>>::value;

/**
 * is_sync - Type trait for thread-safe shared access.
 *
 * Sync types can be safely referenced from multiple threads. Immutable types
 * are Sync. Mutable containers (vector, ordered_map) are not Sync.
 * Sema owns the authoritative per-type answer; see is_send above for the
 * per-record mirror status.
 */
template<typename T> struct is_sync : is_value_type<T> {};

// Mutable pointers are not Sync
template<typename T> struct is_sync<T*> : std::false_type {};
// Const pointers are Sync if the pointee is Sync
template<typename T> struct is_sync<const T*> : is_sync<T> {};

// Mutable containers are not Sync
template<typename T, typename A> struct is_sync<std::vector<T, A>> : std::false_type {};
template<typename K, typename V> struct is_sync<ordered_map<K, V>> : std::false_type {};
template<typename T> struct is_sync<ordered_set<T>> : std::false_type {};
template<typename T, std::size_t N> struct is_sync<std::array<T, N>> : is_sync<T> {};

// Mutable span is not Sync; a read-only span is Sync if the element is
template<typename T, std::size_t E> struct is_sync<std::span<T, E>> : std::false_type {};
template<typename T, std::size_t E> struct is_sync<std::span<const T, E>> : is_sync<T> {};

template<typename T>
concept Sync = is_sync<storage_t<T>>::value;

// Dispatched through impl structs so `T = void` can be specialized;
// `val_or_ref_t<void> = void` lets `def f[T] -> T` instantiate with
// T=None (the naked `std::conditional_t<..., void, void&>` form would
// substitute to ill-formed `void&`).
namespace detail {
    template<typename T> struct val_or_ref_impl       { using type = std::conditional_t<is_value_type<T>::value, T,        T&>; };
    template<typename T> struct val_or_cref_impl      { using type = std::conditional_t<is_value_type<T>::value, T,        const T&>; };
    template<typename T> struct param_val_or_ref_impl { using type = std::conditional_t<is_value_type<T>::value, T,        T&>; };

    template<> struct val_or_ref_impl<void>       { using type = void; };

    // At `T = Own[X]` a result hands over: the twin's `-> Own[X]` by value
    // (`unique_ptr<X>` for a @dynamic base), in mutable and const slots alike.
    template<typename X> struct val_or_ref_impl<own_arg<X>>;
    template<typename X> struct val_or_cref_impl<own_arg<X>>;
    template<typename X> struct param_val_or_ref_impl<own_arg<X>>;
    template<> struct val_or_cref_impl<void>      { using type = void; };
    template<> struct param_val_or_ref_impl<void> { using type = void; };

    // `std::string` is TPy `str` and nothing else (TPy `String` is
    // `tpy::String`), so a generic slot at it takes the twin's parameter form:
    // the view, not an owned `std::string` the caller would have to
    // materialize. The bytes sibling of this row lives in buffer_types.hpp,
    // next to `tpy::Bytes`.
    template<> struct param_val_or_ref_impl<std::string> {
        using type = std::string_view;
    };

    // The value types whose hand-written parameter is a const reference
    // (`TypeDef.param_passing` CONST_REF, a tuple's `_param_spelling`): a
    // generic slot at one of them takes that form too, so it neither copies
    // where the twin borrows nor differs from it. `String`'s row lives in
    // buffer_types.hpp, next to `tpy::String`.
    template<> struct param_val_or_ref_impl<BigInt> {
        using type = const BigInt&;
    };
    template<typename... Ts> struct param_val_or_ref_impl<std::tuple<Ts...>> {
        using type = const std::tuple<Ts...>&;
    };
}

template<typename T> using val_or_ref_t       = typename detail::val_or_ref_impl<T>::type;
template<typename T> using val_or_cref_t      = typename detail::val_or_cref_impl<T>::type;
template<typename T> using param_val_or_ref_t = typename detail::param_val_or_ref_impl<T>::type;

// A generic `T` parameter slot holds a value of its storage type -- at
// `T = Own[X]`, and at a value instantiation whose parameter form is by value
// (`int32_t`, a ValueType record) -- rather than borrowing the caller's (a
// reference, or a view such as `string_view` at `str`). `param_handoff`
// below moves such a slot where the hand-written twin moves its by-value
// parameter: a hand-off, a direct `return x;`. Both operands are
// instantiated: the owned tag's own `param_val_or_ref_impl` row keeps it away
// from `is_value_type`. A value with no move constructor (a `@nomove`
// binding) keeps its copy.
template<typename T>
inline constexpr bool param_holds_value_v =
    is_own_arg_v<T>
    || (std::is_same_v<param_val_or_ref_t<T>, storage_t<T>>
        && std::is_move_constructible_v<storage_t<T>>);

// What a closure holds of a `T` parameter declared `P`: a value of its own (the
// parameter may be a view or a reference the call ends), or the caller's object
// through the parameter's own reference, so its const-ness stays the signature's.
// A struct because an init-capture deduces a value; this keeps the reference arm
// with one spelling. The forwarding ctor, not brace-init: `string_view` converts
// to `std::string` only explicitly.
template<typename T, typename P>
struct capture_slot {
    std::conditional_t<is_value_type<T>::value, val_or_ref_t<T>, P> v;
    template<typename U>
        requires (!std::same_as<std::remove_cvref_t<U>, capture_slot>)
    explicit capture_slot(U&& u) : v(std::forward<U>(u)) {}
};

// Tuple-element borrow form for a generic T -- the pointer sibling of
// val_or_ref_t. A reference can't be a std::tuple member and the concrete
// tuple borrow form is std::tuple<..., T*>, so generic tuple slots use a bare
// pointer for non-value Ts (value Ts stay by value). This keeps a generic
// `tuple[T, ...]` param/return ABI-compatible with concrete pointer-form
// tuples at instantiation.
// At `T = Own[X]` the element owns (the twin's `tuple[Own[X], ...]` holds
// `X`); that arm sits beside the other owned arms below.
namespace detail {
    template<typename T> struct val_or_ptr_impl  { using type = std::conditional_t<is_value_type<T>::value, T, T*>; };
    template<typename T> struct val_or_cptr_impl { using type = std::conditional_t<is_value_type<T>::value, T, const T*>; };
    template<> struct val_or_ptr_impl<void>  { using type = void; };
    template<> struct val_or_cptr_impl<void> { using type = void; };
}
template<typename T> using val_or_ptr_t  = typename detail::val_or_ptr_impl<T>::type;
template<typename T> using val_or_cptr_t = typename detail::val_or_cptr_impl<T>::type;

// The `T | None` PARAMETER form for a generic T -- the nullable sibling of
// param_val_or_ref_t. A value T takes std::optional<T> by value, which is what
// the monomorphic twin (`def f(v: int32 | None)`) already takes, so a caller
// passes the value straight through instead of materializing a temp to point
// at. A reference T stays a pointer, so a mutation inside the body is visible
// to the caller (std::optional<T> would copy it). Return position is NOT this
// form: a `T*` return can alias a field, an optional cannot.
// At `T = Own[X]` the slot owns its payload, as the twin's `Own[X] | None`
// parameter (`std::optional<X>`): a borrow there would let the body write
// through to an object the caller handed over.
namespace detail {
    template<typename T> struct opt_param_impl  { using type = std::conditional_t<is_value_type<T>::value, std::optional<T>, T*>; };
    template<typename T> struct opt_cparam_impl { using type = std::conditional_t<is_value_type<T>::value, std::optional<T>, const T*>; };
    template<typename X> struct opt_param_impl<own_arg<X>>  { using type = std::optional<X>; };
    template<typename X> struct opt_cparam_impl<own_arg<X>> { using type = std::optional<X>; };
}
template<typename T> using opt_param_t  = typename detail::opt_param_impl<T>::type;
template<typename T> using opt_cparam_t = typename detail::opt_cparam_impl<T>::type;

// Read a generic `T | None` slot: `x is not None` is has_value() on the
// optional form and a null test on the pointer form. Spelled once here so a
// generic body's null test is form-neutral. (Dereferencing needs no helper --
// `*x` reads both forms.)
template<typename T>
constexpr bool opt_has_value(const T& x) {
    if constexpr (std::is_pointer_v<T>) {
        return x != nullptr;
    } else {
        return x.has_value();
    }
}

// Lift a generic `T | None` slot into its storage form (std::optional<T>, the
// field/container shape): the optional form already IS the storage form; the
// pointer form copies the pointee, nullptr becoming an empty optional. Dest is
// explicit for the same reason to_val_or_ptr's is -- the source alone does not
// name the slot.
template<typename Dest, typename Src>
Dest to_opt_storage(const Src& s) {
    if constexpr (std::is_pointer_v<Src>) {
        if (s != nullptr) return Dest(*s);
        return Dest{};
    } else {
        return s;
    }
}

// to_opt_storage's move sibling: the caller has given up the pointee (or the
// optional), so the payload relocates instead of being copied. No program
// reaches it yet -- every owning-move source into a generic `T | None` field
// rejects further up (an `Own[T]` parameter at `assign.field_write_shape`, a
// rebound local at `method.arg_shape`) -- but the emit picks between the two
// on the move flag, so a copy here would be silent the day one lands.
template<typename Dest, typename Src>
Dest to_opt_storage_move(Src&& s) {
    using SrcD = std::remove_cvref_t<Src>;
    if constexpr (std::is_pointer_v<SrcD>) {
        if (s != nullptr) return Dest(std::move(*s));
        return Dest{};
    } else {
        return Dest(std::forward<Src>(s));
    }
}

// Read a generic tuple slot (val_or_ptr_t<T>) as a usable value/reference:
// deref the non-value pointer case, pass value slots through. Constrained to
// lvalues (and pointers, whose deref outlives the call regardless of the
// pointer's own value category): a value-type rvalue would have its T&&
// forwarded out of the call -- a dangling reference to the dead parameter.
template<typename T>
    requires (std::is_lvalue_reference_v<T>
              || std::is_pointer_v<std::remove_cvref_t<T>>)
decltype(auto) tuple_elem_ref(T&& x) {
    using D = std::remove_cvref_t<T>;
    if constexpr (std::is_pointer_v<D>) {
        return (*x);
    } else {
        return std::forward<T>(x);
    }
}

// Build a generic slot value (tuple element or async return payload), with
// the slot type given explicitly (Dest = val_or_ptr_t<T>): borrow into a
// pointer slot by address (pass already-pointer sources through), construct/
// copy/move into a value slot. Dest must be explicit -- the source alone
// can't distinguish a `T = val_or_ref<U>` instantiation (value slot
// constructed from U&) from a plain non-value `T = U` (pointer slot taking
// &x). Forwarding reference: a source whose render is an lvalue at an
// object-typed instantiation (a borrow-returning call) may be a prvalue at a
// value-typed one, where the value slot absorbs it; sema's borrow gating
// makes a temporary at a pointer slot unreachable -- the static_assert
// documents that invariant.
template<typename Dest, typename T>
Dest to_val_or_ptr(T&& x) {
    if constexpr (std::is_pointer_v<Dest>) {
        if constexpr (std::is_pointer_v<std::remove_cvref_t<T>>) {
            return x;
        } else {
            static_assert(std::is_lvalue_reference_v<T>,
                          "borrow slot cannot take a temporary");
            return &x;
        }
    } else {
        return std::forward<T>(x);
    }
}

// The READ-ONLY form of a generic `T` parameter slot -- what a constructor's
// parameter, a `@readonly` method's and an explicit `readonly[T]` spell. Derived
// from the parameter form rather than from `T`: a mutable reference becomes
// const (so a reference type keeps exactly the `const T&` it had), and anything
// else -- a view, a const reference, a by-value scalar -- passes through
// unchanged. Spelling `const T&` directly instead would not bind at the two
// types whose parameter form is a distinct view, and there is nothing for a
// caller inside a generic body to materialize: `T` is not fixed yet.
template <typename T>
using param_val_or_cref_t = std::conditional_t<
    std::is_lvalue_reference_v<param_val_or_ref_t<T>>
        && !std::is_const_v<std::remove_reference_t<param_val_or_ref_t<T>>>,
    const std::remove_reference_t<param_val_or_ref_t<T>>&,
    param_val_or_ref_t<T>>;

// The STORAGE form of a value handed in through a generic `T` parameter slot,
// for the three body positions a borrow form cannot serve: a store into `T`
// storage, an address-of, and a by-value `T` return. Keyed on the constructions
// the C++ type admits (a view-form source is not implicitly convertible to its
// owning form), never on which container T is. A generic tuple element's
// borrow form (`val_or_ptr_t<T>`, a `T*`) copies its pointee: the source only
// lends it, so a moved element read moves at a value instantiation alone.
template <class T, class Src>
storage_t<T> param_to_storage(Src&& s) {
    using S = std::remove_cvref_t<Src>;
    if constexpr (is_own_arg_v<T>) {
        // A borrow read at `T = Own[X]`: the storage copies it, as the twin's
        // `Own[X]` parameter read into storage that is not its last use.
        return param_to_storage<storage_t<T>>(std::forward<Src>(s));
    } else if constexpr (std::is_same_v<S, T*> || std::is_same_v<S, const T*>) {
        return T(*s);
    } else if constexpr (std::is_constructible_v<T, Src&&>) {
        return T(std::forward<Src>(s));
    } else {
        return T(std::begin(s), std::end(s));
    }
}

// param_to_storage at an assignment into existing `T` storage: a source the
// storage assigns from as it is (a value, a `string_view` into
// `std::string`) is handed over unchanged, so the assignment reuses the
// storage; one it does not (a `BytesView` into `Bytes`) is built first.
// The test is on `T&` also where the target is a `std::optional<T>` payload
// slot: optional's converting assignment needs exactly that assignability.
template <class T, class Src>
decltype(auto) param_to_assign(Src&& s) {
    if constexpr (is_own_arg_v<T>) {
        return param_to_assign<storage_t<T>>(std::forward<Src>(s));
    } else if constexpr (std::is_assignable_v<T&, Src&&>) {
        return std::forward<Src>(s);
    } else {
        return param_to_storage<T>(std::forward<Src>(s));
    }
}

// The result form of a by-value `T` return from a source `Src`: a const
// source (a `readonly[T]` parameter) keeps its const at a reference `T`.
template <class T, class Src>
using param_return_t = std::conditional_t<
    std::is_const_v<std::remove_reference_t<Src>>, val_or_cref_t<T>, val_or_ref_t<T>>;

// param_to_storage at a by-value `T` return: a reference instantiation returns
// the borrow unchanged (it must NOT start copying), a value one owns.
template <class T, class Src>
param_return_t<T, Src> param_to_return(Src&& s) {
    if constexpr (std::is_reference_v<val_or_ref_t<T>>) {
        return s;
    } else {
        return param_to_storage<T>(std::forward<Src>(s));
    }
}

// A read of a generic `T` parameter at its LAST use into an owning sink: at
// `T = Own[X]` the parameter owns its value, so the sink takes it over (the
// twin's `std::move(x)`); at any other `T` the sink copies, as the twin's
// `out.push_back(x)` / `: v(v)` of a by-value parameter does.
template <class T, class Src>
storage_t<T> param_consume(Src&& s) {
    if constexpr (is_own_arg_v<T> && std::is_lvalue_reference_v<Src>
                  && !std::is_const_v<std::remove_reference_t<Src>>) {
        return storage_t<T>(std::move(s));
    } else {
        return param_to_storage<T>(std::forward<Src>(s));
    }
}

// param_to_return at the parameter's last use: at `T = Own[X]` the result
// takes the parameter's value over; at any other `T`, `param_to_return` (a
// staged or frame-field return copies, as the twin's `V __tpy_ret_0 = x;`).
// The twin's direct `return x;` moves implicitly: that one return spells
// `param_to_return<T>(param_handoff<T>(x))`.
template <class T, class Src>
param_return_t<T, Src> param_to_return_last(Src&& s) {
    if constexpr (is_own_arg_v<T> && std::is_lvalue_reference_v<Src>
                  && !std::is_const_v<std::remove_reference_t<Src>>) {
        return val_or_ref_t<T>(std::move(s));
    } else {
        return param_to_return<T>(std::forward<Src>(s));
    }
}

// An open-result local (`acc = xs[0]; acc = f(acc, x)`) read at its last use,
// into any sink: at `T = Own[X]` with a value storage type the local holds a
// snapshot in a slot of its own, which the sink takes over; at any other `T`
// it is the read as it is, which the sink copies (as the twin's `W acc` local
// is copied at its last use, BUGS.md#owned-viewfam-local-last-use-copies),
// borrows or, where every binding was an open result at `T = Own[X]`, moves.
template <class T, class Src>
decltype(auto) open_local_last(Src&& s) {
    if constexpr (is_own_arg_v<T> && is_value_type_v<storage_t<T>>
                  && std::is_move_constructible_v<storage_t<T>>
                  && std::is_lvalue_reference_v<Src>
                  && !std::is_const_v<std::remove_reference_t<Src>>) {
        return static_cast<std::remove_reference_t<Src>&&>(s);
    } else {
        return std::forward<Src>(s);
    }
}

// An open-result local dead after the read -- the direct `return acc;`, which
// the twin's local moves implicitly: at a value storage type the local's own
// slot moves out; at a reference one it is the read as it is.
template <class T, class Src>
decltype(auto) open_local_handoff(Src&& s) {
    if constexpr (is_value_type_v<storage_t<T>>
                  && std::is_move_constructible_v<storage_t<T>>
                  && std::is_lvalue_reference_v<Src>
                  && !std::is_const_v<std::remove_reference_t<Src>>) {
        return static_cast<std::remove_reference_t<Src>&&>(s);
    } else {
        return std::forward<Src>(s);
    }
}

// A read handed to an open-`T` parameter of a callee at the SAME `T`: at
// `T = Own[X]` that parameter owns (`X&&` / `X`), so a read that is not its
// last use hands over a copy (warned at the instantiating call); at any other
// `T` the read passes through unchanged, which a by-value parameter copies
// and a reference one borrows, as at the twin's parameter.
template <class T, class Src>
decltype(auto) param_pass(Src&& s) {
    if constexpr (is_own_arg_v<T>) {
        return param_to_storage<storage_t<T>>(std::forward<Src>(s));
    } else {
        return std::forward<Src>(s);
    }
}

// param_pass at the read's last use: at `T = Own[X]` the value moves on; at
// any other `T` the read passes as `param_pass` does, as the twin's `g(x)`.
template <class T, class Src>
decltype(auto) param_pass_last(Src&& s) {
    if constexpr (is_own_arg_v<T> && std::is_lvalue_reference_v<Src>
                  && !std::is_const_v<std::remove_reference_t<Src>>) {
        return static_cast<std::remove_reference_t<Src>&&>(s);
    } else {
        return param_pass<T>(std::forward<Src>(s));
    }
}

// A parameter the compiler hands on where nothing can read it again -- a
// resumable frame's constructor taking it into its field, a shim or adapter
// forwarding its own parameter, a reassigned parameter's prologue: wherever
// the slot holds a value it moves, as the twin's `std::move(x_)`; otherwise
// it passes as `param_pass` does.
template <class T, class Src>
decltype(auto) param_handoff(Src&& s) {
    if constexpr (param_holds_value_v<T> && std::is_lvalue_reference_v<Src>
                  && !std::is_const_v<std::remove_reference_t<Src>>) {
        return static_cast<std::remove_reference_t<Src>&&>(s);
    } else {
        return param_pass<T>(std::forward<Src>(s));
    }
}

// The binding of a pointer local holding an open `T` result (`acc =
// xs[0]`, `acc = func(acc, x)`), the declaration's and every rebind's: at
// a reference storage type a reference re-points the local, as the twin's
// `P*` local does, and a value lands in the local's slot, which it then
// points at, as the twin's fresh-result slot; at a value storage type
// (`int32`, `str`) the slot holds a snapshot, as the twin's `int32_t acc`.
template <class T, class Ptr, class Slot, class Src>
void reseat_result(Ptr& p, Slot& slot, Src&& s) {
    if constexpr (std::is_lvalue_reference_v<Src&&>
                  && !is_value_type_v<storage_t<T>>) {
        p = &s;
    } else {
        p = &*(slot = std::forward<Src>(s));
    }
}

template <class T, class Slot, class Src>
storage_t<T>* init_result(Slot& slot, Src&& s) {
    storage_t<T>* p = nullptr;
    reseat_result<T>(p, slot, std::forward<Src>(s));
    return p;
}

// Equality between two spellings of the same generic `T`: `==` where the two
// forms compare, element-wise where they do not (a span against its owning
// container, which the standard leaves to us).
template <class A, class B>
bool eq(const A& a, const B& b) {
    if constexpr (requires { { a == b } -> std::convertible_to<bool>; }) {
        return a == b;
    } else {
        return std::ranges::equal(a, b);
    }
}

/**
 * is_dyn_protocol_base<T> -- true iff T is a TPy @dynamic protocol abstract
 * base. Codegen emits a specialization next to each @dynamic protocol class;
 * default-false primary tolerates incomplete T (recursive type aliases).
 */
template<typename T> struct is_dyn_protocol_base : std::false_type {};

template<typename T>
inline constexpr bool is_dyn_protocol_base_v = is_dyn_protocol_base<T>::value;

/**
 * own_param_t<T> -- C++ parameter shape for a TPy Own[T] slot, picking per T
 * at template instantiation: unique_ptr<T> for abstract @dynamic bases,
 * T&& otherwise. Lazy via alias, so one template body handles all T.
 */
namespace detail {
    // A value type moves in by value -- the twin's `Own[str]` is
    // `std::string`, not `std::string&&`.
    template<typename T> struct own_param_impl {
        using type = std::conditional_t<
            is_dyn_protocol_base_v<T>,
            std::unique_ptr<T>,
            std::conditional_t<is_value_type<T>::value, T, T&&>>;
    };
    // `Own[Own[X]]` is `Own[X]`.
    template<typename X> struct own_param_impl<own_arg<X>> : own_param_impl<X> {};
}
template<typename T>
using own_param_t = typename detail::own_param_impl<T>::type;

/**
 * own_return_t<T> -- C++ return shape for a TPy Own[T] result.
 *
 * Companion to own_param_t. The param shape uses T&& for concrete T
 * (rvalue ref to a caller-owned object), but T&& can't be a return type
 * without dangling -- callees can't return a reference to their local.
 * own_return_t is the by-value return shape: T for concrete (move),
 * unique_ptr<T> for abstract @dynamic bases (ownership handle).
 */
namespace detail {
    template<typename T> struct own_return_impl {
        using type = std::conditional_t<
            is_dyn_protocol_base_v<T>, std::unique_ptr<T>, T>;
    };
    template<typename X> struct own_return_impl<own_arg<X>> : own_return_impl<X> {};

    // The owned arms of the generic slot forms, completed here because they
    // read `own_param_t` / `own_return_t`: a parameter `x: T` at
    // `T = Own[X]` is the twin's `x: Own[X]`, a result its `-> Own[X]`.
    template<typename X> struct val_or_ref_impl<own_arg<X>>  { using type = typename own_return_impl<X>::type; };
    template<typename X> struct val_or_cref_impl<own_arg<X>> { using type = typename own_return_impl<X>::type; };
    template<typename X> struct param_val_or_ref_impl<own_arg<X>> { using type = typename own_param_impl<X>::type; };
    template<typename X> struct val_or_ptr_impl<own_arg<X>>  { using type = typename own_return_impl<X>::type; };
    template<typename X> struct val_or_cptr_impl<own_arg<X>> { using type = typename own_return_impl<X>::type; };
}
template<typename T>
using own_return_t = typename detail::own_return_impl<T>::type;

/**
 * await_arg_capture_t<X> -- the type a forwarding-ref factory `f(T&& a)`
 * would deduce for `T` from an argument expression whose `decltype((arg))`
 * is X. Used to spell the sub-future field type of an inline await of a
 * coroutine with a static-protocol param, so the field type matches the
 * factory's deduction and the in-place `emplace(arg)` binds: an lvalue arg
 * (X = U&) is captured by reference (borrow); an rvalue (X = U or U&&) by
 * value (own the moved-in operand). Pass `decltype((arg))` -- the double
 * parens carry the value category.
 */
template<typename X>
using await_arg_capture_t = std::conditional_t<
    std::is_lvalue_reference_v<X>,
    X,
    std::remove_reference_t<X>
>;

/**
 * val_or_ref<T> - Wrapper for iterator __next__() returns.
 *
 * Stores value types (int, str, tuple, ...) by value, non-value types
 * (records, containers) by pointer. This lets __next__() return element
 * references through std::expected (which cannot hold T&).
 *
 * T may be const-qualified (e.g. from a const_iterator). val_or_ref<const T>
 * stores const T* and get() returns const T&.
 */
template<typename T>
struct val_or_ref {
    using is_val_or_ref_tag = void;
    static constexpr bool is_val = is_value_type<std::remove_const_t<T>>::value;
    using storage_t = std::conditional_t<is_val, std::remove_const_t<T>, T*>;
    storage_t data_;

    val_or_ref(const std::remove_const_t<T>& v) requires (is_val) : data_(v) {}
    val_or_ref(std::remove_const_t<T>&& v) requires (is_val) : data_(std::move(v)) {}
    val_or_ref(T& ref) requires (!is_val) : data_(&ref) {}
    // Borrow-pointer sources (tuple borrow slots are `T*`): same non-null
    // borrow contract as the T& ctor.
    val_or_ref(T* ptr) requires (!is_val) : data_(ptr) {}

    // (data_): return a reference to the stored copy, never a prvalue --
    // callers bind views (string_view/span) to the result, which would
    // dangle at end of statement if a temporary were returned.
    //
    // Two overloads: a mutable wrapper lends a MUTABLE element -- the pointee
    // at a reference payload, the wrapper's OWN copy at a value one -- which
    // is what a for-loop variable over the slot binds
    // (`T* x = &unwrap_ref(...)`). With only the const one, a value `T` came
    // back `const T&` while a reference `T` came back `T&` -- an asymmetry
    // invisible until a generic yield slot put both instantiations through the
    // same `val_or_ref<T>`. The non-const overload is gated on a non-const `T`:
    // the value storage drops the const (`storage_t` is `remove_const_t<T>`),
    // so without the gate a `val_or_ref<const V>` would hand out a mutable
    // `V&`.
    decltype(auto) get() requires (!std::is_const_v<T>) {
        if constexpr (is_val) return (data_);
        else return (*data_);
    }

    decltype(auto) get() const {
        if constexpr (is_val) return (data_);
        else return (*data_);
    }
};

// The iterator/yield SLOT form of T -- `val_or_ref<T>`, except where T is
// ALREADY a slot. A generic callee's type argument arrives pre-wrapped when
// sema infers it as a borrow (`Ref[X]`, from a callable parameter's declared
// return: `my_map<Point, val_or_ref<Point>>`), so a frame spelling the slot
// over its open `T` would nest one wrapper inside another. Idempotent, so the
// frame need not know which of the two forms its instantiation supplies.
namespace detail {
    template<typename T> struct yield_slot_impl { using type = val_or_ref<T>; };
    template<typename T> struct yield_slot_impl<val_or_ref<T>> { using type = val_or_ref<T>; };
    template<typename T> struct yield_slot_impl<const val_or_ref<T>> { using type = val_or_ref<const T>; };
    // An owned step (`Iterator[Own[X]]`'s slot) at `T = Own[X]`.
    template<typename X> struct yield_slot_impl<own_arg<X>> { using type = own_return_t<X>; };
}
template<typename T> using yield_slot_t = typename detail::yield_slot_impl<T>::type;

// A generator's frame-resident local at its last use, handed to an open-`T`
// yield slot: a value instantiation moves it into the slot, a reference one
// lends it -- the frame outlives the step. Taking the local by lvalue
// reference keeps a temporary out at either instantiation. A local that is
// already a slot (`T = val_or_ref<P>`, a borrow the call site deduced) lends
// its referent, so a const slot (`yield_slot_t<const T>`) takes it too; a
// const value local copies, as a const local's return does.
template<typename T, typename Src>
yield_slot_t<T> yield_from_local(Src& y) {
    if constexpr (is_own_arg_v<std::remove_const_t<T>>) {
        // An owned step hands the frame local over, as `Iterator[Own[X]]`.
        return yield_slot_t<T>(std::move(y));
    } else if constexpr (requires { typename std::remove_const_t<Src>::is_val_or_ref_tag; }) {
        if constexpr (yield_slot_t<T>::is_val) {
            return yield_slot_t<T>(std::move(y.get()));
        } else {
            return yield_slot_t<T>(y.get());
        }
    } else if constexpr (yield_slot_t<T>::is_val) {
        return yield_slot_t<T>(std::move(y));
    } else {
        return yield_slot_t<T>(y);
    }
}

} // namespace tpy
