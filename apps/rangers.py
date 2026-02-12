"""NY Rangers game tracker - live scores and upcoming games via ESPN API."""

import io
import json
import math
import time
import threading
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image
from ledmatrix import Canvas, run

# --- Sport / Team Config (change these for other ESPN-supported teams) ---
SPORT = "hockey"            # "basketball", "football", etc.
LEAGUE = "nhl"              # "nba", "nfl", etc.
TEAM_ID = "13"              # ESPN ID: 13 = NY Rangers, 18 = NY Knicks
TEAM_ABBR = "NYR"           # Used to identify our team in scoreboard results
TEAM_NAME = "NY RANGERS"    # Display name for no-game screen
POLL_NORMAL = 60             # Seconds between polls (no live game)
POLL_LIVE = 15               # Seconds between polls (live game)
LOGO_SIZE = 22               # Logo resize target (square)

# --- ESPN API URLs ---
TEAM_URL = f"https://site.api.espn.com/apis/site/v2/sports/{SPORT}/{LEAGUE}/teams/{TEAM_ID}"
SCOREBOARD_URL = f"https://site.api.espn.com/apis/site/v2/sports/{SPORT}/{LEAGUE}/scoreboard"

# --- Colors (dimmed for LED matrix) ---
TEAM_COLOR = (0, 40, 80)          # Rangers blue
TEAM_ACCENT = (90, 15, 25)        # Rangers red
WHITE = (80, 80, 80)
DIM_WHITE = (55, 55, 55)
DIM_GRAY = (35, 35, 35)
SCORE_COLOR = (100, 100, 100)
LIVE_RED = (100, 15, 15)
AMBER = (100, 75, 0)
DIVIDER_COLOR = (20, 20, 40)

# --- Layout constants ---
LOGO_Y = 2
AWAY_LOGO_X = 2
HOME_LOGO_X = 40
ABBR_Y = 26
DIVIDER_Y = 31

# --- Shared state ---
game_data = {
    "state": "loading",     # "loading" | "none" | "pre" | "in" | "post"
    "home_abbr": "",
    "away_abbr": "",
    "home_score": "",
    "away_score": "",
    "home_logo": None,      # list of (x, y, r, g, b) tuples
    "away_logo": None,
    "detail": "",           # ESPN shortDetail or custom formatted string
    "game_date": "",        # "FEB 27"
    "game_time": "",        # "7:00 PM"
    "period": 0,
    "clock": "",            # "12:34"
    "period_text": "",      # "P1", "P2", "P3", "OT", "SO"
    "status_detail": "",    # ESPN's full detail text (for Final/OT etc)
    "our_logo": None,       # Our team logo pixels (for no-game screen)
    "updated": 0.0,
}

_logo_cache: dict[str, list[tuple[int, int, int, int, int]]] = {}
_cache_dir = Path(__file__).parent / ".logo_cache"


# ---------------------------------------------------------------------------
# Logo pipeline
# ---------------------------------------------------------------------------

def _download_logo(url: str, abbr: str) -> list[tuple[int, int, int, int, int]]:
    """Download a logo PNG, resize, composite on black, return pixel list."""
    _cache_dir.mkdir(exist_ok=True)
    cache_file = _cache_dir / f"{LEAGUE}_{abbr.lower()}_{LOGO_SIZE}.png"

    if cache_file.exists():
        img = Image.open(cache_file).convert("RGB")
    else:
        req = urllib.request.Request(url, headers={"User-Agent": "LedMatrix/1.0"})
        raw = urllib.request.urlopen(req, timeout=10).read()
        img = Image.open(io.BytesIO(raw)).convert("RGBA")
        img = img.resize((LOGO_SIZE, LOGO_SIZE), Image.LANCZOS)
        # Composite onto black background
        bg = Image.new("RGBA", (LOGO_SIZE, LOGO_SIZE), (0, 0, 0, 255))
        bg.paste(img, (0, 0), img)
        img = bg.convert("RGB")
        img.save(cache_file)

    pixels = []
    for y in range(img.height):
        for x in range(img.width):
            r, g, b = img.getpixel((x, y))
            # Dim slightly for LED matrix
            r, g, b = int(r * 0.5), int(g * 0.5), int(b * 0.5)
            if r > 2 or g > 2 or b > 2:
                pixels.append((x, y, r, g, b))
    return pixels


