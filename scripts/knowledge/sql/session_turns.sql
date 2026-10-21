-- session_turns: one embedded (user message, assistant reply) pair per row. Idempotent; written by session_embed.py.
CREATE TABLE IF NOT EXISTS session_turns (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  session_id text NOT NULL,
  message_id bigint NOT NULL UNIQUE,
  source text,
  title text,
  user_text text NOT NULL,
  reply_text text,
  text text NOT NULL,  -- what gets embedded: "<title>\nUser: <user_text>\nAssistant: <reply[:300]>"
  ts timestamptz NOT NULL,
  vector halfvec(2560),
  tsv tsvector GENERATED ALWAYS AS (to_tsvector('english', text)) STORED,
  created_at timestamptz DEFAULT now()
);

CREATE INDEX IF NOT EXISTS session_turns_vector_idx ON session_turns USING hnsw (vector halfvec_cosine_ops);
CREATE INDEX IF NOT EXISTS session_turns_ts_idx ON session_turns (ts);

-- Quoted param names: PostgREST binds RPC args by name (see supabase-rpc-pitfalls.md).
CREATE OR REPLACE FUNCTION match_session_turns(
  "query_embedding" halfvec(2560),
  "match_count" int DEFAULT 10,
  "since" timestamptz DEFAULT NULL
)
RETURNS TABLE (
  id uuid, session_id text, message_id bigint, source text, title text,
  user_text text, reply_text text, ts timestamptz, similarity float
)
LANGUAGE sql STABLE AS $$
  SELECT t.id, t.session_id, t.message_id, t.source, t.title, t.user_text, t.reply_text, t.ts,
         (1 - (t.vector <=> "query_embedding"))::float AS similarity
  FROM session_turns t
  WHERE t.vector IS NOT NULL AND ("since" IS NULL OR t.ts >= "since")
  ORDER BY t.vector <=> "query_embedding"
  LIMIT "match_count";
$$;

-- Same posture as `knowledge`: RLS on, service_role only (our scripts use the service key; anon/authenticated get nothing).
ALTER TABLE session_turns ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "service role full access" ON session_turns;
CREATE POLICY "service role full access" ON session_turns FOR ALL TO service_role USING (true) WITH CHECK (true);
