# The Barolo League — auto-sync website

A self-hosted mobile-friendly dashboard, with a Python backend that fetches Fantrax standings and completed weekly matchups every 30 minutes. CSV fallback is bundled.

## Run locally

Requires Python 3.10+ and no third-party dependencies.

```bash
python server.py
```

Open http://localhost:8000. `/api/status` shows the source and any errors. `/api/data` contains the JSON cache.

## Deploy

Deploy this folder to a Python web hosting service (e.g. Render) using start command `python server.py`. Set `PORT` if the host requires it. Persistent disk is recommended for `data.json`. The website and API are served from the same origin, so no CORS configuration is required.

## Important limitations

- The Fantrax API returned valid JSON in the user's Safari session, but requests from this build environment were blocked by DNS. The hosted server must be tested before declaring live sync operational.
- This project does not log into Fantrax and does not ask for credentials.
- Weekly results are fetched for weeks indicated by the standings record; future weeks are excluded. The API shape was inferred from the user's supplied gameweek 9 sample.
- The site retains the last successful data if Fantrax becomes unreachable; the bundled CSV is used on first startup if the API fails.
- Browser CSV uploads are stored locally in the browser and do not update the server or other visitors.
- The team ID map is specific to this league. New teams require updating the map in index.html.
- This is not yet publicly hosted.


## Historical data (2018–2026)
Eight seasons are preloaded in `historical_seed.json` and served by `/api/history`. No manual upload is required. Administrator uploads override the corresponding seeded season; uploads require `ADMIN_TOKEN` and persistent disk storage on Render to survive redeployments. The 2017–2018 season is listed but has no supplied CSV. The 2026–2027 season uses the current Fantrax feed.