def _get_logo(abbr: str, logo_url: str | None) -> list | None:
    """Get cached logo pixels, downloading if needed."""
    if abbr in _logo_cache:
        return _logo_cache[abbr]
    if not logo_url:
        return None
    try:
        pixels = _download_logo(logo_url, abbr)
        _logo_cache[abbr] = pixels
        return pixels
    except Exception as e:
        print(f"[rangers] Logo download failed for {abbr}: {e}")
        return None


def _get_team_logo_url(team: dict) -> str | None:
    """Extract logo URL from an ESPN team object (handles both formats)."""
    # Scoreboard format: team.logo (direct string)
    if "logo" in team and isinstance(team["logo"], str):
        return team["logo"]
    # Team endpoint format: team.logos[].href
    logos = team.get("logos", [])
    if logos:
        return logos[0].get("href")
    return None


# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------

def _fetch_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "LedMatrix/1.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())


def _format_game_time(iso_date: str) -> tuple[str, str]:
    """Parse ISO date -> ('FEB 27', '7:00 PM') in local timezone."""
    dt = datetime.fromisoformat(iso_date.replace("Z", "+00:00"))
    dt_local = dt.astimezone()
    date_str = dt_local.strftime("%b %d").upper()
    hour = dt_local.hour % 12 or 12
    minute = dt_local.strftime("%M")
    ampm = "AM" if dt_local.hour < 12 else "PM"
    time_str = f"{hour}:{minute} {ampm}"
    return date_str, time_str


def _period_text(period: int, period_type: str = "") -> str:
    """Convert period number to display text."""
    if period_type == "SO" or period > 4:
        return "SO"
    if period == 4 or period_type == "OT":
        return "OT"
    return f"P{period}"


def _ensure_logos(competitors: list) -> None:
    """Fetch logos for both teams in a competition."""
    for c in competitors:
        team = c.get("team", {})
        abbr = team.get("abbreviation", "")
        url = _get_team_logo_url(team)
        if abbr and url:
            _get_logo(abbr, url)


# ---------------------------------------------------------------------------
# Data fetching thread
# ---------------------------------------------------------------------------

def _fetch_loop():
    # First, always fetch our own team logo for the no-game screen
    try:
        data = _fetch_json(TEAM_URL)
        team_info = data.get("team", {})
        our_url = _get_team_logo_url(team_info)
        if our_url:
            game_data["our_logo"] = _get_logo(TEAM_ABBR, our_url)
    except Exception as e:
        print(f"[rangers] Initial team fetch failed: {e}")

    while True:
        poll = POLL_NORMAL
        try:
            # 1. Fetch team data for nextEvent
            data = _fetch_json(TEAM_URL)
            team_info = data.get("team", {})
            next_events = team_info.get("nextEvent", [])

            if not next_events:
                game_data["state"] = "none"
                game_data["updated"] = time.monotonic()
                time.sleep(poll)
                continue

            event = next_events[0]
            competition = event.get("competitions", [{}])[0]
            status = competition.get("status", {})
            status_type = status.get("type", {})
            state = status_type.get("state", "pre")
            competitors = competition.get("competitors", [])

            # Identify home/away
            home_team = {}
            away_team = {}
            home_comp = {}
            away_comp = {}
            for c in competitors:
                if c.get("homeAway") == "home":
                    home_team = c.get("team", {})
                    home_comp = c
                else:
                    away_team = c.get("team", {})
                    away_comp = c

            # Ensure logos are cached
            _ensure_logos(competitors)

            home_abbr = home_team.get("abbreviation", "")
            away_abbr = away_team.get("abbreviation", "")

            game_data["home_abbr"] = home_abbr
            game_data["away_abbr"] = away_abbr
            game_data["home_logo"] = _logo_cache.get(home_abbr)
            game_data["away_logo"] = _logo_cache.get(away_abbr)

            if state == "pre":
                # Upcoming game
                game_date, game_time = _format_game_time(event.get("date", ""))
                game_data["game_date"] = game_date
                game_data["game_time"] = game_time
                game_data["detail"] = status_type.get("shortDetail", "")
                game_data["state"] = "pre"
                game_data["updated"] = time.monotonic()

            elif state in ("in", "post"):
                # Live or final - fetch scoreboard for real-time scores
                try:
                    sb = _fetch_json(SCOREBOARD_URL)
                    for ev in sb.get("events", []):
                        comp = ev.get("competitions", [{}])[0]
                        comps = comp.get("competitors", [])
                        abbrs = [c.get("team", {}).get("abbreviation", "") for c in comps]
                        if TEAM_ABBR in abbrs:
                            # Found our game in scoreboard
                            for c in comps:
                                t = c.get("team", {})
                                a = t.get("abbreviation", "")
                                url = _get_team_logo_url(t)
                                if a and url:
                                    _get_logo(a, url)
                                if c.get("homeAway") == "home":
                                    game_data["home_abbr"] = a
                                    game_data["home_score"] = c.get("score", "0")
                                    game_data["home_logo"] = _logo_cache.get(a)
                                else:
                                    game_data["away_abbr"] = a
                                    game_data["away_score"] = c.get("score", "0")
                                    game_data["away_logo"] = _logo_cache.get(a)

                            sb_status = comp.get("status", {})
                            sb_type = sb_status.get("type", {})
                            period = sb_status.get("period", 0)
                            game_data["period"] = period
                            game_data["clock"] = sb_status.get("displayClock", "")
                            game_data["period_text"] = _period_text(period)
                            game_data["status_detail"] = sb_type.get("detail", "")
                            game_data["detail"] = sb_type.get("shortDetail", "")
                            game_data["state"] = sb_type.get("state", state)
                            break
                    else:
                        # Game not on scoreboard yet, use team endpoint data
                        game_data["home_score"] = home_comp.get("score", "0")
                        game_data["away_score"] = away_comp.get("score", "0")
                        game_data["state"] = state
                except Exception:
                    # Scoreboard fetch failed, use what we have from team endpoint
                    game_data["state"] = state

                game_data["updated"] = time.monotonic()
                if state == "in":
                    poll = POLL_LIVE

        except Exception as e:
            print(f"[rangers] Fetch error: {e}")

        time.sleep(poll)


