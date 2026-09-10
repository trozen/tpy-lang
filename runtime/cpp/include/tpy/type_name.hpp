/**
 * TurboPython Runtime - User-facing type names
 *
 * tpy::type_name<T>() returns the TPy-level name of a C++ type
 * ("int" for tpy::BigInt, "str" for std::string, "Int32" for int32_t,
 * "module.Cls" for user records via their `__tpy_class_name__` member,
 * etc.). Used for diagnostics that surface to TPy users (Any cast errors,
 * etc.) so messages don't leak C++ implementation details.
 *
 * Falls back to the demangled typeid for types without a specialization;
 * the demangler output is normalized to be stable across libc++/libstdc++.
 *
 * Depends on: core.hpp (demangle_type_name), all type headers it
 * specializes for. Include via tpy.hpp after the type headers.
 */

#pragma once

#include <cstddef>
#include <cstdint>
#include <string>
#include <string_view>
#include <typeinfo>
#include <variant>

#include "buffer_types.hpp"
#include "core.hpp"

namespace tpy {

class BigInt;

// Primary template: derives a name for any T not covered by an explicit
// specialization below. Splits on whether T carries the codegen-emitted
// `__tpy_class_name__` member (user records / dataclasses) so we don't
// have to enumerate generated types.
template <typename T>
struct type_name_for {
    static std::string get() {
        if constexpr (requires { T::__tpy_class_name__; }) {
            return std::string(T::__tpy_class_name__);
        } else {
            return demangle_type_name(typeid(T).name());
        }
    }
};

template <typename T>
inline std::string type_name() {
    return type_name_for<T>::get();
}

// Built-in TPy <-> C++ type pairings. Keep in sync with the type table in
// CLAUDE.md / docs/LANGUAGE_FEATURES.md.
#define TPY_TYPE_NAME_(CPP_TYPE, TPY_NAME)                       \
    template <> struct type_name_for<CPP_TYPE> {                 \
        static std::string get() { return TPY_NAME; }            \
    }

TPY_TYPE_NAME_(BigInt,          "int");
TPY_TYPE_NAME_(bool,            "bool");
TPY_TYPE_NAME_(char,            "Char");
TPY_TYPE_NAME_(int8_t,          "Int8");
TPY_TYPE_NAME_(int16_t,         "Int16");
TPY_TYPE_NAME_(int32_t,         "Int32");
TPY_TYPE_NAME_(int64_t,         "Int64");
TPY_TYPE_NAME_(uint8_t,         "UInt8");
TPY_TYPE_NAME_(uint16_t,        "UInt16");
TPY_TYPE_NAME_(uint32_t,        "UInt32");
TPY_TYPE_NAME_(uint64_t,        "UInt64");
TPY_TYPE_NAME_(float,           "Float32");
TPY_TYPE_NAME_(double,          "Float64");
TPY_TYPE_NAME_(std::string,     "str");
TPY_TYPE_NAME_(std::string_view, "StrView");
TPY_TYPE_NAME_(String,          "String");
TPY_TYPE_NAME_(Bytes,           "bytes");
TPY_TYPE_NAME_(ByteArray,       "bytearray");
TPY_TYPE_NAME_(std::nullptr_t,  "None");
TPY_TYPE_NAME_(std::monostate,  "None");

#undef TPY_TYPE_NAME_

}  // namespace tpy
