# Supply Chain Situation Tracker

A self-updating map of global shipping & logistics disruptions, powered by RSS
feeds + a free LLM tier + GitHub Actions. Runs forever at $0/month.

![architecture](https://img.shields.io/badge/cost-%240%2Fmonth-green)
![status](https://img.shields.io/badge/status-POC-orange)

---

## What this is

- **Static HTML page** (`index.html`) showing an interactive Leaflet map + news
  feed of current supply chain events.
- **Python fetcher** (`fetch.py`) pulls RSS feeds from maritime news sources,
  classifies each article via Google Gemini, and writes a structured
  `events.json`.
- **GitHub Actions** (`.github/workflows/update.yml`) runs the fetcher every
  4 hours and commits the updated `events.json` back to the repo.
- **GitHub Pages** serves the page for free.

Net result: deploy once, and you have a tracker that auto-updates without you
ever touching it again. Cost is $0/month using free tiers throughout.

---

## File overview

```
supply-chain-tracker/
├── README.md              you are here
├── index.html             the tracker page (Leaflet + JS)
├── events.json            event data; updated by fetch.py
├── fetch.py               news fetcher / Gemini classifier
├── feeds.json             list of RSS feeds to monitor (edit freely)
├── requirements.txt       Python dependencies
└── .github/workflows/
    └── update.yml         GitHub Actions schedule
```

---

## Deployment — 15 minutes

You need: a GitHub account, and a Google Gemini API key (free).

### 1. Get a Gemini API key (3 min)

1. Go to <https://aistudio.google.com/app/apikey>
2. Sign in with a Google account.
3. Click "Create API key". Pick "Create API key in new project" if asked.
4. Copy the key (looks like `AIza...`).

Free tier is 1,500 requests/day on Gemini 2.0 Flash — about 10× what this
tracker needs.

### 2. Create the GitHub repo (5 min)

1. Go to <https://github.com/new>
2. Name it whatever you want (e.g. `supply-chain-tracker`).
3. Make it **public** (required for free GitHub Pages on personal accounts).
4. Create the repo.
5. Either upload all the files in this folder via the web UI ("Add file →
   Upload files"), or clone the empty repo and copy the files in via
   command line.

### 3. Add the API key as a secret (1 min)

1. In your new repo, go to **Settings → Secrets and variables → Actions**.
2. Click **New repository secret**.
3. Name: `GEMINI_API_KEY`
4. Secret: paste your Gemini key.
5. Click **Add secret**.

### 4. Enable GitHub Pages (2 min)

1. Go to **Settings → Pages**.
2. Under **Source**, pick **Deploy from a branch**.
3. Branch: `main`, folder: `/ (root)`.
4. Click **Save**.
5. Wait 1-2 minutes; GitHub will give you a URL like
   `https://yourusername.github.io/supply-chain-tracker/`.

### 5. Test it (3 min)

1. Visit your GitHub Pages URL. You should see the tracker with the 17 seed
   events loaded.
2. Go to the **Actions** tab in your repo. You should see the "Update events"
   workflow listed.
3. Click it, then click **Run workflow** to trigger it manually for the first
   run.
4. Wait ~1-2 minutes for it to finish. Refresh your tracker page; new events
   from the past week should appear.

That's it. The workflow will now run every 4 hours forever.

---

## Local testing (optional)

If you want to test the fetcher on your own machine before deploying:

```bash
cd supply-chain-tracker
pip install -r requirements.txt

# Test the pipeline with no API key (placeholder data only)
DRY_RUN=1 python fetch.py

# Real run
export GEMINI_API_KEY=your_key_here
python fetch.py
```

To view the page locally, you need to serve it via HTTP (not file://):

```bash
python -m http.server 8000
# then open http://localhost:8000
```

---

## Customisation

### Add or remove news sources

Edit `feeds.json`. Just keep the JSON structure. Suggested sources beyond
the defaults:

- **Industry-specific:** Aerial Press, Air Cargo News, AJOT, Container News,
  Joc.com, Maritime Executive, Maersk newsroom, MSC press
- **Regional:** Asia Cargo News, Eyefortransport, Lloyd's List, Splash
- **Government/regulator:** USCG, IMO, USTR, UNCTAD

### Change the update frequency

Edit `.github/workflows/update.yml`, change the cron schedule:

```yaml
- cron: '0 */4 * * *'   # every 4 hours (default)
- cron: '0 */1 * * *'   # every hour (heavier on Gemini quota)
- cron: '0 9,17 * * *'  # twice a day at 9am and 5pm UTC
```

### Adjust the classification rules

Edit the `PROMPT` constant in `fetch.py`. The relevant changes:

- **Stricter relevance:** add explicit "reject" clauses (e.g. "reject articles
  about specific carrier earnings unless they include a service change")
- **Different categories:** change the `CATEGORIES` list — make sure
  `index.html`'s `CATEGORIES` object matches
- **Geographic focus:** add to the prompt "Only return events located in Asia,
  Africa, or the Middle East — reject events solely about North America" or
  similar

### Cap the number of events shown

Edit `MAX_TOTAL` in `fetch.py` (default 200). Older events past this cap are
dropped on each run.

---

## How it actually works

1. GitHub Actions starts on schedule.
2. `fetch.py` reads `feeds.json` and `events.json`.
3. For each feed, it parses the RSS, looks at each article.
4. New articles (not previously seen by URL hash, less than 7 days old) get
   sent to Gemini with the classification prompt.
5. Gemini returns either `{"relevant": false}` or a structured event object.
6. Relevant events get added to `events.json` with stable IDs.
7. The file is committed back to the repo.
8. GitHub Pages serves the updated file automatically.
9. When anyone visits the tracker, `index.html` fetches the latest
   `events.json` and renders.

Total elapsed time per run: typically 1-3 minutes depending on how many new
articles there are.

---

## Cost breakdown

Per month, with default settings:

| Component | Cost |
|---|---|
| GitHub Actions (2,000 min free, we use ~10) | $0 |
| GitHub Pages hosting | $0 |
| Gemini API (free tier: 1,500 req/day; we use ~50-200) | $0 |
| Domain (optional, if you want a custom URL) | $0-15 |
| **Total** | **$0/month** |

You only start paying if you upgrade to a much higher article volume or want
faster updates than every hour.

---

## Known limitations

- **Snapshot, not real-time.** Updates every 4 hours by default; not minute-by-
  minute. For real-time you'd need streaming integrations.
- **LLM hallucinations.** Gemini occasionally puts the wrong city in a wrong
  country, or gets a lat/lng off by a degree. Spot-check the output
  periodically. For production credibility you'd want a human-review step.
- **RSS feeds change.** If a source restructures their feed or shuts it down,
  it silently drops out of your sources. Logs will show the failure.
- **No vessel tracking.** This tool tracks *events*, not ships. AIS data is a
  paid feed (~$100+/month) and isn't included.
- **No alerting.** Tool is pull-based (you visit it). No push notifications
  to Slack/email — easy to add later if useful.

---

## Where to go next

If the POC proves useful, the natural next steps roughly in order:

1. **Add a human review queue.** Set new events to `status: "pending"` by
   default, surface them in a separate UI, require manual approval before
   they appear on the public map. This is the LiveUAMap pattern.
2. **Add static context layers.** Show major shipping lanes, key chokepoints
   shaded, major hubs as muted markers. Makes the map more meaningful.
3. **Add filters by date / region.** "Show me events from the last 7 days in
   Asia only" etc.
4. **Add alerting.** Slack webhook integration so the team gets pinged when
   a critical-severity event is detected.
5. **Add selective AIS data.** Pay for cheaper MarineTraffic API endpoints to
   show e.g. "vessels currently queued at Hormuz" — targeted indicators, not
   full traffic.

---

## License

Do whatever you want with this.
