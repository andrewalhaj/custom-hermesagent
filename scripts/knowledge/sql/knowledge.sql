-- Knowledge store schema, dumped from the live project on 2026-10-21 (scripts/knowledge/sql/dump_knowledge_schema.py).
-- Idempotent: safe to re-run. Apply via the Supabase Management API or the SQL editor, then NOTIFY pgrst, 'reload schema'.

create extension if not exists vector;
create extension if not exists pg_net;

create table if not exists public.knowledge (
  id text not null,
  text text not null,
  tags jsonb,
  priority text default 'normal'::text,
  source text,
  stored_at timestamp with time zone default now(),
  context_prefix text,
  body_hash text,
  tsv tsvector,
  metadata jsonb,
  sector_path jsonb default '[]'::jsonb,
  vector halfvec(2560)
);

create index if not exists idx_knowledge_metadata ON public.knowledge USING gin (metadata);
create unique index if not exists knowledge_pkey ON public.knowledge USING btree (id);
create index if not exists knowledge_priority_idx ON public.knowledge USING btree (priority);
create index if not exists knowledge_source_idx ON public.knowledge USING btree (source);
create index if not exists knowledge_tags_idx ON public.knowledge USING gin (tags);
create index if not exists knowledge_tsv_idx ON public.knowledge USING gin (tsv);
create index if not exists knowledge_vector_hnsw ON public.knowledge USING hnsw (vector halfvec_cosine_ops) WITH (m='16', ef_construction='64');

alter table public.knowledge enable row level security;
-- policy service role full access: ALL for {service_role} using (true) with check (true)

CREATE OR REPLACE FUNCTION public._meta_matches_any(meta jsonb, filter jsonb)
 RETURNS boolean
 LANGUAGE sql
 IMMUTABLE
AS $function$
  SELECT meta IS NOT NULL AND filter IS NOT NULL AND EXISTS (
    SELECT 1 FROM jsonb_each_text(filter) AS f(fkey, fval)
    WHERE meta ? fkey
    AND EXISTS (
      SELECT 1 FROM jsonb_array_elements_text(
        CASE WHEN jsonb_typeof(meta->fkey) = 'array' 
             THEN meta->fkey 
             ELSE jsonb_build_array(meta->fkey) 
        END
      ) mv
      WHERE EXISTS (
        SELECT 1 FROM jsonb_array_elements_text(
          CASE WHEN jsonb_typeof(fval::jsonb) = 'array'
               THEN fval::jsonb
               ELSE jsonb_build_array(fval::jsonb)
          END
        ) jv
        WHERE replace(lower(mv), '-', ' ') LIKE '%' || replace(lower(jv), '-', ' ') || '%'
      )
    )
  )
$function$;

CREATE OR REPLACE FUNCTION public.knowledge_tsv_trigger()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
BEGIN
  NEW.tsv := to_tsvector('english', COALESCE(NEW.text, ''));
  RETURN NEW;
END;
$function$;

CREATE OR REPLACE FUNCTION public.match_knowledge(query_embedding vector, match_count integer DEFAULT 5, filter_tags text[] DEFAULT NULL::text[], min_priority text DEFAULT NULL::text)
 RETURNS TABLE(id text, text text, tags jsonb, priority text, source text, stored_at timestamp with time zone, context_prefix text, body_hash text, similarity double precision)
 LANGUAGE plpgsql
AS $function$
BEGIN
  RETURN QUERY
  SELECT
    k.id,
    k.text,
    k.tags,
    k.priority,
    k.source,
    k.stored_at,
    k.context_prefix,
    k.body_hash,
    1 - (k.vector <=> query_embedding) AS similarity
  FROM knowledge k
  WHERE
    (filter_tags IS NULL OR k.tags ?| filter_tags)
  ORDER BY k.vector <=> query_embedding
  LIMIT match_count;
END;
$function$;

CREATE OR REPLACE FUNCTION public.match_knowledge_v2(query_embedding vector, query_text text DEFAULT ''::text, match_count integer DEFAULT 20, filter_tags jsonb DEFAULT NULL::jsonb, min_priority text DEFAULT NULL::text, metadata_filter jsonb DEFAULT NULL::jsonb)
 RETURNS TABLE(id text, text text, tags jsonb, priority text, source text, stored_at timestamp with time zone, context_prefix text, body_hash text, similarity double precision, match_source text)
 LANGUAGE plpgsql
