---
name: principle-of-least-astonishment
sector_path: ["development"]
description: "POLA: design APIs/CLI/config to least surprise."
version: 1.1.0
author: Hermes Agent (merged — uploaded revision + concrete examples + pitfalls from v1.0.0)
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [POLA, code-quality, api-design, naming, conventions, code-review, refactoring]
---

# Principle of Least Astonishment

## The core idea

A component should behave the way the people who use it already expect it to behave, given its name, its signature, the surrounding conventions, and the obvious common-sense reading. When behavior diverges from expectation, the user pays a tax: a bug, a debugging session, a re-read of the docs, or a quiet wrong assumption that ships to production. The goal of this skill is to spend *your* effort up front so that *no one downstream is surprised.*

The operative test, applied before you commit to any decision:

> If a competent colleague who has never seen this code read only the name and signature, would the actual behavior match the picture in their head? If not, you have an astonishment debt. Pay it now — by changing the behavior, the name, or (last resort) by documenting loudly — not later.

Surprise is the symptom. The cure is almost always to change the *thing*, not to add a comment apologizing for it.

## Two modes: designing vs refactoring

This SKILL.md covers **designing** for least-astonishment (naming, signatures,
defaults, failure modes). For **executing a behavior-preserving POLA refactor of
an existing repo** — the "inventory → greenlight → single-class slices → prove
byte-identical" workflow — see `references/executing-a-pola-refactor.md`. It
carries the route-map-diff proof technique, the untrack-needs-a-guard pitfall, the
check-the-response-shape-not-just-the-path rule, and more. The runnable
`scripts/fastapi_route_dump.py` resolves net URLs through FastAPI's
`_IncludedRouter` wrappers for before/after route diffs.

## When to reach for this

Use POLA whenever you touch a surface other people (or future-you) will rely on:

- **Naming** anything: functions, methods, variables, flags, endpoints, env vars, events.
- **Designing a signature**: argument order, optionality, return type, what "success" looks like.
- **Choosing a default**: what happens when the caller says nothing.
- **Deciding how something fails**: exception vs. null vs. sentinel vs. silent no-op.
- **Refactoring**: keeping the same name while changing what it does is the classic trap.
- **Reviewing a diff**: half of code review is just "this surprised me — is that intended?"

## Heuristics by surface

Each rule below is paired with *why it reduces surprise*, then a before/after. The "after" is the unsurprising version.

### Names must tell the whole truth — and nothing but

A name is a promise. The reader will trust it instead of reading the body. So the name must describe everything the function does, and the function must do nothing the name doesn't imply.

- A name that *understates* (does more than it says) causes hidden side effects.
- A name that *overstates* (does less than it says) causes silent gaps.

**Before:** `getUser(id)` also lazily creates the user if missing and logs them in.
**After:** `getOrCreateUser(id)` for the creation; keep `getUser` as a pure lookup that returns nothing for a miss. If it logs them in too, that belongs in `signIn`, not a getter.

Rule of thumb: `get`/`is`/`has`/`find` imply *no observable side effects*. If your `get` mutates, writes, or fires events, rename it to a verb that admits it (`fetchAndCache`, `ensure`, `resolve`).

### Return types should be predictable across all paths

The caller writes one piece of handling code. If the return shape changes depending on the input, every caller must defend against every shape — and most won't.

**Before:** returns a single object for one match, an array for many, and `false` for none.
**After:** always return an array (empty for none). One shape, one code path. If a single item is the contract, return the item or throw/return a documented "not found" — but pick one and hold it everywhere.

Corollary: don't mix "I'll throw on failure" and "I'll return null on failure" across sibling functions in the same module. Consistency within a boundary beats local cleverness.

### Side effects should live where the reader expects them

Functions that *sound* like queries should not mutate. Constructors should not perform I/O or network calls. Importing a module should not start a server. The surprise here is temporal: the effect happens at a moment the caller didn't anticipate.

**Before:** `new ReportBuilder()` opens a database connection in the constructor.
**After:** the constructor just configures; `build()` (or `connect()`) does the I/O, where the caller can see it, await it, and handle its failure.

### The default should be the safe, common, least-surprising choice

Most callers will take the default. Make the default the thing they'd choose if they thought about it for a minute — usually the safe and common case, not the powerful-but-dangerous one.

**Before:** `deleteFiles(paths, { recursive: true })` defaults `recursive` to `true`.
**After:** default `recursive` to `false`. The destructive, far-reaching behavior should require the caller to ask for it explicitly. Defaults should fail safe, not fail big.

