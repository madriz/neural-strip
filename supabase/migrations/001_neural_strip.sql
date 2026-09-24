-- Neural Strip: votes for the main site and the lab A/B comparison.
-- Project: apcapfjvdofhifsmwpvx (created 2026-09-24).
-- Idempotent: safe to run more than once in the Supabase SQL Editor.
-- Touches only ns_ tables and functions.

-- ── ns_votes: like/dislike on main site cartoons ────────────────────────────

CREATE TABLE IF NOT EXISTS public.ns_votes (
    id          UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    cartoon_id  TEXT NOT NULL CHECK (cartoon_id ~ '^\d{4}-\d{2}-\d{2}$'),
    vote        TEXT NOT NULL CHECK (vote IN ('like', 'dislike')),
    visitor_id  TEXT CHECK (char_length(visitor_id) <= 64),
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ns_votes_cartoon ON public.ns_votes (cartoon_id);

-- ── ns_lab_votes: blind cloud vs local choice on /lab ──────────────────────

CREATE TABLE IF NOT EXISTS public.ns_lab_votes (
    id          UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    pair_id     TEXT NOT NULL CHECK (pair_id ~ '^(backfill-)?\d{4}-\d{2}-\d{2}$'),
    choice      TEXT NOT NULL CHECK (choice IN ('cloud', 'local')),
    visitor_id  TEXT NOT NULL CHECK (char_length(visitor_id) BETWEEN 1 AND 64),
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT ns_lab_votes_pair_visitor_key UNIQUE (pair_id, visitor_id)
);

-- ── Row level security: anon may read and insert, nothing else ─────────────

ALTER TABLE public.ns_votes     ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.ns_lab_votes ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Allow anonymous reads"   ON public.ns_votes;
DROP POLICY IF EXISTS "Allow anonymous inserts" ON public.ns_votes;
CREATE POLICY "Allow anonymous reads"   ON public.ns_votes FOR SELECT TO anon USING (true);
CREATE POLICY "Allow anonymous inserts" ON public.ns_votes FOR INSERT TO anon WITH CHECK (true);

DROP POLICY IF EXISTS "Allow anonymous reads"   ON public.ns_lab_votes;
DROP POLICY IF EXISTS "Allow anonymous inserts" ON public.ns_lab_votes;
CREATE POLICY "Allow anonymous reads"   ON public.ns_lab_votes FOR SELECT TO anon USING (true);
CREATE POLICY "Allow anonymous inserts" ON public.ns_lab_votes FOR INSERT TO anon WITH CHECK (true);

-- Explicit grants: RLS already blocks update and delete (no policy), and the
-- revoke removes the privileges outright as a second layer.
REVOKE ALL ON public.ns_votes, public.ns_lab_votes FROM anon, authenticated;
GRANT SELECT, INSERT ON public.ns_votes, public.ns_lab_votes TO anon;

-- ── Aggregate helpers ──────────────────────────────────────────────────────

CREATE OR REPLACE FUNCTION public.ns_vote_counts()
RETURNS TABLE (cartoon_id TEXT, likes BIGINT, dislikes BIGINT)
LANGUAGE sql STABLE SECURITY INVOKER SET search_path = public AS $$
    SELECT
        cartoon_id,
        COUNT(*) FILTER (WHERE vote = 'like')    AS likes,
        COUNT(*) FILTER (WHERE vote = 'dislike') AS dislikes
    FROM public.ns_votes
    GROUP BY cartoon_id;
$$;

CREATE OR REPLACE FUNCTION public.ns_lab_vote_counts()
RETURNS TABLE (pair_id TEXT, cloud BIGINT, local BIGINT)
LANGUAGE sql STABLE SECURITY INVOKER SET search_path = public AS $$
    SELECT
        pair_id,
        COUNT(*) FILTER (WHERE choice = 'cloud') AS cloud,
        COUNT(*) FILTER (WHERE choice = 'local') AS local
    FROM public.ns_lab_votes
    GROUP BY pair_id;
$$;

REVOKE ALL ON FUNCTION public.ns_vote_counts(), public.ns_lab_vote_counts() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.ns_vote_counts(), public.ns_lab_vote_counts() TO anon;
