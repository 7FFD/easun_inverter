#!/usr/bin/env bash
# Usage: bridge/build.sh [goarch] [gomips]   default: mips softfloat (matches `uname -m` = mips)
set -euo pipefail
ARCH="${1:-mips}"; GOMIPS="${2:-softfloat}"
cd "$(dirname "$0")"; mkdir -p out
CGO_ENABLED=0 GOOS=linux GOARCH="$ARCH" GOMIPS="$GOMIPS" go build -trimpath -ldflags "-s -w" -o "out/easun-bridge-$ARCH" .
ls -la "out/easun-bridge-$ARCH"
