#!/usr/bin/env bash
# resolve_domains.sh
# Usage: resolve_domains.sh <file-with-domains>
# Each line: a domain (comments starting with # are allowed)

set -Eeuo pipefail

if [[ $# -ne 1 || ! -f "$1" ]]; then
  echo "Usage: $0 <domains_file>" >&2
  exit 1
fi

DOMAINS_FILE="$1"

while IFS= read -r line; do
  # strip comments and trim
  domain="${line%%#*}"
  domain="$(echo "$domain" | xargs)"
  [[ -z "$domain" ]] && continue

  # Run nslookup for A records; ignore the resolver's own Address line (contains '#53')
  out="$(nslookup -type=A "$domain" 2>/dev/null || true)"
  ips="$(printf '%s\n' "$out" | awk '/^Address: / && $2 !~ /#/{print $2}')"

  if [[ -z "$ips" ]]; then
    echo "$domain,NO_ANSWER"
  else
    while IFS= read -r ip; do
      [[ -n "$ip" ]] && echo "$domain,$ip"
    done <<< "$ips"
  fi
done < "$DOMAINS_FILE"
