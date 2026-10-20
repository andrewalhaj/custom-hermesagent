#!/usr/bin/env bash
# Probe which fine-grained PAT permissions a token actually has for one repo.
# Usage: pat-permission-probe.sh OWNER REPO [token-file]   (default token file: ~/.hermes/.github-pat)
# Prints one line per capability with the HTTP code and what a 403/404 means, so the
# missing permissions can be requested from the user in ONE message instead of one 403 at a time.
set -u
OWNER=${1:?owner}; REPO=${2:?repo}; TF=${3:-${HERMES_HOME:-$HOME/.hermes}/.github-pat}
H="Authorization: Bearer $(tr -d '\n' < "$TF")"
R="https://api.github.com/repos/$OWNER/$REPO"
probe() { # label url expect-note
  code=$(curl -s -o /dev/null -w '%{http_code}' -H "$H" -H "User-Agent: hermes" "$2")
  printf '%-28s %s   %s\n' "$1" "$code" "$3"
}
probe "identity (/user)"            https://api.github.com/user                 "200 = token valid"
probe "repo metadata"               "$R"                                        "200 = Metadata; 404 = repo missing or not in token's repo list"
probe "Contents (read)"             "$R/contents/"                              "200/404(empty repo) = OK; 403 = add Contents RW"
probe "Pull requests"               "$R/pulls?per_page=1"                       "403 = add Pull requests RW"
probe "Actions (runs)"              "$R/actions/runs?per_page=1"                "403 = add Actions Read"
probe "Actions (workflows list)"    "$R/actions/workflows"                      "403 = add Actions Read"
probe "Checks"                      "$R/commits/HEAD/check-runs"                "403 = add Checks Read"
probe "Administration (settings)"   "$R/actions/permissions"                    "403 = add Administration"
echo "Workflows RW cannot be probed read-only: it only shows as a push rejection when .github/workflows/* is in the push."
