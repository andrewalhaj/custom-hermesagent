---
name: linkedin-writing
description: "Use when writing LinkedIn posts or work write-ups as Andrew"
sector_path: ["user", "writing"]
version: 1.0.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [linkedin, writing, voice, style, posts, networking]
    created_by: agent
load_when:
  - "user asks to draft a LinkedIn post or LinkedIn message"
  - "user asks to make writing sound more human / less AI"
  - "drafting professional networking correspondence (recruiter/hiring follow-ups)"
  - "user asks for a short write-up / notes explaining what an issue was and how it was fixed"
---

# LinkedIn Writing — Andrew's Voice

## When to Use

Drafting or editing any LinkedIn artifact for Andrew: Hermes-series posts, professional posts, networking messages, recruiter/offer follow-ups — or any "make this sound more human / less AI" edit pass on professional writing.

Two artifact types: **posts** (Hermes series & professional) and **messages** (networking, recruiter follow-ups). Both share one rule: it should read like it was typed in one pass by a person.

## Hard bans (Andrew-corrected, do not regress)

- **Contrast-hook intros** — BANNED 2026-08-27, explicit: "lets stop using this type of intro, its very ai sounding." Patterns: "X should be simple. Here's why it isn't." / "X should be one command. On my install, it's the most dangerous thing I do." Any *setup-then-subversion* opener counts. **Open mid-story with a concrete fact instead**: "Last month an update deleted one line from a systemd service file, and it took me days to notice."
- Emoji bullets, hashtags, engagement-bait closers ("Agree? 👇", "What's your take?")
- Em-dashes in messages (biggest AI tell in short-form; acceptable sparingly in posts)
- Stiff formal scaffolding: "I wanted to reach out and say that while it's unfortunate..." → "Wanted to reach out and say that even though we couldn't make X work..."
- **Reacting to someone else's post.** When the idea was sparked by a third-party post/article/screenshot, the draft never references it ("Saw a post this week where someone...", "someone's agent was burning..."). Posts are first-person from Andrew's own setup; a reaction frame makes it a quote-tweet and borrows the other person's numbers. Restate the problem as a general mechanism and anchor it in Andrew's own measurements instead.

## Posts (Hermes series)

- First-person plumbing details, honest about limits and costs. Real mechanisms, not vague "AI journey" abstractions.
- **Two post modes — infer which before drafting.** *Technical* Hermes posts use measured figures (rule below). *Reflective* posts (why he builds, what it feels like) must NOT name his setup's features or numbers at all ("talk about them loosely"): describe mechanisms generically (where knowledge lives, what it's allowed to touch, what proves it looked first). His framing for these: the goal was never to offload work — it was building something structurally perfect; the chase is the joy; a system that keeps getting better but never stops producing issues is "a crazy kind of fun"; building it felt like home as an IT professional. Keep "a rule written in a prompt is a suggestion, a check that returns an error is a rule" — he kept it through every revision.
- Pull the concrete numbers from the live system before drafting (technical mode only: tool counts per MCP server, token totals, latencies), not from memory of the chat — the post's credibility is the measured figure, and a number that was quoted earlier in conversation may already be stale or approximate. State the counterfactual in the same units ("1,500 tokens vs. ~60,000 if loaded eagerly").
- **Public post = no private shorthand.** Anything that only means something inside his sessions (experiment tags like RG2, internal component names like "store gate", metric names like MRR / S@5 / "containment", host aliases) is out. Name the thing by what it does ("the step that scores each fact before it is saved") and name a vendor product only by its public name.
- **Benchmarks for non-AI readers: plain outcomes, then flat percentages.** Say what the test is ("151 questions written against my own documents, most with the answer buried deep in a section") and what was counted ("the text handed to the model actually contained the answer"). Report before/after as percentages, and when he asks for "a flat %" give both the point gain and the relative gain in one sentence ("52% to 80%: 28 points, a 55% improvement"). Rounded figures only; keep the exact decimals and the derivation in the chat note under the draft, never in the post.
- **Explain the mechanism in lay steps before the numbers** when the story hinges on where a pipeline broke: a short paragraph that names the steps in everyday words ("filing / finding / handing it over") and says where the fault sat. He pointed at exactly this kind of paragraph as what makes the post readable to non-AI people; weave it in right after the hook, and refer back to the step names later ("the ranker in the finding step") so the reader keeps the map.
- **"I liked draft N better, add X" = rebuild on draft N**, not on the latest. Keep N's sentences verbatim and add only X.
- **When he hands back a cut-down draft, re-stitch it.** Removing a paragraph leaves dangling references ("the new score" with no score paragraph) and paragraphs that start cold; open each paragraph with a join to the previous one ("So I measured the handoff itself." / "The reason I could measure it at all is...") and drop every reference to removed content. "No transition in the paragraphs" is the complaint this prevents.
- When he hands back a thesis or an ordering ("start with X, then transition to Y"), rebuild the post around his order from the opening line; patching the previous draft keeps the old spine and he will ask again. Keep the sentences he kept through earlier revisions verbatim.
- Structure: concrete incident → what it revealed → the structural fix or lesson → honest cost/verdict. No listicles.
- Lessons stated as earned aphorisms in-flow ("The net needs a net."), never as a **Bold Takeaway:** block.
- ~250–350 words. End on a plain statement, not a question to the audience.