### Fail loudly, early, and at the source

Silent failures are the most expensive kind of astonishment because the surprise is deferred — the user discovers it three layers and ten minutes away from the cause. Validate inputs at the boundary and reject bad ones immediately with a message that names what was wrong and what was expected.

**Before:** invalid config value is coerced to a default and execution continues; the user sees mysterious wrong output later.
**After:** invalid config raises at startup: `PORT must be an integer between 1–65535, got "eighty"`. Loud, immediate, actionable.

Never swallow an exception just to keep going unless "keep going" is the genuinely correct, documented behavior. An empty `catch` block is a future debugging nightmare wearing a disguise.

### Match the conventions of the language, the ecosystem, and *this* codebase

People carry expectations from everything they've used before. Honor them. The unsurprising choice is usually the idiomatic one, even if you personally prefer another.

- Use the language's standard iteration, error, and async idioms rather than inventing your own.
- Match the surrounding codebase's existing patterns (naming, file layout, test style) over your personal preference — internal consistency is itself a form of predictability.
- For CLIs, behave like other CLIs: support `--help`, accept `-v`/`--verbose`, read from stdin when piped, exit non-zero on failure, write errors to stderr.

**Before:** a flag `--no-cache=true` that, when set to `false`, *enables* the no-cache (double negative).
**After:** `--cache` / `--no-cache`, no value, following the standard boolean-flag convention.

### Make API and protocol semantics match their meaning

For HTTP and other well-specified protocols, the spec *is* the expectation. Diverging from it astonishes every client author.

- `GET` is safe and idempotent — never let a `GET` mutate state.
- `PUT` is idempotent; `POST` is not. Pick the verb that matches the semantics.
- Return the status code that means what happened: `404` for missing, `400` for bad input, `409` for conflict, `201` for created — not `200` with `{ "error": ... }` in the body.

### Hold the contract steady across changes

The deepest astonishment is when something that worked yesterday does something different today under the same name and version. Treat observable behavior as a contract.

- Don't change what a function does while keeping its name and signature. Add a new path, or version it.
- Follow semantic versioning honestly: behavior changes that break callers are major bumps, not patch releases.
- When you must deprecate, keep the old behavior working and warn, rather than yanking it silently.

### Avoid magic and hidden implicitness

"Magic" is behavior the reader can't predict from what's in front of them — implicit conversions, action-at-a-distance via global state, configuration that changes behavior invisibly. Each instance forces the reader to know a secret.

**Before:** a function's behavior silently changes based on a global `APP_ENV` read deep inside.
**After:** pass the mode in explicitly, or at minimum make the dependency visible at the call site. Explicit is predictable; implicit is a trap.

## Review checklist