AS $function$
DECLARE effective_limit INTEGER;
BEGIN
  IF query_text IS NULL OR query_text = '' THEN
    RETURN QUERY
    SELECT k.id, k.text, k.tags, k.priority, k.source,
           k.stored_at, k.context_prefix, k.body_hash,
           1 - (k.vector <=> query_embedding) AS similarity,
           'vector'::TEXT AS match_source
    FROM knowledge k
    WHERE (metadata_filter IS NULL OR _meta_matches_any(k.metadata, metadata_filter))
      AND (filter_tags IS NULL OR k.tags @> filter_tags)
      AND (min_priority IS NULL
           OR array_position(ARRAY['low','normal','high','critical'], k.priority)
              >= array_position(ARRAY['low','normal','high','critical'], min_priority))
    ORDER BY k.vector <=> query_embedding LIMIT match_count;
    RETURN;
  END IF;
  effective_limit := match_count * 2;
  RETURN QUERY
  WITH vector_arm AS (
    SELECT k.id _id, k.text _text, k.tags _tags, k.priority _priority, k.source _source,
           k.stored_at _stored_at, k.context_prefix _context_prefix, k.body_hash _body_hash,
           row_number() OVER (ORDER BY k.vector <=> query_embedding) _rn
    FROM knowledge k
    WHERE (metadata_filter IS NULL OR _meta_matches_any(k.metadata, metadata_filter))
      AND (filter_tags IS NULL OR k.tags @> filter_tags)
      AND (min_priority IS NULL
           OR array_position(ARRAY['low','normal','high','critical'], k.priority)
              >= array_position(ARRAY['low','normal','high','critical'], min_priority))
    ORDER BY k.vector <=> query_embedding LIMIT effective_limit
  ),
  fts_arm AS (
    SELECT k.id _id, k.text _text, k.tags _tags, k.priority _priority, k.source _source,
           k.stored_at _stored_at, k.context_prefix _context_prefix, k.body_hash _body_hash,
           row_number() OVER (ORDER BY ts_rank(k.tsv, websearch_to_tsquery('english', query_text)) DESC) _rn
    FROM knowledge k
    WHERE k.tsv @@ websearch_to_tsquery('english', query_text)
      AND (metadata_filter IS NULL OR _meta_matches_any(k.metadata, metadata_filter))
      AND (filter_tags IS NULL OR k.tags @> filter_tags)
      AND (min_priority IS NULL
           OR array_position(ARRAY['low','normal','high','critical'], k.priority)
              >= array_position(ARRAY['low','normal','high','critical'], min_priority))
    ORDER BY _rn LIMIT effective_limit
  ),
  merged AS (
    SELECT _id, _text, _tags, _priority, _source, _stored_at, _context_prefix, _body_hash,
           (1.0 / (60.0 + _rn::double precision)) _rrf_score, 'vector'::TEXT _arm_source FROM vector_arm
    UNION ALL
    SELECT _id, _text, _tags, _priority, _source, _stored_at, _context_prefix, _body_hash,
           (1.0 / (60.0 + _rn::double precision)) _rrf_score, 'fts'::TEXT _arm_source FROM fts_arm
  ),
  summed AS (
    SELECT m._id, m._text, m._tags, m._priority, m._source,
           m._stored_at, m._context_prefix, m._body_hash,
           SUM(m._rrf_score) _total_rrf,
           string_agg(DISTINCT m._arm_source, '+' ORDER BY m._arm_source) _match_source
    FROM merged m GROUP BY 1,2,3,4,5,6,7,8
  )
  SELECT s._id, s._text, s._tags, s._priority, s._source,
         s._stored_at, s._context_prefix, s._body_hash,
         s._total_rrf, s._match_source
  FROM summed s ORDER BY s._total_rrf DESC LIMIT match_count;
END;
$function$;

CREATE OR REPLACE FUNCTION public.match_knowledge_v4(query_embedding vector, query_text text DEFAULT ''::text, match_count integer DEFAULT 20, filter_tags jsonb DEFAULT NULL::jsonb, min_priority text DEFAULT NULL::text, metadata_filter jsonb DEFAULT NULL::jsonb, sector_filter text DEFAULT NULL::text)
 RETURNS TABLE(id text, text text, tags jsonb, priority text, source text, stored_at timestamp with time zone, context_prefix text, body_hash text, sector_path jsonb, similarity double precision, match_source text)
 LANGUAGE plpgsql
