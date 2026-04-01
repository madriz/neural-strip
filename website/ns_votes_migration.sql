-- ns_votes: Vote tracking for Neural Strip cartoons
-- Run in Supabase SQL editor

CREATE TABLE IF NOT EXISTS ns_votes (
    id          UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    cartoon_id  TEXT NOT NULL,
    vote        TEXT NOT NULL CHECK (vote IN ('like', 'dislike')),
    visitor_id  TEXT,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ns_votes_cartoon ON ns_votes (cartoon_id);

-- RLS: allow anonymous inserts and reads (anon key)
ALTER TABLE ns_votes ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Allow anonymous reads" ON ns_votes
    FOR SELECT TO anon USING (true);

CREATE POLICY "Allow anonymous inserts" ON ns_votes
    FOR INSERT TO anon WITH CHECK (true);

-- Helper function for aggregated counts (optional, app.js falls back to manual query)
CREATE OR REPLACE FUNCTION ns_vote_counts()
RETURNS TABLE (cartoon_id TEXT, likes BIGINT, dislikes BIGINT) AS $$
    SELECT
        cartoon_id,
        COUNT(*) FILTER (WHERE vote = 'like') AS likes,
        COUNT(*) FILTER (WHERE vote = 'dislike') AS dislikes
    FROM ns_votes
    GROUP BY cartoon_id;
$$ LANGUAGE sql STABLE;
