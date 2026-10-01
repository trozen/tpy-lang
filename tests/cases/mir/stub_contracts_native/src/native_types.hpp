#pragma once
#include <string>
#include <string_view>

inline std::string_view probe_view(std::string_view s) { return s.substr(1); }

inline double probe_plain(double x) { return x * 2; }

inline double probe_tick() { return 0.25; }

// The protocol parameter is monomorphized per argument type.
template <class T>
std::string probe_text(const T& x) { return std::to_string(x); }

inline void probe_fill(const ::tpy::String&) {}

// A template because this header is included before the generated record is declared.
template <class R>
void probe_bump(R& r) { r.n += 1; }
