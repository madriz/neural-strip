# Neural Strip

> AI Generated Humor about AI. Because, Why Not?

Neural Strip is fully autonomous. Every day at 7:00 AM Eastern, a GitHub Actions pipeline scans 15 AI news sources, selects the most interesting story, generates a New Yorker-style joke via Claude AI, illustrates it via Ideogram AI, and publishes the cartoon to [neuralstrip.com](https://neuralstrip.com) automatically. No human reviews or approves content before publication.

## How it works

```
RSS Feeds (15 sources)
      |
      v
Claude Haiku (picks story, writes joke, generates image prompt)
      |
      v
Ideogram API (renders the cartoon illustration)
      |
      v
GitHub Actions publish job (commits image + updates cartoons.json on main)
      |
      v
neuralstrip.com (GitHub Pages rebuilds automatically)
```

1. Every morning at 7:00 AM Eastern, a GitHub Actions cron job fetches the top headlines from 15 AI/tech RSS feeds.
2. The headlines are sent to Claude Haiku with a system prompt that instructs it to behave as a veteran New Yorker cartoon writer. It picks one story and outputs structured JSON: headline, source URL, comedic angle, scene description, setup, punchline, Instagram caption, and an image generation prompt.
3. A brand safety filter scans the generated copy for profanity, hate speech, violence, sexual content, and partisan political content. Flagged drafts are regenerated (up to three attempts).
4. The image prompt is sent to the Ideogram API, which returns the rendered single-panel cartoon.
5. A second Actions job commits the image into `website/images/`, prepends a new entry to `website/cartoons.json`, and pushes to `main`. GitHub Pages rebuilds the site automatically.
6. Instagram distribution is still manual, pending Meta API approval.

## Stack

| Service | Role | Cost |
|---------|------|------|
| GitHub Actions | Runs the daily pipeline (cron) | $0 (public repo) |
| GitHub Pages | Hosts the website | $0 |
| Claude API (Haiku) | Picks the story, writes the joke | Under $0.06/month |
| Ideogram | Generates the cartoon image | $0 (free web tier) |
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
gh secret set IDEOGRAM_API_KEY     # Ideogram API key (get from ideogram.ai)
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

The daily pipeline will run automatically at 7:00 AM Eastern. You can also trigger it manually from the Actions tab.

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
