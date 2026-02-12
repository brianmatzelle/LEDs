# Live Sports on a 64x64 LED Matrix

**2026-02-12**

I wanted to glance across the room and know when the Rangers play next -- or see the score if they're mid-game. That turned into a multi-team sports tracker that pulls live data from ESPN, downloads team logos, and lets me cycle through favorites with the board buttons.

## ESPN's free API

The biggest surprise: ESPN has a fully public JSON API with no authentication required. Two endpoints do all the heavy lifting:

- **Team detail** (`/teams/{id}`) -- returns the next scheduled event, opponent, date/time, and the team's record. This is how we know about upcoming games days in advance.
- **Scoreboard** (`/scoreboard`) -- returns every game happening today with live scores, period, and clock.

The URL pattern is identical across sports -- swap `hockey/nhl` for `basketball/nba` and everything else stays the same. That made multi-sport support essentially free.

The team endpoint also provides logo URLs and hex color codes for every team, which we use for display theming and logo downloads.

## Getting images onto a 64x64 canvas

The canvas only has `set(x, y, color)` -- no image blitting. Pillow handles the pipeline:

1. Download the PNG from ESPN's CDN (500x500, transparent background)
2. Resize to 22x22 with LANCZOS resampling
3. Composite onto a black RGBA background (transparent pixels become black for the LED matrix)
4. Convert to RGB, save to `apps/.logo_cache/` on disk
5. At render time, iterate pre-computed pixel lists calling `canvas.set()` per pixel

Logos are cached by `{league}_{abbr}_{size}.png` so they only download once. The pixel lists are also cached in memory so there's no Pillow work after the first load.

22x22 turned out to be the sweet spot -- big enough to be recognizable, small enough to fit two logos side-by-side with room for "VS" between them.

## Display modes

The app has four states based on what ESPN reports:

- **Pre-game**: Away logo / VS / Home logo at the top. Date and time centered below.
- **Live**: Same logo layout but with a pulsing red dot between them. Score lines for each team, period + clock, and a blinking "LIVE" label.
- **Final**: Logos, "FINAL" (or "FINAL OT" / "FINAL SO"), and the final score.
- **No game**: Just the team's logo centered with "NO GAME SCHEDULED."

Each team's abbreviation is highlighted in the team's own color (pulled from ESPN's hex color data and dimmed to ~35% brightness for the LEDs).

## From single-team to multi-team

The first version (`apps/rangers.py`) was hardcoded to one team. Making it multi-team meant solving three problems:

**1. Favorites management.** The app now opens with a terminal submenu where you pick leagues, browse teams, and build a favorites list. Favorites save to `apps/.sports_favorites.json` with all the ESPN metadata (team ID, abbreviation, colors, logo URL) so the display never needs to hit the teams-list endpoint at runtime.

**2. Fetching N teams without hammering the API.** A single background thread maintains a priority queue of `(next_poll_time, team)` entries. It processes the nearest one, sleeps until it's due, fetches, then reschedules based on game state (60s for upcoming games, 15s for live). A 2-second minimum gap between any two fetches keeps things polite. Same-league teams share a scoreboard response via a 10-second TTL cache.

**3. Button navigation.** The display loop follows the same pattern as `chooser.py` -- custom main loop with a non-blocking UDP socket on port 7778 for board buttons, plus keyboard arrow fallback. Up/Down cycles through favorites with a 2-second overlay showing the team abbreviation and position.

## Sport-aware rendering

Period text adapts to the sport: P1/P2/P3/OT for hockey, Q1-Q4 for basketball and football, 1H/2H for soccer, INN for baseball. This is a simple lookup in `_period_text()` keyed on the `sport` field from the favorites config.

## What I'd do next

- Auto-cycle through teams on a timer (30s per team) with manual override resetting the timer
- Flash/animate on goal events by polling more aggressively during live games and detecting score changes
- Add the team's record to the pre-game screen (the data is already in the ESPN team endpoint)

## How to use it

```bash
make sim app=apps/sports.py       # Opens submenu, then simulator
make stream app=apps/sports.py    # Same but streams to the board
```

First run prompts you to pick teams. After that, the submenu lets you add/remove before starting. On the display, Up/Down buttons (or arrow keys) cycle through your teams.
