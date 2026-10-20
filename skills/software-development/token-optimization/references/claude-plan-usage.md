# Claude plan usage: reading the live gauge and calling the API on the subscription

Use when the user asks "how much of my plan will X use", before launching a bulk job on the OAuth login, or when building a self-throttling job.

## Read the live gauge (one cheap call)

Send a 1-token Haiku request with the OAuth access token from `~/.claude/.credentials.json` (`claudeAiOauth.accessToken`, never printed) and read the response headers:

- `anthropic-ratelimit-unified-5h-utilization` and `-7d-utilization`: fraction used, two decimals (whole-percent resolution).
- `-5h-reset` / `-7d-reset`: epoch seconds. Convert them and give the user local reset times.
- `-overage-status: rejected`: going over the cap is disabled, so hitting the weekly cap stops EVERY request, including the live agent's, until the weekly reset. Say so when the weekly gauge is high.

Use this, not `/api/oauth/usage`: that endpoint returns 429 under normal use and tells you nothing about inference limits.

## Request shape that works on the OAuth login

- Headers: `Authorization: Bearer <token>`, `anthropic-version: 2023-06-01`, `anthropic-beta: claude-code-20250219,oauth-2025-04-20`.
- `system` as a LIST of text blocks with the Claude Code identity line alone as the first block and task instructions in later blocks. A single concatenated system string drew a 429 while the block form succeeded with no retries.
- Retry 429s with backoff (20 s × attempt), and log the response body plus the ratelimit headers so a real limit can be told apart from a malformed request.

## Estimating a job's share

- Anthropic publishes no tokens-per-percent figure. Measure the job's tokens with a pilot (sum `usage` across responses), then convert using what the gauge moved during a known heavy stretch (e.g. a session of Opus turns plus several subagents moved the 5h gauge only ~6%).
- Present it as a range labelled as an estimate, plus the offer to measure. Never present a converted percentage as measured.
- Sonnet weighs less against the allowance than Opus; repeated cached prefixes are cheap.

## Guard for bulk jobs on the shared pool

The job, the live agent and its subagents all draw on one allowance. Run with concurrency 1, read the utilization headers on every response, and checkpoint progress. Andrew's standing ceiling: a bulk job may run at any time, but neither the 5h nor the weekly gauge may pass 0.75 — sleep until the relevant reset when either reaches it. Use that ceiling unless he sets another. With the guard, the worst case is the job stopping early, not the agent.

Count parallel subagent fan-outs against the same pool: five Sonnet children doing tool-heavy verification can cost a few percent of the week, so state that cost when dispatching them while the weekly gauge is high.
