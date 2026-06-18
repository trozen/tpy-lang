// Bodies for tpy::stdlib::os (declared in tpy/stdlib/os.hpp). Kept out of the
// header so <filesystem> and <sys/stat.h> are not pulled into every TU that
// calls into os; failed operations map to the matching TPy exception.

#include <tpy/stdlib/os.hpp>

#include <cerrno>
#include <cstdlib>
#include <filesystem>
#include <string>
#include <string_view>
#include <system_error>
#include <vector>

#include <sys/stat.h>

#include <tpy/core.hpp>

namespace tpy::stdlib::os {

namespace {

std::filesystem::path to_path(std::string_view p) {
    // A TPy str view is not guaranteed null-terminated; copy into a string.
    return std::filesystem::path(std::string(p));
}

[[noreturn]] void raise_fs_error(const std::error_code& ec,
                                 std::string_view op, std::string_view path) {
    // Partial errno->subclass mapping (M2 completes it): CPython also maps
    // ENOTDIR->NotADirectoryError and EACCES/EPERM->PermissionError, but the
    // former class does not exist in TPy yet, so the whole table is deferred
    // rather than mapped asymmetrically. These currently surface as OSError.
    if (ec == std::errc::no_such_file_or_directory) {
        ::tpy::raise_file_not_found_error("{}: '{}': {}", op, path, ec.message());
    }
    ::tpy::raise_os_error("{}: '{}': {}", op, path, ec.message());
}

} // namespace

std::string getcwd() {
    std::error_code ec;
    auto p = std::filesystem::current_path(ec);
    if (ec) ::tpy::raise_os_error("getcwd: {}", ec.message());
    return p.string();
}

void chdir(std::string_view path) {
    std::error_code ec;
    std::filesystem::current_path(to_path(path), ec);
    if (ec) raise_fs_error(ec, "chdir", path);
}

std::vector<std::string> listdir(std::string_view path) {
    // Drive the iterator with the non-throwing increment(ec) overload: the
    // range-for form calls the throwing operator++, whose filesystem_error
    // is not a TPy exception and would escape the FFI boundary uncaught.
    std::error_code ec;
    std::filesystem::directory_iterator it(to_path(path), ec);
    if (ec) raise_fs_error(ec, "listdir", path);
    const std::filesystem::directory_iterator end;
    std::vector<std::string> names;
    while (it != end) {
        names.push_back(it->path().filename().string());
        it.increment(ec);
        if (ec) raise_fs_error(ec, "listdir", path);
    }
    return names;
}

bool path_exists(std::string_view path) {
    std::error_code ec;
    return std::filesystem::exists(to_path(path), ec);
}

bool path_lexists(std::string_view path) {
    // symlink_status does not follow the link, so a broken symlink still
    // reports as existing -- which is exactly os.path.lexists.
    std::error_code ec;
    return std::filesystem::exists(std::filesystem::symlink_status(to_path(path), ec));
}

bool path_isfile(std::string_view path) {
    std::error_code ec;
    return std::filesystem::is_regular_file(to_path(path), ec);
}

bool path_isdir(std::string_view path) {
    std::error_code ec;
    return std::filesystem::is_directory(to_path(path), ec);
}

bool path_islink(std::string_view path) {
    std::error_code ec;
    return std::filesystem::is_symlink(to_path(path), ec);
}

int64_t path_getsize(std::string_view path) {
    // os.path.getsize is os.stat(path).st_size: it follows symlinks and reports
    // a size for any path type (a directory's st_size, not an error), unlike
    // std::filesystem::file_size which is defined only for regular files.
    // st_size is a signed off_t, so no overflow on the cast.
    struct ::stat st;
    if (::stat(std::string(path).c_str(), &st) != 0) {
        // Capture errno before constructing the error_code: the arguments to
        // error_code(...) have unsequenced evaluation, so reading errno inline
        // could race a side effect in the other argument.
        int err = errno;
        raise_fs_error(std::error_code(err, std::generic_category()),
                       "getsize", path);
    }
    return static_cast<int64_t>(st.st_size);
}

bool env_has(std::string_view key) {
    return std::getenv(std::string(key).c_str()) != nullptr;
}

std::string env_get(std::string_view key) {
    const char* v = std::getenv(std::string(key).c_str());
    return v ? std::string(v) : std::string();
}

} // namespace tpy::stdlib::os
