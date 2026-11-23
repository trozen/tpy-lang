#pragma once
#include <string>
#include <string_view>
inline std::string_view view_of(const std::string& s) { return s; }
inline std::string mk() { return "abc"; }
