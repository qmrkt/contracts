#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TOOLS_DIR="${ROOT_DIR}/.tools"
JAR_PATH="${TOOLS_DIR}/tla2tools.jar"
JAR_URL="https://github.com/tlaplus/tlaplus/releases/download/v1.8.0/tla2tools.jar"

mkdir -p "${TOOLS_DIR}"

if ! command -v java >/dev/null 2>&1; then
  echo "FAIL: Java is required to run TLC." >&2
  exit 1
fi

if [[ ! -f "${JAR_PATH}" ]]; then
  echo "Downloading tla2tools.jar into ${JAR_PATH}" >&2
  curl -fsSL "${JAR_URL}" -o "${JAR_PATH}"
fi

run_tlc() {
  local cfg="$1"
  local label="$2"
  set +e
  java -Xss32m -cp "${JAR_PATH}" tlc2.TLC -config "${cfg}" "${ROOT_DIR}/QuestionMarket.tla" -workers auto
  local s=$?
  set -e
  if [[ ${s} -eq 0 ]]; then
    echo "PASS: ${label}"
  else
    echo "FAIL: ${label} (see TLC output above)." >&2
  fi
  return "${s}"
}

status=0
run_tlc "${ROOT_DIR}/QuestionMarket.cfg" "QuestionMarket.cfg (minimal users)" || status=$?
run_tlc "${ROOT_DIR}/QuestionMarket.extended.cfg" "QuestionMarket.extended.cfg (creator + lp)" || status=$?
run_tlc "${ROOT_DIR}/QuestionMarket.multiparty.cfg" "QuestionMarket.multiparty.cfg (creator + lp + trader)" || status=$?

rm -rf "${ROOT_DIR}/states"

if [[ ${status} -eq 0 ]]; then
  echo "PASS: All TLC model checks completed without invariant or property violations."
else
  echo "FAIL: One or more TLC runs failed." >&2
fi

exit "${status}"
