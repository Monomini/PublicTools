#!/usr/bin/env bash
# mark_aliyun.sh
# Usage: mark_aliyun.sh <ip_file>
# Output: CSV to stdout: ip,matched,reason
#
# Requires: whois (and network access). Works on Linux/macOS.
# Team Cymru whois is used for ASN lookups.

set -euo pipefail

if [[ $# -ne 1 || ! -f "$1" ]]; then
  echo "Usage: $0 <ip_file>" >&2
  exit 2
fi

IP_FILE="$1"

# keywords to detect Alibaba/Aliyun in ASN/whois text (case-insensitive)
KEYWORDS_REGEX='(alibaba|aliyun|hangzhou|taobao|alibabagroup|阿里|阿里云)'

# Print CSV header
echo "ip,matched,reason"

while IFS= read -r raw || [[ -n "$raw" ]]; do
  ip="$(echo "$raw" | tr -d ' \t\r' )"
  [[ -z "$ip" ]] && continue
  # skip comments
  [[ "$ip" =~ ^# ]] && continue

  # Basic private IP check (skip RFC1918 & local)
  if [[ "$ip" =~ ^10\. ]] || [[ "$ip" =~ ^192\.168\. ]] || [[ "$ip" =~ ^172\.(1[6-9]|2[0-9]|3[0-1])\. ]] || [[ "$ip" == "127."* ]] || [[ "$ip" == "::1" ]] ; then
    echo "$ip,PRIVATE,private-range"
    continue
  fi

  matched="NO"
  reason=""

  # 1) Query Team Cymru whois for ASN and AS name (fast)
  # Query format: " -v <ip>" returns a line like:
  # AS      | IP               | BGP Prefix          | CC | Registry | Allocated  | AS Name
  cy_output="$(echo " -v $ip" | whois -h whois.cymru.com 2>/dev/null || true)"
  # drop header if present; take non-empty last line
  cy_line="$(printf "%s\n" "$cy_output" | sed '/^$/d' | tail -n +2 | tail -n1 || true)"
  if [[ -n "$cy_line" ]]; then
    # compress spaces and get ASN fields
    cy_line_clean="$(echo "$cy_line" | tr -s ' ' )"
    # AS name is after the 6th field typically; extract trailing text
    as_name="$(echo "$cy_line_clean" | cut -d' ' -f7- || true)"
    # Check for keywords in AS name
    if echo "$as_name" | grep -Eiq "$KEYWORDS_REGEX"; then
      matched="YES"
      reason="ASN: $as_name"
    fi
  fi

  # 2) If not matched yet, fallback to whois textual scan
  if [[ "$matched" == "NO" ]]; then
    whois_text="$(whois "$ip" 2>/dev/null || true)"
    # extract useful lines: org, descr, netname, org-name, inetnum, etc
    candidate="$(printf "%s\n" "$whois_text" | awk 'BEGIN{IGNORECASE=1} /org|org-name|organization|descr|netname|owner|custname|abuse-mailbox/ {print}' | tr '\n' ' ' | sed 's/  */ /g' )"
    if [[ -n "$candidate" ]]; then
      if echo "$candidate" | grep -Eiq "$KEYWORDS_REGEX"; then
        matched="YES"
        # pick first matching word/phrase as reason (show snippet)
        snippet="$(echo "$candidate" | sed -E "s/.*(${KEYWORDS_REGEX}).*/...\\1.../I" )"
        reason="whois-snippet: $snippet"
      else
        # Not matched; but keep the ASN name if available
        if [[ -n "${as_name:-}" ]]; then
          reason="ASN: ${as_name}"
        else
          # fallback to a short whois summary
          short="$(printf "%s" "$candidate" | cut -c1-120)"
          reason="whois-summary: ${short}"
        fi
      fi
    else
      # no whois info found
      if [[ -n "${as_name:-}" ]]; then
        reason="ASN: ${as_name}"
      else
        reason="no-whois"
      fi
    fi
  fi

  echo "$ip,$matched,\"${reason//\"/\"\"}\""

done < "$IP_FILE"
