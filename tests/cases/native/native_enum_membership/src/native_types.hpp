#pragma once
#include <vector>
#include <algorithm>
enum class NColor { Red, Green };
template <class T> struct Bag { std::vector<T> v; };
template <class T> bool bag_has(const Bag<T>& b, const T& x) { return std::find(b.v.begin(), b.v.end(), x) != b.v.end(); }
struct Owner { Bag<NColor> colors; };
inline Owner g_owner{{{NColor::Red}}};
inline Owner& get_owner() { return g_owner; }
