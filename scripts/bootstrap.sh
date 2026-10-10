#!/usr/bin/env bash
# Idempotent, no sudo. Reuses ../corv32/tools when present (same pins), else downloads the pins.
set -euo pipefail
R="$(cd "$(dirname "$0")/.." && pwd)"
cd "$R"
mkdir -p tools/dl .cache
C=../corv32/tools
OSS_DATE=2026-10-03
GCC_VER=15.2.0-1
GCC_SHA=6588e8351455fad8aca37551f0e5a5543f3346bfa9a837cf03cbd3bdd4989f8f
GCC=xpack-riscv-none-elf-gcc-$GCC_VER

case "$(uname -s)-$(uname -m)" in
  Darwin-arm64) PLAT=darwin-arm64 ;;
  Linux-x86_64) PLAT=linux-x64 ;;
  *) echo "unsupported host $(uname -s)-$(uname -m)" >&2; exit 1 ;;
esac

if [ -x tools/oss-cad-suite/bin/yosys ]; then :
elif [ -x $C/oss-cad-suite/bin/yosys ]; then
  ln -sfn "$(cd $C && pwd)/oss-cad-suite" tools/oss-cad-suite; echo "reuse: tools/oss-cad-suite -> corv32"
else
  f=oss-cad-suite-$PLAT-${OSS_DATE//-/}.tgz
  curl -fsSL -o tools/dl/$f https://github.com/YosysHQ/oss-cad-suite-build/releases/download/$OSS_DATE/$f
  tar xzf tools/dl/$f -C tools
fi

if [ -x tools/$GCC/bin/riscv-none-elf-gcc ]; then :
elif [ -x $C/$GCC/bin/riscv-none-elf-gcc ]; then
  ln -sfn "$(cd $C && pwd)/$GCC" tools/$GCC; echo "reuse: tools/$GCC -> corv32"
else
  f=$GCC-$PLAT.tar.gz
  curl -fsSL -o tools/dl/$f https://github.com/xpack-dev-tools/riscv-none-elf-gcc-xpack/releases/download/v$GCC_VER/$f
  [ "$PLAT" = darwin-arm64 ] && echo "$GCC_SHA  tools/dl/$f" | shasum -a 256 -c -
  tar xzf tools/dl/$f -C tools
fi

# Python 3.12 venv. uv-managed CPython lives in tools/python (symlinked to corv32's when present).
UV="$(command -v uv || true)"; [ -z "$UV" ] && [ -x /Library/Frameworks/Python.framework/Versions/3.14/bin/uv ] && UV=/Library/Frameworks/Python.framework/Versions/3.14/bin/uv
if [ ! -x .venv/bin/python ]; then
  if [ -n "$UV" ]; then
    [ -e tools/python ] || { [ -d $C/python ] && ln -sfn "$(cd $C && pwd)/python" tools/python && echo "reuse: tools/python -> corv32"; } || true
    export UV_CACHE_DIR="$R/.cache/uv" UV_PYTHON_INSTALL_DIR="$R/tools/python"
    "$UV" venv -q --python 3.12 .venv
  else
    python3.12 -m venv .venv
  fi
fi
if [ -n "$UV" ]; then
  UV_CACHE_DIR="$R/.cache/uv" "$UV" pip install -q --python .venv/bin/python -r requirements.txt
else
  .venv/bin/python -m pip install -q -r requirements.txt
fi

[ -f third_party/mcugrad/mcugrad/quant/sim.py ] || echo "hint: mcugrad submodule missing; run: git submodule update --init"
# Datasets for the mcugrad models (MNIST, CIFAR-10): copy from a sibling mcugrad checkout when
# present; otherwise mcugrad.data downloads and hash-verifies them on first use.
D=third_party/mcugrad/data
mkdir -p "$D"
for f in train-images-idx3-ubyte.gz train-labels-idx1-ubyte.gz t10k-images-idx3-ubyte.gz \
         t10k-labels-idx1-ubyte.gz cifar-10-python.tar.gz HASHES.json; do
  if [ ! -f "$D/$f" ] && [ -f "../mcugrad/data/$f" ]; then cp "../mcugrad/data/$f" "$D/$f"; echo "reuse: $D/$f"; fi
done

echo "bootstrap complete; run: source scripts/env.sh"
