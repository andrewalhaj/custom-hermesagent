# Grounded answers inside an application (cite-or-refuse RAG)

Use this when designing an app feature where an LLM must answer only from the organisation's own reviewed content (an "Ask <platform>" box, internal procedures Q&A). The agent-side citation ledger in SKILL.md is a different job: there the agent cites web sources; here the app forces the model to cite its corpus.

## Design rules

1. **Retrieval decides, the model only phrases.** Hybrid search (keyword + vector, RRF) over chunks finds passages. The model writes an answer only from those passages.
2. **Use Claude's native citations.** Pass each retrieved chunk as a `search_result` content block with citations enabled. Set `source` to a stable versioned URI (`app://page/{id}/v{version}`) and `title` to the breadcrumb. Use one block per paragraph for finer citations. The model can only cite blocks you supplied. Docs: platform.claude.com/docs/en/build-with-claude/search-results.
3. **Enforce in code, not the prompt:**
   - Drop any answer span that carries no citation.
   - If no chunk clears the score threshold, don't call the model; show "No reviewed page covers this" plus a request button.
   - The unanswered-question log is the content team's gap list, often the feature's biggest value.
   - Citations can't be combined with structured-output (JSON schema) mode, which returns an error. Render from the citation blocks instead.
4. **Index only what is publishable:** published, reviewed and in-date versions, never drafts. Chunk on the platform's natural unit (e.g. a Step), and split only oversized units (~800 chars). Prefix each chunk with its breadcrumb, which is free and measurably helps retrieval.
5. **Filter by the user's permissions before the model sees anything.**
6. **No conversational memory by default.** The index is fed by content, never by chats. Each question is answered fresh from current pages, so edits take effect at once.
   - **Follow-ups (only if logs show demand):** the client holds the last 1-2 turns in tab storage and sends them along. The search query is the current plus the previous question, with the last-cited pages added as candidates; no extra model call is needed.
   - **Prior answers** go in as plain text, never as citable blocks. Re-check client-supplied page IDs against permissions on the server.
   - Put it behind a config flag.
7. **Keep the index current without webhooks** (transactional outbox):
   - The publish/retire code writes an `index_queue` row in the same DB transaction.
   - An in-app background worker chunks and embeds the new version, upserts it, and deletes the old version's chunks.
   - A nightly reconcile compares published versions against the index and drops pages past their review date.
   - Acceptance test: edit, publish, ask; the answer cites the new version within about a minute.
8. **Minimal logging:** question text, cited page IDs and the answered/unanswered flag, with a stated retention and an explicit decision on recording the asker. People type sensitive things into question boxes.
9. **Evaluate before launch:**
   - About 40 real questions with their expected page, plus about 10 with no answer.
   - Measure: right page in the top 5, correct refusal, every sentence cited, and real-user p95 latency.
   - Set pass marks before running.
   - Build the eval only once the content exists; an empty library proves nothing.

## Wiring Claude in Microsoft Foundry (Azure tenant apps)

- **Eligibility:** Claude quota needs an Enterprise Agreement or MCA-E billing account; check this first. Deploying accepts Anthropic Marketplace terms (company legal name, country, industry). Pick a region that hosts the models and choose Data Zone deployment for in-geography processing.
- **Endpoint:** `https://<resource>.services.ai.azure.com/anthropic/v1/messages`, Anthropic Messages API, header `anthropic-version: 2023-06-01`, `model` = the deployment name.
- **Keyless auth:**
  - The app's system-assigned managed identity gets **Cognitive Services User** on the Foundry resource. Owner/Contributor do not grant inference.
  - Disable key auth on the resource.
  - App settings hold only the endpoint and deployment names.
  - Token scopes: Claude uses `https://ai.azure.com/.default`; Azure OpenAI embeddings use `https://cognitiveservices.azure.com/.default`.
  - Use one singleton `DefaultAzureCredential`, which caches tokens and also works for developers via `az login`.
- **The browser never calls Foundry;** the app backend does. Point the backend at "a model endpoint" from config, so a different provider can be swapped in later.
- **Model choice:** start with the small model (Haiku), since grounded answering from supplied passages is easy. Move up only if the eval shows misses.

## "Can we host the model on our own servers?"

- **Claude:** no. Its weights are closed and it is API-only (Anthropic, Foundry, Bedrock, Vertex). Foundry "managed compute" runs open models on dedicated GPUs, but still in Azure.
- **Open models on-prem:**
  - **Foundry Local:** single-user, on-device runtime with an OpenAI-compatible API. Microsoft says it is not a server stack, and the Windows Server preview lacked embeddings and concurrency.
  - **Foundry Local on Azure Local + Agentic Retrieval (formerly Edge RAG):** Microsoft's full on-prem RAG. It needs Azure Local, AKS Arc and GPU VMs, a platform project.
  - **vLLM/Ollama on a GPU server:** the simple self-hosted path.
- **Recommend on-prem only for a hard requirement** (contract, export control, customer rule). It loses native citations, so cite-or-refuse must then be built and tested by hand.
