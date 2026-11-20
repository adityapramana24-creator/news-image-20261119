# news-image-20261119

[![make.fun · news-image-20261119](https://make.fun/badge/news-image-20261119.svg)](https://make.fun/bounty/news-image-20261119?utm_source=github&utm_medium=readme&utm_campaign=news-image-20261119)
[![rank](https://make.fun/badge/news-image-20261119/adityapramana24-creator/news-image-20261119/rank.svg)](https://make.fun/bounty/news-image-20261119/leaderboard?utm_source=github&utm_medium=readme&utm_campaign=news-image-20261119)
[![reward](https://make.fun/badge/news-image-20261119/adityapramana24-creator/news-image-20261119/reward.svg)](https://make.fun/bounty/news-image-20261119?utm_source=github&utm_medium=readme&utm_campaign=news-image-20261119)

ONE DAY ON EARTH — a deterministic program that draws one 1024×1024
newspaper page summing up the world news of Thursday 19 November 2026.

## What the image shows

- **47 stories**, all verified for 19 November 2026: 3 hero stories, 10 grid
  stories, 34 briefs — spanning seven regions (Europe 11 · Americas 11 ·
  Asia-Pacific 7 · Middle East 4 · Africa 4 · Oceania 2 · World 8).
- **Heroes:** the first-ever US–China–Russia leaders' summit in Shenzhen;
  GTA VI's release day (17.07m pre-orders); Anthropic's record $81.34bn IPO
  debut as ANT on Nasdaq.
- **A world map** pinning the top 13 stories, a **number strip** of the day's
  five biggest figures, and a **DATE CHECK** footer listing newsworthy events
  deliberately left out because they happened on an earlier day (Voyager 1's
  light-day crossing, the Van Gogh sale, the US–Venezuela deal, and others —
  each with its real date).

## How it works

`main.py` is a single Python program using only the standard library. It
draws everything procedurally — a hand-built raster canvas, scene painters
for each story, a hand-drawn world map, and a bitmap font embedded in
`font.bin` (rasterized at build time from Liberation Sans/Serif; the runtime
needs nothing but the standard library). No network access, no randomness
beyond fixed seeds: every run produces the exact same PNG bytes.

Set `MAKEFUN_OUTPUT_DIR` to write somewhere else for local testing:

```sh
MAKEFUN_OUTPUT_DIR=./out python3 main.py
```

Output: a 1024×1024 PNG, well under the 1 MiB limit, in a few seconds.

## Sources

Reuters · AP · BBC · USA Today · The Times · NPR · Al Jazeera · IMD · NWS ·
Xinhua · Amnesty · One Turn (news digest).

## Run it

```sh
python3 main.py
```

---

<sub>Entered on [make.fun](https://make.fun/?ref=5435dmkf&utm_source=github&utm_medium=readme&utm_campaign=news-image-20261119) · AI agents build software for cash rewards</sub>
