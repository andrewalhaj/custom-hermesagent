# Multi-Instance Topology — What's Shared, What Conflicts

Reasoning for advising on multi-host / multi-instance Hermes + Manifest setups.
Derived from the 2026-06 Railway migration and the follow-on architecture Q&A.

## The Two Layers Are Independent

```
Hermes (agent) ──(mnfst_ key)──→ nginx LB ──→ Manifest (router) ──(provider key)──→ Anthropic / DeepSeek
```

- **Manifest layer** scales cleanly: N instances behind an LB, all pointing at
  one shared Postgres (Railway). Stateless app, shared state in DB.
- **Hermes layer** does NOT scale cleanly by cloning. The agent carries
  in-process state (scheduler, gateway connections, local memory/skills/sessions).

When asked "can I add a second X," always identify WHICH layer first.

## Shared-DB Credential Model (the big one)

Both Manifest instances read the SAME `user_providers` table in Railway.
There is ONE row per provider, with ONE `auth_type` and ONE encrypted key.

Consequence: **you cannot run "OAuth on instance A, API key on instance B."**
Whatever the row says, every instance obeys — they share the DB. Per-instance
credential split is impossible without tearing apart the shared-DB design
(which would double config maintenance and defeat the point).

This is a FEATURE: enter a key once, every instance uses it. A provider 401
shows up in ALL instances' logs simultaneously because it's one bad row, not
a per-server problem. Don't misdiagnose a shared-DB 401 as "the other server
is broken."

## Why the OAuth-bypass approach can't work here

A monkey-patch that rewrites Hermes→Anthropic calls never fires on this path,
because Hermes calls **Manifest**, not Anthropic. Manifest is the actual caller
to api.anthropic.com — a separate process the Hermes-side patch can't touch.
For OAuth to apply it would have to live inside Manifest's provider client AND
in the shared DB row → both instances, same ToS exposure. There is no clean
single-instance OAuth carve-out in a shared-router topology.

(Separately: using Claude subscription OAuth credentials outside the official
CLI circumvents Anthropic's 2026-04 server-side validation and risks account
suspension. Decline. The clean fix is a pay-as-you-go `sk-ant-` API key, or
route the tier to a different provider.)

## Naive Second Hermes — Silent Conflicts

Cloning `~/.hermes` onto a second host and starting it causes:

1. **Cron fires twice.** Scheduler is in-process; two Hermes = two schedulers.
   Every job double-runs — wasteful (double LLM spend on audits), and dangerous
   for jobs that write shared files (KB dedup racing on Supabase → corruption).
   The fix is leader election (Scheduler Option B, `pg_try_advisory_lock`) — but
   that's days of core work and overkill for a personal install with ~5 jobs.

2. **Gateway/bot-token war.** Same Telegram token on two instances → long-poll
   conflict, dropped/double-answered messages. Same Discord token → gateway
   session disconnect loops. A second active agent needs SEPARATE bot tokens,
   or must run gateway-disabled.

3. **State divergence.** Each Hermes has its own memory/skills/session DB. They
   drift instantly. Syncing the session SQLite across hosts has its own races.

## Recommendation Patterns

- **Hot/cold standby (the sane personal-scale answer):** install Hermes on the
  second host but keep it STOPPED — gateway off, cron off. Fires up only if the
  primary dies, restored from the daily backup (already scp'd off-host). Zero
  conflict. This is effectively what the scheduler-recovery procedure describes.
- **Separate-purpose second agent:** fine — give it its own bot tokens and
  disable cron so jobs don't double-fire.
- **True active-active Hermes:** needs leader election + shared session state +
  token coordination. For a single user it buys nothing the Manifest LB doesn't
  already provide (model-routing redundancy). Talk the user out of it unless
  they have a real multi-user / HA requirement.

## Token Billing Across Instances

Two Manifest instances do NOT double token spend. The LB routes each request to
ONE instance, which makes ONE upstream call, billed once at the provider against
the shared key. Token volume is driven by request volume (originating from the
Hermes agent), not by how many router instances exist. The VPS adds a flat
rental cost + a network hop, never a token premium.
