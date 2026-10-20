# Source me: . "${HERMES_HOME:-$HOME/.hermes}/scripts/lib/hermes_env.sh"
# Looks up config from the environment, then $HERMES_HOME/.env (parsed, never sourced).
export HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"

# hermes_env KEY [DEFAULT]
hermes_env() {
  local key="$1" val line
  val="${!key-}"
  if [ -z "$val" ] && [ -r "$HERMES_HOME/.env" ]; then
    line="$(grep -E "^[[:space:]]*(export[[:space:]]+)?${key}=" "$HERMES_HOME/.env" | tail -n 1 || true)"
    if [ -n "$line" ]; then
      val="${line#*=}"
      val="${val%$'\r'}"
      case "$val" in
        \"*\") val="${val#\"}"; val="${val%\"}" ;;
        \'*\') val="${val#\'}"; val="${val%\'}" ;;
      esac
    fi
  fi
  printf '%s\n' "${val:-${2-}}"
}

# hermes_require KEY  (use: X="$(hermes_require X)" || exit 1)
hermes_require() {
  local val
  val="$(hermes_env "$1")"
  if [ -z "$val" ]; then
    echo "$1 is not set: export it or add $1=... to $HERMES_HOME/.env" >&2
    return 1
  fi
  printf '%s\n' "$val"
}
