# Neural Strip

> AI Generated Humor about AI. Because, Why Not?

A daily single-panel cartoon about the AI industry, generated end to end by machines. An automated pipeline reads AI news from RSS feeds every morning, picks the story with the most comedic potential, writes a joke, and generates an image prompt. A human picks the best cartoon from four variations and hits publish. That is the full extent of human involvement. Live at [neuralstrip.com](https://neuralstrip.com).

## How it works

```
RSS Feeds (15 sources)
      |
      v
Claude Haiku (picks story, writes joke, generates image prompt)
      |
      v
Ideogram (generates 4 cartoon variations from the prompt)
      |
      v
Publish Tool (human picks best image, uploads via GitHub API)
      |
      v
neuralstrip.com + Instagram (@neural.strip)
```

1. Every morning at 7:00 AM Eastern, a GitHub Actions cron job fetches the top headlines from 15 AI/tech RSS feeds.
2. The headlines are sent to Claude Haiku with a system prompt that instructs it to behave as a veteran New Yorker cartoon writer. It picks one story and outputs structured JSON: headline, source URL, comedic angle, scene description, setup, punchline, Instagram caption, and an image generation prompt.
3. The image prompt is pasted into Ideogram (free tier), which generates four cartoon variations in a locked visual style: clean line art, muted blue-gray pastels, New Yorker aesthetic.
4. A human reviews the four images, picks the best one, and drops it into a local publish tool. The tool uploads the image to GitHub, updates `cartoons.json`, and triggers a site rebuild.
5. The cartoon appears on the website and (when configured) posts to Instagram automatically.

## Stack

| Service | Role | Cost |
|---------|------|------|
| GitHub Actions | Runs the daily pipeline (cron) | $0 (public repo) |
| GitHub Pages | Hosts the website | $0 |
| Claude API (Haiku) | Picks the story, writes the joke | Under $0.06/month |
| Ideogram | Generates the cartoon image | $0 (free web tier) |
| Supabase | Stores vote counts | $0 (free tier) |
| Instagram Graph API | Distributes to @neural.strip | $0 |

Total monthly cost: under $0.06.

## Running your own

Fork this repo and set it up in about 15 minutes.

### 1. Fork and clone

```bash
gh repo fork madriz/neural-strip --clone
cd neural-strip
```

### 2. Set GitHub Secrets

```bash
gh secret set ANTHROPIC_API_KEY    # Claude API key (get from console.anthropic.com)
gh secret set SUPABASE_URL         # Your Supabase project URL
gh secret set SUPABASE_KEY         # Your Supabase anon/public key
gh secret set INSTAGRAM_ACCESS_TOKEN  # Meta Graph API long-lived token (or "placeholder")
gh secret set INSTAGRAM_USER_ID       # Your Instagram numeric user ID (or "placeholder")
```

### 3. Set up Supabase

Create a free Supabase project. Run this SQL in the SQL editor:

```sql
create table ns_votes (
    id uuid default gen_random_uuid() primary key,
    cartoon_id text not null,
    vote text not null check (vote in ('like', 'dislike')),
    visitor_id text not null,
    created_at timestamptz default now()
);

alter table ns_votes enable row level security;

create policy "Anyone can read votes"
    on ns_votes for select using (true);

create policy "Anyone can insert votes"
    on ns_votes for insert with check (true);
```

### 4. Set up Instagram (optional)

Create a Meta Developer App, add the Instagram Graph API product, and generate a long-lived access token. Set the `INSTAGRAM_ACCESS_TOKEN` and `INSTAGRAM_USER_ID` secrets with real values. If you skip this step, the pipeline still works. Cartoons just won't post to Instagram.

### 5. Create publish.html

Copy `website/publish.html.example` (if provided) or create your own local publish tool. This file must contain your GitHub Personal Access Token and is excluded from git via `.gitignore`. See the [operations manual](https://neuralstrip.com/how-it-works.html) for the full publish workflow.

### 6. Push to main

```bash
git push origin main
```

The daily pipeline will run automatically at 7:00 AM Eastern. You can also trigger it manually from the Actions tab.

## Contributing

Pull requests are welcome. If you improve the joke quality, fix a bug, or add a new RSS feed source, open a PR. Keep the humor dry and the code clean. No frameworks in the frontend. No emoji in the commit messages.

## License

MIT