AS $function$
DECLARE effective_limit INTEGER;
BEGIN
  IF query_text IS NULL OR query_text = '' THEN
    RETURN QUERY
    SELECT k.id, k.text, k.tags, k.priority, k.source,
           k.stored_at, k.context_prefix, k.body_hash, k.sector_path,
           1 - (k.vector <=> query_embedding) AS similarity,
           'vector'::TEXT AS match_source
    FROM knowledge k
    WHERE (sector_filter IS NULL OR k.sector_path->>0 = sector_filter OR k.sector_path = '[]')
      AND (metadata_filter IS NULL OR _meta_matches_any(k.metadata, metadata_filter))
      AND (filter_tags IS NULL OR k.tags @> filter_tags)
      AND (min_priority IS NULL
           OR array_position(ARRAY['low','normal','high','critical'], k.priority)
              >= array_position(ARRAY['low','normal','high','critical'], min_priority))
    ORDER BY k.vector <=> query_embedding LIMIT match_count;
    RETURN;
  END IF;
  effective_limit := match_count * 2;
  RETURN QUERY
  WITH vector_arm AS (
    SELECT k.id _id, k.text _text, k.tags _tags, k.priority _priority, k.source _source,
           k.stored_at _stored_at, k.context_prefix _context_prefix, k.body_hash _body_hash,
           k.sector_path _sector_path,
           row_number() OVER (ORDER BY k.vector <=> query_embedding) _rn
    FROM knowledge k
    WHERE (sector_filter IS NULL OR k.sector_path->>0 = sector_filter OR k.sector_path = '[]')
      AND (metadata_filter IS NULL OR _meta_matches_any(k.metadata, metadata_filter))
      AND (filter_tags IS NULL OR k.tags @> filter_tags)
      AND (min_priority IS NULL
           OR array_position(ARRAY['low','normal','high','critical'], k.priority)
              >= array_position(ARRAY['low','normal','high','critical'], min_priority))
    ORDER BY k.vector <=> query_embedding LIMIT effective_limit
  ),
  fts_arm AS (
    SELECT k.id _id, k.text _text, k.tags _tags, k.priority _priority, k.source _source,
           k.stored_at _stored_at, k.context_prefix _context_prefix, k.body_hash _body_hash,
           k.sector_path _sector_path,
           row_number() OVER (ORDER BY ts_rank(k.tsv, websearch_to_tsquery('english', query_text)) DESC) _rn
    FROM knowledge k
    WHERE k.tsv @@ websearch_to_tsquery('english', query_text)
      AND (sector_filter IS NULL OR k.sector_path->>0 = sector_filter OR k.sector_path = '[]')
      AND (metadata_filter IS NULL OR _meta_matches_any(k.metadata, metadata_filter))
      AND (filter_tags IS NULL OR k.tags @> filter_tags)
      AND (min_priority IS NULL
           OR array_position(ARRAY['low','normal','high','critical'], k.priority)
              >= array_position(ARRAY['low','normal','high','critical'], min_priority))
    ORDER BY _rn LIMIT effective_limit
  ),
  merged AS (
    SELECT _id, _text, _tags, _priority, _source, _stored_at, _context_prefix, _body_hash, _sector_path,
           (1.0 / (20.0 + _rn::double precision)) _rrf_score, 'vector'::TEXT _arm_source FROM vector_arm
    UNION ALL
    SELECT _id, _text, _tags, _priority, _source, _stored_at, _context_prefix, _body_hash, _sector_path,
           (1.0 / (20.0 + _rn::double precision)) _rrf_score, 'fts'::TEXT _arm_source FROM fts_arm
  ),
  summed AS (
    SELECT m._id, m._text, m._tags, m._priority, m._source,
           m._stored_at, m._context_prefix, m._body_hash, m._sector_path,
           SUM(m._rrf_score) _total_rrf,
           string_agg(DISTINCT m._arm_source, '+' ORDER BY m._arm_source) _match_source
    FROM merged m GROUP BY 1,2,3,4,5,6,7,8,9
  )
  SELECT s._id, s._text, s._tags, s._priority, s._source,
         s._stored_at, s._context_prefix, s._body_hash, s._sector_path,
         s._total_rrf, s._match_source
  FROM summed s ORDER BY s._total_rrf DESC LIMIT match_count;
END;
$function$;

CREATE OR REPLACE FUNCTION public.on_knowledge_insert()
 RETURNS trigger
 LANGUAGE plpgsql
 SECURITY DEFINER
AS $function$
DECLARE
  _url    constant text := '<DISPATCHER_PUBLIC_URL>/api/hooks/knowledge';  -- set per deployment
  _secret constant text := '<WEBHOOK_SECRET>';  -- must equal the dispatcher WEBHOOK_SECRET; never commit the real value
  _payload jsonb;
  _headers jsonb;
BEGIN
  _payload := jsonb_build_object(
    'id',       NEW.id,
    'text',     left(NEW.text, 200),
    'tags',     to_jsonb(NEW.tags),
    'source',   NEW.source,
    'priority', NEW.priority
  );

  _headers := jsonb_build_object(
    'Content-Type', 'application/json',
    'Authorization', 'Bearer ' || _secret
  );

  PERFORM net.http_post(
    url     := _url,
    body    := _payload,
    headers := _headers
  );

  RETURN NEW;
EXCEPTION WHEN OTHERS THEN
  RAISE LOG 'knowledge insert webhook failed (row %): %', NEW.id, SQLERRM;
  RETURN NEW;
END;
$function$;

drop trigger if exists knowledge_insert_webhook on public.knowledge;
CREATE TRIGGER knowledge_insert_webhook AFTER INSERT ON public.knowledge FOR EACH ROW EXECUTE FUNCTION on_knowledge_insert();
drop trigger if exists knowledge_tsv_update on public.knowledge;
CREATE TRIGGER knowledge_tsv_update BEFORE INSERT OR UPDATE ON public.knowledge FOR EACH ROW EXECUTE FUNCTION knowledge_tsv_trigger();
