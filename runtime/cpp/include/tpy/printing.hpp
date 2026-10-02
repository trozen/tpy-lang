/**
 * TurboPython Runtime - Collection Printing
 *
 * Python-style printing for containers: [a, b, c]
 */

#pragma once

#include <array>
#include <cstddef>
#include <cstdint>
#include <iostream>
#include <optional>
#include <ranges>
#include <span>
#include <sstream>
#include <string>
#include <string_view>
#include <tuple>
#include <type_traits>
#include <variant>
#include <vector>

#include "bigint.hpp"
#include "buffer_types.hpp"
#include "dunder.hpp"
#include "format.hpp"

namespace tpy {

// Forward declarations for nested container printing
template<class... Ts> struct Union;
template<typename T> class ordered_set;
template<typename K, typename V> class ordered_map;

// --- Collection printing (Python-style: [a, b, c]) ---

template <typename T>
struct ListPrinter {
    const T& value;
    explicit ListPrinter(const T& v) : value(v) {}
};

namespace detail {

// Forward declare for recursive nested container support
template <typename Iter>
void print_list_contents(std::ostream& os, Iter begin, Iter end);

// Forward declare tuple overload so nested containers (e.g. list[tuple]) resolve correctly
template <typename... Ts>
void print_element(std::ostream& os, const std::tuple<Ts...>& t);

// Forward declare ordered_set overload (defined in set_ops.hpp)
template <typename T>
void print_element(std::ostream& os, const ordered_set<T>& elem);

// Forward declare ordered_map overload (defined in dict_ops.hpp)
template <typename K, typename V>
void print_element(std::ostream& os, const ordered_map<K, V>& elem);

// Forward declare Any overload (defined in any.hpp). The forward decl
// must precede the generic `print_element<T>` template so two-phase
// lookup picks the non-template Any overload at template-definition
// time even when any.hpp is included after dict_ops.hpp / set_ops.hpp.
}  // namespace detail
struct Any;
namespace detail {
void print_element(std::ostream& os, const ::tpy::Any& a);

// The value-union row, for the same two-phase-lookup reason as Any above:
// `print_element`'s generic below is UNCONSTRAINED, so a `tpy::Union` (which
// derives from std::variant rather than being one) matches it exactly and
// routes to repr_of, which has no std::vector overload -- a build failure for
// any union with a container alternative. The recursion inside
// `print_list_contents` is an unqualified call from tpy::detail, and ADL on
// `tpy::Union` associates tpy, not tpy::detail, so this row must be declared
// HERE and before the generic.
template <typename... Ts>
void print_element(std::ostream& os, const ::tpy::Union<Ts...>& elem);

// Container element printing goes through tpy::repr_of so containers emit
// the repr form (Python: `print([rec])` uses __repr__, not __str__).
// Specific overloads above cover bool/double/string/BigInt; this generic
// is the fallback for user records and other types.
template <typename T>
void print_element(std::ostream& os, const T& elem) {
    os << ::tpy::repr_of(elem);
}

// A `T*` element is a tuple / Optional borrow slot, not a raw address to
// print: borrow-form tuple members and pointer-repr Optionals both spell `T*`,
// and value-level ops (print / repr) want the referent. A null slot is a
// pointer-repr Optional's None. (char* is excluded so C-string-shaped elements,
// if any, keep the string overload path.) Both `T*` and `const T*` are needed:
// a `T*` element binds to the generic `const T&` (T=`U*`) by identity, which
// would out-rank a `const T*` qualification conversion -- so the non-const
// pointer overload must exist to win for non-const borrow slots.
template <typename T>
    requires (!std::is_same_v<std::remove_cv_t<T>, char>)
void print_element(std::ostream& os, T* elem) {
    if (elem == nullptr) {
        os << "None";
    } else {
        print_element(os, *elem);
    }
}

template <typename T>
    requires (!std::is_same_v<std::remove_cv_t<T>, char>)
void print_element(std::ostream& os, const T* elem) {
    if (elem == nullptr) {
        os << "None";
    } else {
        print_element(os, *elem);
    }
}

inline void print_element(std::ostream& os, bool elem) {
    os << (elem ? "True" : "False");
}

inline void print_element(std::ostream& os, double elem) {
    os << format_float(elem);
}

inline void print_element(std::ostream& os, const std::string& elem) {
    os << repr_quote_string(elem);
}

inline void print_element(std::ostream& os, std::string_view elem) {
    os << repr_quote_string(elem);
}

// A `String` element: the generic template below is an exact match and would
// print through `operator<<` (a raw, unquoted string) rather than repr it.
inline void print_element(std::ostream& os, const String& elem) {
    os << repr_quote_string(elem);
}

inline void print_element(std::ostream& os, const BigInt& elem) {
    os << elem.to_string();
}

// std::vector<bool> uses proxy refs so the bool overload won't match via iterators
inline void print_element(std::ostream& os, const std::vector<bool>& elem) {
    os << '[';
    for (std::size_t i = 0; i < elem.size(); ++i) {
        if (i > 0) os << ", ";
        print_element(os, static_cast<bool>(elem[i]));
    }
    os << ']';
}

// Forward declarations for variant/optional (defined after all other overloads)
template <typename... Ts>
void print_element(std::ostream& os, const std::variant<Ts...>& elem);
template <typename T>
void print_element(std::ostream& os, const std::optional<T>& elem);
inline void print_element(std::ostream& os, std::monostate);

// Overloads for nested containers
template <typename T>
void print_element(std::ostream& os, const std::vector<T>& elem) {
    print_list_contents(os, elem.begin(), elem.end());
}

template <typename T, std::size_t N>
void print_element(std::ostream& os, const std::array<T, N>& elem) {
    print_list_contents(os, elem.begin(), elem.end());
}

template <typename Iter>
void print_list_contents(std::ostream& os, Iter begin, Iter end) {
    os << '[';
    bool first = true;
    for (auto it = begin; it != end; ++it) {
        if (!first) os << ", ";
        first = false;
        print_element(os, *it);
    }
    os << ']';
}

// A Union prints as its active alternative, found by the same index dispatch
// its comparisons use (value_compare.hpp). One row serves both packs with no
// branch: a BORROW pack's leaf is a pointer, which lands on the `T*` row
// above and prints the referent (or "None" for a null slot), so the borrow
// form needs no body of its own. a visit -- even over one operand --
// calls through a table of function pointers the optimiser is not obliged to
// fold, and this chain is plain `if` tests with a direct call at each leaf. An
// operand that is valueless by exception matches no index and falls off the
// end of the chain, which throws `std::bad_variant_access`.
template <std::size_t I, typename... Ts>
void print_union_alternative(std::ostream& os, const ::tpy::Union<Ts...>& elem) {
    if constexpr (I == sizeof...(Ts)) {
        throw std::bad_variant_access{};
    } else {
        if (elem.index() == I) {
            print_element(os, *std::get_if<I>(&elem));
            return;
        }
        print_union_alternative<I + 1>(os, elem);
    }
}

template <typename... Ts>
void print_element(std::ostream& os, const ::tpy::Union<Ts...>& elem) {
    print_union_alternative<0>(os, elem);
}

// Definitions for variant/optional/monostate (after all other print_element
// overloads so std::visit can find container overloads during instantiation).
template <typename... Ts>
void print_element(std::ostream& os, const std::variant<Ts...>& elem) {
    std::visit([&os](const auto& v) { print_element(os, v); }, elem);
}

inline void print_element(std::ostream& os, std::monostate) {
    os << "None";
}

template <typename T>
void print_element(std::ostream& os, const std::optional<T>& elem) {
    if (elem.has_value()) {
        print_element(os, *elem);
    } else {
        os << "None";
    }
}

} // namespace detail

template <typename T>
std::ostream& operator<<(std::ostream& os, const ListPrinter<std::vector<T>>& p) {
    detail::print_list_contents(os, p.value.begin(), p.value.end());
    return os;
}

// std::vector<bool> uses proxy refs, so the generic iterator path won't pick up
// the bool overload of print_element. Handle it explicitly.
inline std::ostream& operator<<(std::ostream& os, const ListPrinter<std::vector<bool>>& p) {
    os << '[';
    for (std::size_t i = 0; i < p.value.size(); ++i) {
        if (i > 0) os << ", ";
        detail::print_element(os, static_cast<bool>(p.value[i]));
    }
    os << ']';
    return os;
}

template <typename T, std::size_t N>
std::ostream& operator<<(std::ostream& os, const ListPrinter<std::array<T, N>>& p) {
    detail::print_list_contents(os, p.value.begin(), p.value.end());
    return os;
}

template <typename T>
std::ostream& operator<<(std::ostream& os, const ListPrinter<std::span<T>>& p) {
    detail::print_list_contents(os, p.value.begin(), p.value.end());
    return os;
}

// Generic fallback for any iterable container (e.g. repeat_range)
template <typename C>
    requires std::ranges::input_range<C>
             && (!requires { typename std::tuple_size<C>::type; })  // exclude array
std::ostream& operator<<(std::ostream& os, const ListPrinter<C>& p) {
    detail::print_list_contents(os, std::ranges::begin(p.value), std::ranges::end(p.value));
    return os;
}

// Prints a *args body view (tpy::varargs<T>) CPython-tuple-style: `(a, b, c)`,
// `(a,)` for a single element (the singleton trailing comma), `()` for empty --
// matching how a `*args` tuple reprs under CPython. Works for both varargs
// storage modes (begin/end/size dispatch internally).
template <typename V>
struct VarargsPrinter {
    const V& value;
    explicit VarargsPrinter(const V& v) : value(v) {}
};

template <typename V>
std::ostream& operator<<(std::ostream& os, const VarargsPrinter<V>& p) {
    os << '(';
    bool first = true;
    for (auto it = p.value.begin(); it != p.value.end(); ++it) {
        if (!first) os << ", ";
        first = false;
        detail::print_element(os, *it);
    }
    if (p.value.size() == 1) os << ',';
    os << ')';
    return os;
}

// --- Generic value printing for type parameters ---

/**
 * ValuePrinter<T> - Prints values handling both scalars and containers.
 *
 * Used for generic type parameters where T might be a value type (int32_t)
 * or a container type (std::vector). Uses ListPrinter for ranges.
 */
template<typename T>
struct ValuePrinter {
    const T& value;
    explicit ValuePrinter(const T& v) : value(v) {}
};

template<typename T>
std::ostream& operator<<(std::ostream& os, const ValuePrinter<T>& p) {
    // String types are ranges but should print as strings, not char lists
    if constexpr (std::is_same_v<T, std::string_view> ||
                  std::is_same_v<T, std::string> ||
                  std::is_same_v<T, String> ||
                  std::is_same_v<T, const char*>) {
        return os << p.value;
    } else if constexpr (std::is_same_v<T, bool>) {
        return os << (p.value ? "True" : "False");
    } else if constexpr (std::is_same_v<T, double>) {
        return os << format_float(p.value);
    } else if constexpr (std::ranges::range<T>) {
        return os << ListPrinter(p.value);
    } else {
        return os << p.value;
    }
}

// --- Tuple printing (Python-style: (a, b) or (a,) for single-element) ---

namespace detail {

template <typename Tuple, std::size_t... Is>
void print_tuple_elements(std::ostream& os, const Tuple& t, std::index_sequence<Is...>) {
    ((Is == 0 ? (void)(os) : (void)(os << ", "),
      print_element(os, std::get<Is>(t))), ...);
}

template <typename... Ts>
void print_element(std::ostream& os, const std::tuple<Ts...>& t) {
    os << '(';
    print_tuple_elements(os, t, std::index_sequence_for<Ts...>{});
    if constexpr (sizeof...(Ts) == 1) {
        os << ',';
    }
    os << ')';
}

} // namespace detail

template <typename... Ts>
struct TuplePrinter {
    const std::tuple<Ts...>& value;
    explicit TuplePrinter(const std::tuple<Ts...>& v) : value(v) {}
};

// Deduction guide
template <typename... Ts>
TuplePrinter(const std::tuple<Ts...>&) -> TuplePrinter<Ts...>;

template <typename... Ts>
std::ostream& operator<<(std::ostream& os, const TuplePrinter<Ts...>& p) {
    os << '(';
    detail::print_tuple_elements(os, p.value, std::index_sequence_for<Ts...>{});
    if constexpr (sizeof...(Ts) == 1) {
        os << ',';
    }
    os << ')';
    return os;
}

// --- to_str helpers for str()/repr()/f-string on containers ---

template <typename T>
std::string list_to_str(const T& c) {
    std::ostringstream oss;
    oss << ListPrinter(c);
    return oss.str();
}

template <typename... Ts>
std::string tuple_to_str(const std::tuple<Ts...>& t) {
    std::ostringstream oss;
    oss << TuplePrinter(t);
    return oss.str();
}

// --- __str__ / __repr__ overloads for std::tuple ---

template <typename... Ts>
std::string __str__(const std::tuple<Ts...>& t) {
    return tuple_to_str(t);
}

template <typename... Ts>
std::string __repr__(const std::tuple<Ts...>& t) {
    return tuple_to_str(t);
}

// --- print(*xs): items whose count is known only at run time ---

// The default element writer of a `*xs` segment: the element's own
// operator<<, as a positional arg of that type streams.
struct PrintRaw {
    template <typename T>
    void operator()(std::ostream& os, const T& elem) const {
        os << elem;
    }
};

// One `*xs` segment of a print chain. The sequence is borrowed, never
// copied: a temporary source (`print(*list(g))`) lives until the end of the
// print statement, the full-expression this segment belongs to.
template <typename R, typename F = PrintRaw>
struct PrintEach {
    const R& items;
    F write;
    explicit PrintEach(const R& r, F f = F{}) : items(r), write(f) {}
};

struct PrintJoinEnd {};
inline constexpr PrintJoinEnd print_join_end{};

// A print chain holding a `*xs` segment: `wrote_` spans every segment, so a
// separator precedes an item exactly when something was written before it
// (`print("a", *xs, "b")` with `xs` empty writes `a b`). `print_join_end` hands the stream
// back for the end string, the flush and the signal check point.
class PrintJoin {
    std::ostream& os_;
    std::string_view sep_;
    bool wrote_ = false;

    void before_item() {
        if (wrote_) os_ << sep_;
        wrote_ = true;
    }

public:
    PrintJoin(std::ostream& os, std::string_view sep) : os_(os), sep_(sep) {}
    // A `file=` sink adapter is a temporary stream; it lives to the end of
    // the print statement like the join itself.
    PrintJoin(std::ostream&& os, std::string_view sep) : os_(os), sep_(sep) {}

    template <typename T>
    PrintJoin& operator<<(const T& item) {
        before_item();
        os_ << item;
        return *this;
    }

    template <typename R, typename F>
    PrintJoin& operator<<(const PrintEach<R, F>& seg) {
        for (const auto& elem : seg.items) {
            before_item();
            seg.write(os_, elem);
        }
        return *this;
    }

    std::ostream& operator<<(PrintJoinEnd) { return os_; }
};

} // namespace tpy