## Messages (networking / offer-declined / follow-ups)

- Short. Warm without gushing. No bitterness on declined offers; leave the door open both directions ("if things change down the road on either side, my door's open").
- "Pick your brain," "you've got going on" — conversational idiom over polish. Some sentences shouldn't start with "I."
- Fill in a specific detail from the actual conversations (projects discussed) — the difference between a form letter and a real note. Ask Andrew for the specific if unknown.
- Close simple: "All the best to you and the team."

## De-AI-ing an existing draft (the edit pass Andrew asks for)

1. Kill em-dashes → commas or periods.
2. Kill "I wanted to reach out and say that while..." class openers → start with the verb ("Wanted to reach out...").
3. Swap formal phrases for spoken ones ("in motion" → "you've got going on").
4. Vary sentence starts; drop subject pronouns where natural speech would.
5. Read it back: if any sentence couldn't be said aloud to the person, rewrite it.

## Incident / fix write-ups for work

Same voice rules apply when Andrew asks for "a short write-up of what the issue was and how we fixed it" and then "make it sound human":
- Issue + cause chain + fix only. **No dead ends, no attempted-and-ruled-out steps, no typos/detours.** Ruled-out items may appear only as a one-line "not X, not Y" after the cause. The cause must be a chain back to the first decision that went wrong (e.g. replaced drive -> no auto-match -> wizard default), not just the proximate missing piece; "what caused it in the first place?" is the follow-up otherwise.
- First person, told in order, plain opener ("Quick rundown on the X laptop so the next person doesn't have to work this out again"). No headers except a closing "For next time:" bullet list. No em-dashes.
- Keep one mechanism sentence per step (what the thing is and why it mattered, e.g. what Winre.wim is). A bare "copied Winre.wim" gets "what was the point of that?"
- Status lines must match reality (e.g. BitLocker still off); never narrate a planned step as done.
- Save under ~/reports/; keep the technical version alongside the plain one instead of overwriting.

## External humanizer skills

When Andrew asks for a tool (not a draft) to make AI writing sound human: the maintained Claude Code options are [blader/humanizer](https://github.com/blader/humanizer) (the original; rewrite, critique, rewrite again; no invented facts) and [conorbronsdon/avoid-ai-writing](https://github.com/conorbronsdon/avoid-ai-writing) (detect-only mode, voice profiles, deterministic checker for CI). Both are based on Wikipedia's "Signs of AI writing". A generic humanizer only gets text to clean and generic; his voice comes from this skill's rules, so offer to export them as a voice profile (`VOICE.md`) alongside the humanizer.

## Pitfalls

- Grammar-polishing a human draft can make it MORE AI-sounding — Andrew's own phrasing ("you guys") is often better left than "corrected." Only fix actual errors; flag, don't auto-formalize.
- When Andrew says "how does this sound?" he wants targeted tightens (1–3 specific edits), not a rewrite.
- "Retype the whole thing" = clean final copy only, no commentary above it.