When auditing a diff (yours or someone else's) for astonishment, walk these in order. Stop and flag at the first "no."

1. **Name truth** — Does each new/changed name describe exactly what it does, no more, no less?
2. **One shape** — Does each function return a consistent type across all input paths?
3. **Effect placement** — Do query-sounding names stay side-effect-free? Are I/O and mutation where a reader would look for them?
4. **Safe default** — Is the default the safe, common case? Does anything destructive require an explicit opt-in?
5. **Loud failure** — Are bad inputs rejected early at the boundary with a clear message? No silent swallowing?
6. **Convention fit** — Does it match the language idiom, the ecosystem norm, and the existing patterns in this repo?
7. **Protocol correctness** — Do verbs, status codes, and idempotency match their real semantics?
8. **Contract stability** — Does this change anything callers already depend on under the same name/version?
9. **No magic** — Can the behavior be predicted from what's visible at the call site, without secret knowledge?

If a surprising choice survives all this on purpose (see below), require a comment that explains *why* it's surprising and what the reader should expect — the surprise must at least be documented at the point of contact.

## Anti-patterns quick reference

- A `get`/`is`/`has` that mutates, writes, or logs in.
- A function returning different types for different inputs.
- A constructor that does network or disk I/O.
- A destructive operation that's on by default.
- An empty or log-only `catch` that hides a real error.
- A boolean flag with a double negative (`--no-disable-x`).
- A `GET` endpoint with side effects, or a `200` wrapping an error.
- A "patch" release that changes observable behavior.
- Behavior that flips based on hidden global state.
- Inventing a bespoke pattern where a well-known idiom exists.

## Concrete examples

**Name that lies about side effects**
```python
# ASTONISHING — "get" silently writes to the DB
def get_user(id):
    user = db.query(id)
    user.last_seen = now()
    db.commit()          # surprise: a getter mutates state
    return user

# UNASTONISHING — the write is named
def touch_and_get_user(id):
    user = db.query(id)
    user.last_seen = now()
    db.commit()
    return user
# or split: get_user(id) (pure read) + mark_seen(id) (explicit write)
```

**Inconsistent failure mode**
```javascript
// ASTONISHING — three different failure behaviors
function find(id) {
  if (!id) throw new Error("no id");   // throws
  const row = db.get(id);
  if (!row) return null;               // returns null
  if (row.deleted) return undefined;   // returns undefined (!)
  return row;
}

// UNASTONISHING — one failure mode
function find(id) {
  if (!id) throw new Error("id is required");
  const row = db.get(id);
  return row && !row.deleted ? row : null;  // not-found is always null
}
```

**Dangerous default**
```python
# ASTONISHING — wipes a tree with no opt-in
def clear(path, recursive=True): ...   # one typo nukes a directory

# UNASTONISHING — destructive behavior is opt-in
def clear(path, recursive=False): ...  # default is the safe, shallow op
```

**Flag that switches modes**
```python
# ASTONISHING — `notify=True` makes a "save" also send emails
def save_order(order, notify=False):
    db.write(order)
    if notify:
        email_all_customers(order)     # a save function that spams inboxes

# UNASTONISHING — one job each
def save_order(order): db.write(order)
def notify_customers(order): email_all_customers(order)
```

**Unconventional argument order**
```go
// ASTONISHING — destination first breaks the stdlib mental model
func Copy(dst, src string) error  // wait, which one gets overwritten?

// UNASTONISHING — source, then destination (matches io.Copy, cp, rsync)
func Copy(src, dst string) error
```

## When astonishment is acceptable

POLA is a default, not an absolute. Sometimes the genuinely correct behavior is mildly surprising — a performance optimization that reorders work, a security control that fails closed in an unintuitive way, a domain rule that's just inherently weird. In those cases the rule is *not* "never surprise" but "never surprise **silently**":

- Make the surprising behavior **discoverable** — name it honestly, document it at the call site, surface it in the type or signature where you can.
- Make sure the surprise buys something real (correctness, safety, a hard constraint) — not just your personal taste or a clever trick.
- Prefer surprising *once, loudly, at the boundary* over surprising *quietly, repeatedly, deep in the system.*

When in doubt, optimize for the reader who is tired, in a hurry, and trusting your names. That reader is, sooner or later, you.

## Pitfalls

- **"It's obvious to me" ≠ obvious to a cold reader.** You wrote it; you're the worst judge of its surprise factor. Do the cold-read pass anyway.
- **Consistency beats personal taste.** A codebase that's uniformly "B-grade" is more predictable — and thus better — than one that's a patchwork of locally-optimal "A-grade" choices.
- **Comments don't fix bad names.** If you need a comment to explain *what* a name means, rename it. Reserve comments for *why* a necessary surprise exists.
- **Cleverness is a smell.** If you're proud of how clever a line is, a maintainer will hate it at 2am. Prefer boring.
- **Report before executing.** Writing files, editing config, or making infrastructure changes without presenting a written plan first is a surprise — the worst kind, because the user discovers it after the fact and has to roll back. Present a report of planned changes. Wait for greenlight. Then act. SURPRISE IS THE EXECUTION, NOT THE PLAN.
- **Profile-awareness is part of the contract.** When making the same change across profiles (default + ha-bot), verify each profile gets its own correctly-scoped version. Copy-pasting a generic reference (like "read AGENTS.md") into a profile-specific file creates a path ambiguity that will fail silently in context. Test each profile's version against its own environment.
- **Narrow greenlight is not broad permission.** When the user says "proceed with X only" or "just do the compresses," the scope is exactly X — nothing more. Adding Y (cuts, extras, side changes, "while I'm in there" improvements) without explicit approval is scope creep. The user discovers the extra work after the fact and has to roll back or correct you. If you spot something else worth doing while executing X, flag it and ask; don't fold it in silently. A greenlight is a contract — honor its boundaries.

See `references/report-before-execute.md` for the full protocol and failure-mode documentation.

See `references/fastapi-route-map-diff.md` for the route-map-diff technique that proves a FastAPI routing-convention refactor (moving the `/api` prefix between router files and `server.py`) is behavior-preserving — includes the `_IncludedRouter` walker needed because modern FastAPI doesn't flatten included routers into `app.routes`.