# ---------------------------------------------------------------------------
# Drawing helpers
# ---------------------------------------------------------------------------

def _draw_logo(canvas: Canvas, pixels: list | None, ox: int, oy: int) -> None:
    if pixels is None:
        return
    for x, y, r, g, b in pixels:
        canvas.set(ox + x, oy + y, (r, g, b))


def _centered_text(canvas: Canvas, y: int, text: str, color,
                   x_min: int = 0, x_max: int = 63) -> None:
    w = len(text) * 4 - 1
    x = x_min + ((x_max - x_min + 1) - w) // 2
    canvas.text(max(0, x), y, text, color)


def _abbr_centered_under_logo(canvas: Canvas, abbr: str, logo_x: int, y: int,
                              color) -> None:
    """Center abbreviation text under a logo."""
    w = len(abbr) * 4 - 1
    x = logo_x + (LOGO_SIZE - w) // 2
    canvas.text(max(0, x), y, abbr, color)


def _draw_divider(canvas: Canvas, y: int) -> None:
    canvas.line(2, y, 61, y, DIVIDER_COLOR)


def _draw_status_dot(canvas: Canvas, t: float) -> None:
    age = t - game_data["updated"] if game_data["updated"] else 999
    if game_data["state"] == "loading":
        b = int(40 + 40 * abs(math.sin(t * 2)))
        canvas.set(1, 62, (b, b, 0))
    elif age < 120:
        b = int(30 + 30 * abs(math.sin(t * 2)))
        canvas.set(1, 62, (0, b, 0))
    else:
        b = int(40 + 40 * abs(math.sin(t * 2)))
        canvas.set(1, 62, (b, b // 2, 0))


def _abbr_color(abbr: str) -> tuple[int, int, int]:
    """Highlight our team abbreviation."""
    return TEAM_COLOR if abbr == TEAM_ABBR else DIM_WHITE


# ---------------------------------------------------------------------------
# Display modes
# ---------------------------------------------------------------------------

def _draw_loading(canvas: Canvas, t: float) -> None:
    _centered_text(canvas, 28, "LOADING", DIM_GRAY)
    # Animated dots
    dots = "." * (int(t * 2) % 4)
    _centered_text(canvas, 35, dots, DIM_GRAY)


def _draw_no_game(canvas: Canvas, t: float) -> None:
    # Centered team logo
    logo = game_data["our_logo"]
    if logo:
        cx = (64 - LOGO_SIZE) // 2
        _draw_logo(canvas, logo, cx, LOGO_Y)

    _centered_text(canvas, ABBR_Y, TEAM_NAME, TEAM_COLOR)
    _draw_divider(canvas, DIVIDER_Y)
    _centered_text(canvas, 36, "NO GAME", DIM_GRAY)
    _centered_text(canvas, 43, "SCHEDULED", DIM_GRAY)


def _draw_pre_game(canvas: Canvas, t: float) -> None:
    # Logos
    _draw_logo(canvas, game_data["away_logo"], AWAY_LOGO_X, LOGO_Y)
    _draw_logo(canvas, game_data["home_logo"], HOME_LOGO_X, LOGO_Y)

    # VS between logos
    _centered_text(canvas, 10, "VS", DIM_GRAY, x_min=24, x_max=39)

    # Abbreviations under logos
    away = game_data["away_abbr"]
    home = game_data["home_abbr"]
    _abbr_centered_under_logo(canvas, away, AWAY_LOGO_X, ABBR_Y, _abbr_color(away))
    _abbr_centered_under_logo(canvas, home, HOME_LOGO_X, ABBR_Y, _abbr_color(home))

    _draw_divider(canvas, DIVIDER_Y)

    # Date and time
    _centered_text(canvas, 34, game_data["game_date"], WHITE)
    _centered_text(canvas, 41, game_data["game_time"], WHITE)


def _draw_live_game(canvas: Canvas, t: float) -> None:
    # Logos
    _draw_logo(canvas, game_data["away_logo"], AWAY_LOGO_X, LOGO_Y)
    _draw_logo(canvas, game_data["home_logo"], HOME_LOGO_X, LOGO_Y)

    # Pulsing dot between logos (live indicator)
    pulse = int(50 + 50 * abs(math.sin(t * 3)))
    canvas.set(31, 11, (pulse, 5, 5))
    canvas.set(32, 11, (pulse, 5, 5))

    # Abbreviations
    away = game_data["away_abbr"]
    home = game_data["home_abbr"]
    _abbr_centered_under_logo(canvas, away, AWAY_LOGO_X, ABBR_Y, _abbr_color(away))
    _abbr_centered_under_logo(canvas, home, HOME_LOGO_X, ABBR_Y, _abbr_color(home))

    _draw_divider(canvas, DIVIDER_Y)

    # Score lines: "PHI  3" / "NYR  2"
    away_line = f"{away:3} {game_data['away_score']:>2}"
    home_line = f"{home:3} {game_data['home_score']:>2}"
    _centered_text(canvas, 34, away_line, _abbr_color(away))
    _centered_text(canvas, 41, home_line, _abbr_color(home))

    # Period and clock
    period_clock = f"{game_data['period_text']} {game_data['clock']}"
    _centered_text(canvas, 49, period_clock, AMBER)

    # Blinking LIVE
    if int(t * 2) % 2:
        _centered_text(canvas, 57, "LIVE", LIVE_RED)


def _draw_final(canvas: Canvas, t: float) -> None:
    # Logos
    _draw_logo(canvas, game_data["away_logo"], AWAY_LOGO_X, LOGO_Y)
    _draw_logo(canvas, game_data["home_logo"], HOME_LOGO_X, LOGO_Y)

    # Abbreviations
    away = game_data["away_abbr"]
    home = game_data["home_abbr"]
    _abbr_centered_under_logo(canvas, away, AWAY_LOGO_X, ABBR_Y, _abbr_color(away))
    _abbr_centered_under_logo(canvas, home, HOME_LOGO_X, ABBR_Y, _abbr_color(home))

    _draw_divider(canvas, DIVIDER_Y)

    # Final text
    detail = game_data.get("status_detail", "Final")
    if "OT" in detail:
        final_text = "FINAL OT"
    elif "SO" in detail:
        final_text = "FINAL SO"
    else:
        final_text = "FINAL"
    _centered_text(canvas, 35, final_text, DIM_WHITE)

    # Score lines
    away_line = f"{away:3} {game_data['away_score']:>2}"
    home_line = f"{home:3} {game_data['home_score']:>2}"
    _centered_text(canvas, 42, away_line, _abbr_color(away))
    _centered_text(canvas, 49, home_line, _abbr_color(home))


# ---------------------------------------------------------------------------
# Main render
# ---------------------------------------------------------------------------

def render(canvas: Canvas, t: float, frame: int) -> None:
    canvas.clear()

    state = game_data["state"]
    if state == "loading":
        _draw_loading(canvas, t)
    elif state == "none":
        _draw_no_game(canvas, t)
    elif state == "pre":
        _draw_pre_game(canvas, t)
    elif state == "in":
        _draw_live_game(canvas, t)
    elif state == "post":
        _draw_final(canvas, t)

    _draw_status_dot(canvas, t)


# Start background fetcher
threading.Thread(target=_fetch_loop, daemon=True).start()

if __name__ == "__main__":
    run(render, fps=10, title="NY Rangers")
