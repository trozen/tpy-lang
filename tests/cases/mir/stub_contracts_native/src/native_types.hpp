#pragma once
#include <cstddef>
#include <cstdint>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

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

// An element-owning container: `put` replaces an element in place, `push`
// may reallocate and move every element.
template <class T>
struct ProbeRing {
    std::vector<T> items;
    auto begin() { return items.begin(); }
    auto end() { return items.end(); }
    auto begin() const { return items.begin(); }
    auto end() const { return items.end(); }
    std::size_t size() const { return items.size(); }
    // A subscript renders through the runtime's `tpy::__getitem__`, which calls this member.
    T& __getitem__(int32_t i) { return items[static_cast<std::size_t>(i)]; }
    const T& __getitem__(int32_t i) const { return items[static_cast<std::size_t>(i)]; }
    void put(int32_t i, T v) { items[static_cast<std::size_t>(i)] = std::move(v); }
    void push(T v) { items.push_back(std::move(v)); }
};
