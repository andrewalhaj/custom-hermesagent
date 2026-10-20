#!/usr/bin/env python3
"""Linear cron reports: digest (daily 07:00), stale (daily 09:00), weekly (Mon 09:00).
Cron's `script` field takes no arguments, so linear-daily-digest.py, stale-linear-issues.py and
linear_weekly_summary.py are one-line entry points into main(<report>). Stdout is the message.
Any API or GraphQL error exits non-zero so cron records a failure instead of a quiet day.
"""
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

API_URL = "https://api.linear.app/graphql"
TEAM_ID = "38a0c106-e9a8-4f65-84d2-ec8bdc61855d"
STALE_DAYS = 14
QUERY = """query($filter: IssueFilter, $after: String) {
  issues(filter: $filter, first: 100, after: $after, orderBy: updatedAt) {
    nodes { %s }
    pageInfo { hasNextPage endCursor }
  }
}"""


def gql(query, variables):
    key = os.environ.get("LINEAR_API_KEY", "").strip()
    if not key:
        sys.exit("LINEAR_API_KEY not set")
    req = urllib.request.Request(
        API_URL, data=json.dumps({"query": query, "variables": variables}).encode(),
        headers={"Authorization": key, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read())
    except (urllib.error.URLError, ValueError) as e:  # HTTPError is a URLError
        sys.exit(f"Linear API call failed: {e}")
    if body.get("errors") or not body.get("data"):
        sys.exit(f"Linear GraphQL errors: {body.get('errors')}")
    return body["data"]


def issues(filter_, fields):
    """Every issue matching filter_, following pages."""
    nodes, after = [], None
    while True:
        page = gql(QUERY % fields, {"filter": filter_, "after": after})["issues"]
        nodes += page["nodes"]
        if not page["pageInfo"]["hasNextPage"]:
            return nodes
        after = page["pageInfo"]["endCursor"]


def _dt(iso):
    return datetime.fromisoformat(iso.replace("Z", "+00:00"))


def _age(iso):
    try:
        return (datetime.now(timezone.utc) - _dt(iso)).days
    except (AttributeError, ValueError, TypeError):
        return 0


def _section(out, title, items, cap):
    if items:
        out.append(title)
        out += items[:cap]
        if len(items) > cap:
            out.append(f"  _...and {len(items) - cap} more_")
        out.append("")


def digest():
    rows = issues({"state": {"type": {"neq": "completed"}}, "canceledAt": {"null": True}},
                  "identifier title priority updatedAt assignee { name } state { name }")
    if not rows:
        print("☀️ **Good morning** — no open issues today. 🎉")
        return
    now = datetime.now(timezone.utc)
    urgent, stale, normal = [], [], []
    for i in rows:
        ident, title = i.get("identifier", "???"), i.get("title", "Untitled")
        who = (i.get("assignee") or {}).get("name") or "unassigned"
        age = _age(i.get("updatedAt"))
        if i.get("priority") == 1:  # 1=Urgent only; 0=No Priority is not urgent
            when = "today" if age == 0 else f"{age}d" if age < 30 else f"{age // 30}mo"
            urgent.append(f"🔴 {ident} {title} _({when}, {who})_")
        elif age > 7:
            stale.append(f"⚠️ {ident} {title} _({age}d, {who})_")
        else:
            normal.append(f"📋 {ident} {title} _({who})_")
    out = [f"☀️ **Good morning** — {len(rows)} open issues  _{now.strftime('%A, %B %-d')}_",
           f"🔴 {len(urgent)} urgent · ⚠️ {len(stale)} stale · 📋 {len(normal)} normal", ""]
    _section(out, "**🔴 Urgent**", urgent, len(urgent))
    _section(out, "**⚠️ Stale**", stale, 5)
    _section(out, "**📋 Open**", normal, 7)
    out += ["---", f"_{now.strftime('%H:%M UTC')} · Linear Digest_"]
    print("\n".join(out))


def stale():
    cutoff = (datetime.now(timezone.utc) - timedelta(days=STALE_DAYS)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    rows = issues({"team": {"id": {"eq": TEAM_ID}}, "updatedAt": {"lt": cutoff},
                   "state": {"type": {"nin": ["completed", "canceled"]}}},
                  "identifier title updatedAt url assignee { name }")
    if not rows:
        return  # silent: nothing stale
    rows.sort(key=lambda i: i.get("updatedAt", ""))
    out = [f"⚠️ {len(rows)} stale issue{'s' if len(rows) != 1 else ''}:"]
    for i in rows:
        who = f" ({i['assignee']['name']})" if i.get("assignee") else ""
        out += [f"  {i['identifier']} ({_age(i.get('updatedAt'))}d){who} — {i['title']}", f"  {i['url']}"]
    out.append(f"\n— Stale issue check: {', '.join(i['identifier'] for i in rows)}")
    print("\n".join(out))


def weekly():
    now = datetime.now(timezone.utc)
    since = (now - timedelta(days=7)).strftime("%Y-%m-%dT00:00:00Z")
    team = {"team": {"id": {"eq": TEAM_ID}}}
    # Closed this week by close date; updatedAt would count an old issue that just got a comment.
    closed = issues({**team, "state": {"type": {"in": ["completed", "canceled"]}},
                     "or": [{"completedAt": {"gte": since}}, {"canceledAt": {"gte": since}}]},
                    "identifier title createdAt completedAt canceledAt url")
    opened = issues({**team, "createdAt": {"gte": since}}, "identifier title createdAt url")
    if not closed and not opened:
        return  # silent: no activity
    days = []
    for i in closed:
        try:
            days.append((_dt(i.get("completedAt") or i["canceledAt"]) - _dt(i["createdAt"])).total_seconds() / 86400)
        except (AttributeError, KeyError, ValueError, TypeError):
            pass
    parts = [f"✅ {len(closed)} closed"] if closed else []
    if opened:
        parts.append(f"📥 {len(opened)} opened")
    if days:
        avg = sum(days) / len(days)
        parts.append(f"⏱️ Avg close: {avg * 24:.1f} hrs" if avg < 1 else f"⏱️ Avg close: {avg:.1f} days")

    def fmt(items, cap=8):
        extra = len(items) - cap
        return ", ".join(f"{i['identifier']} {i['title']}" for i in items[:cap]) + (f", …and {extra} more" if extra > 0 else "")

    out = [f"📊 **Week in Review** — {(now - timedelta(days=7)).strftime('%b %d')}–{now.strftime('%b %d')}",
           " · ".join(parts)]
    if closed:
        out.append(f"\n**Shipped:** {fmt(closed)}")
    if opened:
        out.append(f"**Opened:** {fmt(opened)}")
    print("\n".join(out))


def main(report):
    {"digest": digest, "stale": stale, "weekly": weekly}[report]()


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in ("digest", "stale", "weekly"):
        sys.exit("Usage: linear_report.py digest|stale|weekly")
    main(sys.argv[1])
