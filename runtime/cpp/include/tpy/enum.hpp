/**
 * TurboPython Runtime - Enum Utilities
 *
 * Primary template for EnumUtil<T>. Each TurboPython enum generates
 * an explicit specialization with type_name, str_is_value, name(),
 * members, from_value(), and try_parse() in the module's generated code.
 */

#pragma once

#include <charconv>
#include <ostream>
#include <span>
#include <string_view>
#include <string>
#include <type_traits>
#include <utility>

namespace tpy {

template<typename T>
struct EnumUtil;

namespace detail {

// An enum TPy generated an EnumUtil<T> specialization for (every TPy enum,
// @native ones included).
template<typename T>
concept util_enum = std::is_enum_v<T> && requires(T x) { ::tpy::EnumUtil<T>::name(x); };

// The two sinks enum text is written to: a stream (print) and a string.
inline void enum_text_append(std::ostream& os, std::string_view s) {
    os.write(s.data(), static_cast<std::streamsize>(s.size()));
}
inline void enum_text_append(std::string& out, std::string_view s) {
    out += s;
}

// The member's value in decimal, as Python writes the int: independent of
// the sink's locale and flags, and an 8-bit underlying value is a number,
// not a char.
template<util_enum T>
inline std::string_view enum_value_chars(T x, std::span<char, 24> buf) {
    auto r = std::to_chars(buf.data(), buf.data() + buf.size(), +std::to_underlying(x));
    return std::string_view(buf.data(), static_cast<std::size_t>(r.ptr - buf.data()));
}

// Python's str(): `TypeName.MEMBER`, or the value for an IntEnum (CPython's
// ReprEnum, whose str() and format() are the int's).
template<typename Sink, util_enum T>
inline void put_enum_str(Sink& sink, T x) {
    if constexpr (::tpy::EnumUtil<T>::str_is_value) {
        char buf[24];
        enum_text_append(sink, enum_value_chars(x, buf));
    } else {
        enum_text_append(sink, ::tpy::EnumUtil<T>::type_name);
        enum_text_append(sink, ".");
        enum_text_append(sink, ::tpy::EnumUtil<T>::name(x));
    }
}

// Python's repr(), the same for every flavor: `<TypeName.MEMBER: value>`.
template<typename Sink, util_enum T>
inline void put_enum_repr(Sink& sink, T x) {
    char buf[24];
    enum_text_append(sink, "<");
    enum_text_append(sink, ::tpy::EnumUtil<T>::type_name);
    enum_text_append(sink, ".");
    enum_text_append(sink, ::tpy::EnumUtil<T>::name(x));
    enum_text_append(sink, ": ");
    enum_text_append(sink, enum_value_chars(x, buf));
    enum_text_append(sink, ">");
}

template<util_enum T>
inline std::ostream& write_enum_str(std::ostream& os, T x) {
    put_enum_str(os, x);
    return os;
}

template<util_enum T>
inline std::string enum_str_string(T x) {
    std::string out;
    put_enum_str(out, x);
    return out;
}

template<util_enum T>
inline std::ostream& write_enum_repr(std::ostream& os, T x) {
    put_enum_repr(os, x);
    return os;
}

template<util_enum T>
inline std::string enum_repr_string(T x) {
    std::string out;
    put_enum_repr(out, x);
    return out;
}

} // namespace detail
} // namespace tpy
