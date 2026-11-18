# Neural Strip

> AI Generated Humor about AI. Because, Why Not?

Neural Strip runs in two steps. Every day at 7:00 AM Eastern, a GitHub Actions pipeline scans 15 AI news sources, selects the most interesting story and writes a New Yorker-style joke and image prompt via Claude AI. A person then draws the cartoon from that prompt on Ideogram's free web tier and publishes it to [neuralstrip.com](https://neuralstrip.com). The words are published as Claude wrote them.

From April 16 to November 18, 2026 the pipeline was fully automatic: it also called the Ideogram API and published about 200 cartoons with no human step. The image step went back to a person to stop paying for image generation. The automatic path is still in the code, switched off (see [Full automation](#full-automation)).

## How it works

```
RSS Feeds (15 sources)
      |
      v
Claude Haiku (picks story, writes joke, generates image prompt)
      |
      v
pending_post.json on the daily-output branch + a "Cartoon ready" GitHub issue
      |
      v
Ideogram free web tier, by hand (best of four)
      |
      v
publish.html, by hand (uploads the image, updates cartoons.json on main)
      |
      v
neuralstrip.com (GitHub Pages rebuilds automatically)
```

1. Every morning at 7:00 AM Eastern, a GitHub Actions cron job fetches the top headlines from 15 AI/tech RSS feeds.
2. The headlines are sent to Claude Haiku with a system prompt that instructs it to behave as a veteran New Yorker cartoon writer. It picks one story and outputs structured JSON: headline, source URL, comedic angle, scene description, setup, punchline, Instagram caption, and an image generation prompt.
3. A brand safety filter scans the generated copy for profanity, hate speech, violence, sexual content, and partisan political content. Flagged drafts are regenerated (up to three attempts).
4. The JSON is committed to the `daily-output` branch, and the workflow opens a GitHub issue titled "Cartoon ready for YYYY-MM-DD" with the image prompt in a copyable block.
5. The image prompt goes into Ideogram's free web tier by hand. The best of the four results is downloaded.
6. A local publish page (`website/publish.html`, gitignored) fetches the joke, uploads the image to `website/images/` and inserts the entry into `website/cartoons.json` on `main`. GitHub Pages rebuilds the site automatically.
7. Instagram distribution is still manual, pending Meta API approval.

### Full automation

The original fully automatic path is kept, switched off. In `.github/workflows/daily.yml`, set `IDEOGRAM_ENABLED: 'true'` in the generate job and remove `false &&` from the publish job's `if`. With an `IDEOGRAM_API_KEY` secret set, the pipeline then calls the Ideogram API and publishes on its own, as it did from April to November 2026.

## Stack

| Service | Role | Cost |
|---------|------|------|
| GitHub Actions | Runs the daily pipeline (cron) | $0 (public repo) |
| GitHub Pages | Hosts the website | $0 |
| Claude API (Haiku) | Picks the story, writes the joke | Under $0.06/month |
| Ideogram | Draws the cartoon, by hand | $0 (free web tier) |
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
gh secret set IDEOGRAM_API_KEY     # Optional: only for full automation (paid Ideogram API)
gh secret set INSTAGRAM_ACCESS_TOKEN  # Meta Graph API long-lived token (or "placeholder")
gh secret set INSTAGRAM_USER_ID       # Your Instagram numeric user ID (or "placeholder")
```

### 3. Set up Instagram (optional)

Create a Meta Developer App, add the Instagram Graph API product, and generate a long-lived access token. Set the `INSTAGRAM_ACCESS_TOKEN` and `INSTAGRAM_USER_ID` secrets with real values. If you skip this step, the pipeline still works. Cartoons just won't post to Instagram.

### 4. Create publish.html

Copy `website/publish.html.example` (if provided) or create your own local publish tool. This file must contain your GitHub Personal Access Token and is excluded from git via `.gitignore`. See the [operations manual](https://neuralstrip.com/how-it-works.html) for the full publish workflow.

### 5. Push to main

```bash
git push origin main
```

The daily pipeline will run automatically at 7:00 AM Eastern and open a "Cartoon ready" issue with the day's image prompt. You can also trigger it manually from the Actions tab.

## Contributing

Pull requests are welcome. If you improve the joke quality, fix a bug, or add a new RSS feed source, open a PR. Keep the humor dry and the code clean. No frameworks in the frontend. No emoji in the commit messages.

## License

The Neural Strip codebase (pipeline, website, automation) is
open source under the [MIT License](LICENSE).

The Neural Strip brand, cartoon archive, and character
intellectual property — including Halluci-Nate, Max Token,
Dr. Prompt, Agent Lucy, Greg Gradient, Ned Null, Pete Policygun,
and Madame Modela — are proprietary and are NOT covered by the
MIT license. All rights reserved © 2026 Neural Strip.

See [CHARACTERS.md](CHARACTERS.md) for details.
