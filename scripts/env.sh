# Source me: puts project-local toolchains on PATH. No system installs needed.
_R="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")/.." && pwd)"
export MCUACCEL_ROOT="$_R"
# macOS: call CommandLineTools clang directly (xcrun shims are slow/sandbox-hostile).
if [ "$(uname -s)" = Darwin ] && [ -x /Library/Developer/CommandLineTools/usr/bin/clang++ ]; then
  export PATH="/Library/Developer/CommandLineTools/usr/bin:$PATH"
  export SDKROOT=/Library/Developer/CommandLineTools/SDKs/MacOSX.sdk
  export CC=/Library/Developer/CommandLineTools/usr/bin/clang CXX=/Library/Developer/CommandLineTools/usr/bin/clang++
fi
export PATH="$_R/.venv/bin:$_R/tools/xpack-riscv-none-elf-gcc-15.2.0-1/bin:$_R/tools/oss-cad-suite/bin:$PATH"
export XDG_CONFIG_HOME="$_R/.cache/config" XDG_CACHE_HOME="$_R/.cache"
export PYTHONPATH="$_R/third_party/mcugrad:$_R/backend:$_R/model:${PYTHONPATH:-}"
