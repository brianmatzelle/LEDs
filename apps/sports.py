"""Multi-team sports tracker - favorite teams with button navigation."""

import io
import json
import math
import socket
import sys
import time
import threading
import urllib.request
from datetime import datetime
from pathlib import Path

import pygame
from PIL import Image

from ledmatrix.canvas import Canvas
from ledmatrix.simulator import Simulator
from ledmatrix.sender import Sender

# --- Constants ---
BUTTON_PORT = 7778
BTN_UP_CODE = 0x01
BTN_DOWN_CODE = 0x02
OVERLAY_DURATION = 2.0
AUTO_ROTATE = 60.0
POLL_NORMAL = 60
POLL_LIVE = 15
MIN_FETCH_GAP = 2.0
LOGO_SIZE = 22
ESPN_BASE = "https://site.api.espn.com/apis/site/v2/sports"

# --- Layout ---
LOGO_Y = 1
AWAY_LOGO_X = 2
HOME_LOGO_X = 40
ABBR_Y = 25

# --- Fixed colors ---
WHITE = (80, 80, 80)
DIM_WHITE = (55, 55, 55)
DIM_GRAY = (35, 35, 35)
LIVE_RED = (100, 15, 15)
AMBER = (100, 75, 0)

# --- League catalog ---
LEAGUES = [
    ("hockey",     "nhl",   "NHL",  "National Hockey League"),
    ("basketball", "nba",   "NBA",  "National Basketball Assoc."),
    ("football",   "nfl",   "NFL",  "National Football League"),
    ("baseball",   "mlb",   "MLB",  "Major League Baseball"),
    ("soccer",     "eng.1", "EPL",  "English Premier League"),
    ("soccer",     "esp.1", "LIGA", "Spanish La Liga"),
    ("soccer",     "ger.1", "BUND", "German Bundesliga"),
    ("soccer",     "ita.1", "SA",   "Italian Serie A"),
    ("soccer",     "fra.1", "L1",   "French Ligue 1"),
    ("soccer",     "usa.1", "MLS",  "Major League Soccer"),
    ("soccer",     "mex.1", "LIGM", "Mexican Liga MX"),
    ("soccer",     "uefa.champions", "UCL", "UEFA Champions League"),
    ("basketball", "wnba",  "WNBA", "Women's NBA"),
    ("football",   "college-football", "NCAF", "NCAA Football"),
    ("basketball", "mens-college-basketball", "NCAB", "NCAA Basketball"),
]

# --- Shared state ---
all_game_data: dict[str, dict] = {}
_logo_cache: dict[str, list] = {}
_scoreboard_cache: dict[str, tuple[float, dict]] = {}
_cache_dir = Path(__file__).parent / ".logo_cache"
_config_path = Path(__file__).parent / ".sports_favorites.json"


# ---------------------------------------------------------------------------
# Color helpers
# ---------------------------------------------------------------------------

def _hex_to_led(hex_color: str, brightness: float = 0.35) -> tuple[int, int, int]:
    """Convert ESPN hex color (e.g. '0041A8') to dimmed LED RGB tuple."""
    if not hex_color or len(hex_color) < 6:
        return (40, 40, 40)
    r = int(hex_color[0:2], 16)
    g = int(hex_color[2:4], 16)
    b = int(hex_color[4:6], 16)
    return (int(r * brightness), int(g * brightness), int(b * brightness))


# ---------------------------------------------------------------------------
# Logo pipeline
# ---------------------------------------------------------------------------

def _download_logo(url: str, league: str, abbr: str) -> list:
    """Download a logo PNG, resize, composite on black, return pixel list."""
    _cache_dir.mkdir(exist_ok=True)
    cache_file = _cache_dir / f"{league}_{abbr.lower()}_{LOGO_SIZE}.png"

    if cache_file.exists():
        img = Image.open(cache_file).convert("RGB")
    else:
        req = urllib.request.Request(url, headers={"User-Agent": "LedMatrix/1.0"})
        raw = urllib.request.urlopen(req, timeout=10).read()
        img = Image.open(io.BytesIO(raw)).convert("RGBA")
        img = img.resize((LOGO_SIZE, LOGO_SIZE), Image.LANCZOS)
        bg = Image.new("RGBA", (LOGO_SIZE, LOGO_SIZE), (0, 0, 0, 255))
        bg.paste(img, (0, 0), img)
        img = bg.convert("RGB")
        img.save(cache_file)

    pixels = []
    for y in range(img.height):
        for x in range(img.width):
            r, g, b = img.getpixel((x, y))
            r, g, b = int(r * 0.5), int(g * 0.5), int(b * 0.5)
            if r > 2 or g > 2 or b > 2:
                pixels.append((x, y, r, g, b))
    return pixels


def _get_logo(league: str, abbr: str, url: str | None) -> list | None:
    """Get cached logo pixels, downloading if needed."""
    key = f"{league}_{abbr}"
    if key in _logo_cache:
        return _logo_cache[key]
    if not url:
        return None
    try:
        pixels = _download_logo(url, league, abbr)
        _logo_cache[key] = pixels
        return pixels
    except Exception as e:
        print(f"[sports] Logo download failed for {abbr}: {e}")
        return None


def _get_team_logo_url(team: dict) -> str | None:
    """Extract logo URL from an ESPN team object (handles both formats)."""
    if "logo" in team and isinstance(team["logo"], str):
        return team["logo"]
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
    return date_str, f"{hour}:{minute} {ampm}"


def _period_text(period: int, sport: str = "hockey") -> str:
    """Convert period number to sport-appropriate display text."""
    if sport == "hockey":
        if period > 3:
            return "OT"
        return f"P{period}"
    elif sport in ("basketball", "football"):
        if period > 4:
            return "OT"
        return f"Q{period}"
    elif sport == "baseball":
        return f"INN {period}"
    elif sport == "soccer":
        if period == 1:
            return "1H"
        elif period == 2:
            return "2H"
        return "ET"
    return f"P{period}"


def _fetch_scoreboard(sport: str, league: str) -> dict:
    """Fetch scoreboard with per-league caching (10s TTL)."""
    key = f"{sport}/{league}"
    now = time.monotonic()
    if key in _scoreboard_cache:
        ts, data = _scoreboard_cache[key]
        if now - ts < 10.0:
            return data
    url = f"{ESPN_BASE}/{sport}/{league}/scoreboard"
    data = _fetch_json(url)
    _scoreboard_cache[key] = (now, data)
    return data


# ---------------------------------------------------------------------------
# Favorites config
# ---------------------------------------------------------------------------

def _load_favorites() -> list[dict]:
    if not _config_path.exists():
        return []
    try:
        return json.loads(_config_path.read_text())
    except Exception:
        return []


def _save_favorites(favorites: list[dict]) -> None:
    _config_path.write_text(json.dumps(favorites, indent=2))


# ---------------------------------------------------------------------------
# Terminal setup
# ---------------------------------------------------------------------------

def _fetch_teams_list(sport: str, league: str) -> list[dict]:
    """Fetch all teams for a league from ESPN."""
    url = f"{ESPN_BASE}/{sport}/{league}/teams"
    data = _fetch_json(url)
    raw = data["sports"][0]["leagues"][0]["teams"]
    teams = []
    for item in raw:
        t = item["team"]
        logo_url = _get_team_logo_url(t)
        teams.append({
            "id": t["id"],
            "abbr": t.get("abbreviation", ""),
            "name": t.get("displayName", ""),
            "color": t.get("color", "808080"),
            "alt_color": t.get("alternateColor", "404040"),
            "logo_url": logo_url or "",
        })
    teams.sort(key=lambda x: x["abbr"])
    return teams


def _show_favorites(favorites: list[dict]) -> None:
    if not favorites:
        print("\n  No favorites yet.")
        return
    print(f"\n  Current favorites ({len(favorites)}):")
    for i, f in enumerate(favorites):
        league = f["league"].upper()
        print(f"    {i + 1}. [{league:4}] {f['abbr']:4} {f['name']}")


def _run_setup(existing: list[dict] | None = None) -> list[dict]:
    """Interactive terminal setup. Returns list of favorite dicts."""
    favorites = list(existing) if existing else []

    print("\n  SPORTS TRACKER - FAVORITES SETUP")
    print("  " + "=" * 34)

    while True:
        _show_favorites(favorites)

        print("\n  Leagues:")
        for i, (_, _, short, full) in enumerate(LEAGUES):
            print(f"    {i + 1}) {short:5} {full}")

        choice = input("\n  Pick a league (or 'q' to finish): ").strip()
        if choice.lower() == "q":
            break

        try:
            idx = int(choice) - 1
            if not 0 <= idx < len(LEAGUES):
                raise ValueError
        except ValueError:
            print("  Invalid choice.")
            continue

        sport, league, short, _ = LEAGUES[idx]
        print(f"\n  Fetching {short} teams...")

        try:
            teams = _fetch_teams_list(sport, league)
        except Exception as e:
            print(f"  Error fetching teams: {e}")
            continue

        print(f"\n  {short} Teams:")
        for i, t in enumerate(teams):
            marker = "*" if any(
                f["league"] == league and f["team_id"] == t["id"]
                for f in favorites
            ) else " "
            print(f"   {marker}{i + 1:3}) {t['abbr']:4} {t['name']}")

        print("\n  * = already in favorites")
        pick = input("  Pick a team number (or 'b' to go back): ").strip()
        if pick.lower() == "b":
            continue

        try:
            tidx = int(pick) - 1
            if not 0 <= tidx < len(teams):
                raise ValueError
        except ValueError:
            print("  Invalid choice.")
            continue

        team = teams[tidx]

        # Check duplicate
        if any(f["league"] == league and f["team_id"] == team["id"] for f in favorites):
            print(f"  {team['abbr']} is already in favorites.")
            continue

        fav = {
            "sport": sport,
            "league": league,
            "team_id": team["id"],
            "abbr": team["abbr"],
            "name": team["name"].upper(),
            "color": team["color"],
            "alt_color": team["alt_color"],
            "logo_url": team["logo_url"],
        }
        favorites.append(fav)
        print(f"\n  Added: {team['abbr']} - {team['name']}")

    if favorites:
        _save_favorites(favorites)
        print(f"\n  Saved {len(favorites)} favorites.")
    else:
        print("\n  No favorites added.")

    return favorites


def _run_remove(favorites: list[dict]) -> list[dict]:
    """Remove a team from favorites interactively."""
    if not favorites:
        print("  No favorites to remove.")
        return favorites

    _show_favorites(favorites)
    pick = input("\n  Pick a number to remove (or 'q' to cancel): ").strip()
    if pick.lower() == "q":
        return favorites

    try:
        idx = int(pick) - 1
        if not 0 <= idx < len(favorites):
            raise ValueError
    except ValueError:
        print("  Invalid choice.")
        return favorites

    removed = favorites.pop(idx)
    _save_favorites(favorites)
    print(f"  Removed: {removed['abbr']} - {removed['name']}")
    return favorites


# ---------------------------------------------------------------------------
# Game data
# ---------------------------------------------------------------------------

def _make_game_data(fav: dict) -> dict:
    return {
        "state": "loading",
        "home_abbr": "",
        "away_abbr": "",
        "home_score": "",
        "away_score": "",
        "home_logo": None,
        "away_logo": None,
        "detail": "",
        "game_date": "",
        "game_time": "",
        "period": 0,
        "clock": "",
        "period_text": "",
        "status_detail": "",
        "our_logo": None,
        "our_abbr": fav["abbr"],
        "our_name": fav["name"],
        "sport": fav["sport"],
        "team_color": _hex_to_led(fav["color"], 0.35),
        "team_accent": _hex_to_led(fav["alt_color"], 0.35),
        "updated": 0.0,
    }


def _data_key(fav: dict) -> str:
    return f"{fav['league']}_{fav['team_id']}"


# ---------------------------------------------------------------------------
# Data fetching thread
# ---------------------------------------------------------------------------

def _poll_one_team(fav: dict, gd: dict) -> int:
    """Poll ESPN for one team's game data. Returns suggested poll interval."""
    sport, league = fav["sport"], fav["league"]
    team_url = f"{ESPN_BASE}/{sport}/{league}/teams/{fav['team_id']}"
    team_abbr = fav["abbr"]
    poll = POLL_NORMAL

    # Fetch own logo if missing
    if gd["our_logo"] is None:
        gd["our_logo"] = _get_logo(league, team_abbr, fav.get("logo_url"))

    data = _fetch_json(team_url)
    team_info = data.get("team", {})
    next_events = team_info.get("nextEvent", [])

    if not next_events:
        gd["state"] = "none"
        gd["updated"] = time.monotonic()
        return poll

    event = next_events[0]
    competition = event.get("competitions", [{}])[0]
    status = competition.get("status", {})
    status_type = status.get("type", {})
    state = status_type.get("state", "pre")
    competitors = competition.get("competitors", [])

    # Identify home/away
    home_team, away_team = {}, {}
    home_comp, away_comp = {}, {}
    for c in competitors:
        if c.get("homeAway") == "home":
            home_team = c.get("team", {})
            home_comp = c
        else:
            away_team = c.get("team", {})
            away_comp = c

    # Ensure logos
    for c in competitors:
        t = c.get("team", {})
        a = t.get("abbreviation", "")
        url = _get_team_logo_url(t)
        if a and url:
            _get_logo(league, a, url)

    home_abbr = home_team.get("abbreviation", "")
    away_abbr = away_team.get("abbreviation", "")
    gd["home_abbr"] = home_abbr
    gd["away_abbr"] = away_abbr
    gd["home_logo"] = _logo_cache.get(f"{league}_{home_abbr}")
    gd["away_logo"] = _logo_cache.get(f"{league}_{away_abbr}")

    if state == "pre":
        game_date, game_time = _format_game_time(event.get("date", ""))
        gd["game_date"] = game_date
        gd["game_time"] = game_time
        gd["detail"] = status_type.get("shortDetail", "")
        gd["state"] = "pre"
        gd["updated"] = time.monotonic()

    elif state in ("in", "post"):
        try:
            sb = _fetch_scoreboard(sport, league)
            for ev in sb.get("events", []):
                comp = ev.get("competitions", [{}])[0]
                comps = comp.get("competitors", [])
                abbrs = [c.get("team", {}).get("abbreviation", "") for c in comps]
                if team_abbr in abbrs:
                    for c in comps:
                        t = c.get("team", {})
                        a = t.get("abbreviation", "")
                        url = _get_team_logo_url(t)
                        if a and url:
                            _get_logo(league, a, url)
                        if c.get("homeAway") == "home":
                            gd["home_abbr"] = a
                            gd["home_score"] = c.get("score", "0")
                            gd["home_logo"] = _logo_cache.get(f"{league}_{a}")
                        else:
                            gd["away_abbr"] = a
                            gd["away_score"] = c.get("score", "0")
                            gd["away_logo"] = _logo_cache.get(f"{league}_{a}")

                    sb_status = comp.get("status", {})
                    sb_type = sb_status.get("type", {})
                    period = sb_status.get("period", 0)
                    gd["period"] = period
                    gd["clock"] = sb_status.get("displayClock", "")
                    gd["period_text"] = _period_text(period, sport)
                    gd["status_detail"] = sb_type.get("detail", "")
                    gd["detail"] = sb_type.get("shortDetail", "")
                    gd["state"] = sb_type.get("state", state)
                    break
            else:
                gd["home_score"] = home_comp.get("score", "0")
                gd["away_score"] = away_comp.get("score", "0")
                gd["state"] = state
        except Exception:
            gd["state"] = state

        gd["updated"] = time.monotonic()
        if state == "in":
            poll = POLL_LIVE

    return poll


def _fetch_loop(favorites: list[dict]) -> None:
    """Single thread polls all favorites on staggered schedule."""
    n = len(favorites)
    now = time.monotonic()

    # Initialize game_data and build staggered schedule
    schedule = []
    for i, fav in enumerate(favorites):
        key = _data_key(fav)
        all_game_data[key] = _make_game_data(fav)
        stagger = (i / max(n, 1)) * 5.0
        schedule.append([now + stagger, fav])

    while True:
        schedule.sort(key=lambda x: x[0])
        entry = schedule[0]
        next_time, fav = entry

        wait = next_time - time.monotonic()
        if wait > 0:
            time.sleep(wait)

        key = _data_key(fav)
        gd = all_game_data[key]

        try:
            poll = _poll_one_team(fav, gd)
        except Exception as e:
            print(f"[sports] Fetch error for {fav['abbr']}: {e}")
            poll = POLL_NORMAL

        entry[0] = time.monotonic() + poll

        # Enforce minimum gap between fetches
        time.sleep(MIN_FETCH_GAP)


# ---------------------------------------------------------------------------
# Button listener
# ---------------------------------------------------------------------------

def _create_button_listener():
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("0.0.0.0", BUTTON_PORT))
        sock.setblocking(False)
        return sock
    except OSError as e:
        print(f"[sports] Could not bind button listener on port {BUTTON_PORT}: {e}")
        return None


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


def _abbr_under_logo(canvas: Canvas, abbr: str, logo_x: int, y: int,
                     color) -> None:
    w = len(abbr) * 4 - 1
    x = logo_x + (LOGO_SIZE - w) // 2
    canvas.text(max(0, x), y, abbr, color)


def _draw_status_dot(canvas: Canvas, gd: dict, t: float) -> None:
    age = t - gd["updated"] if gd["updated"] else 999
    if gd["state"] == "loading":
        b = int(40 + 40 * abs(math.sin(t * 2)))
        canvas.set(1, 62, (b, b, 0))
    elif age < 120:
        b = int(30 + 30 * abs(math.sin(t * 2)))
        canvas.set(1, 62, (0, b, 0))
    else:
        b = int(40 + 40 * abs(math.sin(t * 2)))
        canvas.set(1, 62, (b, b // 2, 0))


def _abbr_color(abbr: str, gd: dict) -> tuple[int, int, int]:
    return gd["team_color"] if abbr == gd["our_abbr"] else DIM_WHITE


# ---------------------------------------------------------------------------
# Display modes
# ---------------------------------------------------------------------------

def _draw_loading(canvas: Canvas, gd: dict, t: float) -> None:
    _centered_text(canvas, 28, "LOADING", DIM_GRAY)
    dots = "." * (int(t * 2) % 4)
    _centered_text(canvas, 35, dots, DIM_GRAY)


def _draw_no_game(canvas: Canvas, gd: dict, t: float) -> None:
    if gd["our_logo"]:
        cx = (64 - LOGO_SIZE) // 2
        _draw_logo(canvas, gd["our_logo"], cx, LOGO_Y)

    _centered_text(canvas, ABBR_Y, gd["our_name"], gd["team_color"])
    _centered_text(canvas, 42, "NO GAME", DIM_GRAY)
    _centered_text(canvas, 49, "SCHEDULED", DIM_GRAY)


def _draw_pre_game(canvas: Canvas, gd: dict, t: float) -> None:
    _draw_logo(canvas, gd["away_logo"], AWAY_LOGO_X, LOGO_Y)
    _draw_logo(canvas, gd["home_logo"], HOME_LOGO_X, LOGO_Y)

    _centered_text(canvas, 9, "AT", DIM_GRAY, x_min=24, x_max=39)

    away = gd["away_abbr"]
    home = gd["home_abbr"]
    _abbr_under_logo(canvas, away, AWAY_LOGO_X, ABBR_Y, _abbr_color(away, gd))
    _abbr_under_logo(canvas, home, HOME_LOGO_X, ABBR_Y, _abbr_color(home, gd))


    _centered_text(canvas, 40, gd["game_date"], WHITE)
    _centered_text(canvas, 47, gd["game_time"], WHITE)


def _draw_live_game(canvas: Canvas, gd: dict, t: float) -> None:
    _draw_logo(canvas, gd["away_logo"], AWAY_LOGO_X, LOGO_Y)
    _draw_logo(canvas, gd["home_logo"], HOME_LOGO_X, LOGO_Y)

    pulse = int(50 + 50 * abs(math.sin(t * 3)))
    canvas.set(31, 10, (pulse, 5, 5))
    canvas.set(32, 10, (pulse, 5, 5))

    away = gd["away_abbr"]
    home = gd["home_abbr"]
    _abbr_under_logo(canvas, away, AWAY_LOGO_X, ABBR_Y, _abbr_color(away, gd))
    _abbr_under_logo(canvas, home, HOME_LOGO_X, ABBR_Y, _abbr_color(home, gd))


    away_line = f"{away:3} {gd['away_score']:>2}"
    home_line = f"{home:3} {gd['home_score']:>2}"
    _centered_text(canvas, 40, away_line, _abbr_color(away, gd))
    _centered_text(canvas, 47, home_line, _abbr_color(home, gd))

    period_clock = f"{gd['period_text']} {gd['clock']}"
    _centered_text(canvas, 53, period_clock, AMBER)

    if int(t * 2) % 2:
        _centered_text(canvas, 59, "LIVE", LIVE_RED)


def _draw_final(canvas: Canvas, gd: dict, t: float) -> None:
    _draw_logo(canvas, gd["away_logo"], AWAY_LOGO_X, LOGO_Y)
    _draw_logo(canvas, gd["home_logo"], HOME_LOGO_X, LOGO_Y)

    away = gd["away_abbr"]
    home = gd["home_abbr"]
    _abbr_under_logo(canvas, away, AWAY_LOGO_X, ABBR_Y, _abbr_color(away, gd))
    _abbr_under_logo(canvas, home, HOME_LOGO_X, ABBR_Y, _abbr_color(home, gd))


    detail = gd.get("status_detail", "Final")
    if "OT" in detail:
        final_text = "FINAL OT"
    elif "SO" in detail:
        final_text = "FINAL SO"
    else:
        final_text = "FINAL"
    _centered_text(canvas, 41, final_text, DIM_WHITE)

    away_line = f"{away:3} {gd['away_score']:>2}"
    home_line = f"{home:3} {gd['home_score']:>2}"
    _centered_text(canvas, 48, away_line, _abbr_color(away, gd))
    _centered_text(canvas, 55, home_line, _abbr_color(home, gd))


def _render_game(canvas: Canvas, gd: dict, t: float) -> None:
    state = gd["state"]
    if state == "loading":
        _draw_loading(canvas, gd, t)
    elif state == "none":
        _draw_no_game(canvas, gd, t)
    elif state == "pre":
        _draw_pre_game(canvas, gd, t)
    elif state == "in":
        _draw_live_game(canvas, gd, t)
    elif state == "post":
        _draw_final(canvas, gd, t)
    _draw_status_dot(canvas, gd, t)


def _draw_overlay(canvas: Canvas, fav: dict, index: int, total: int) -> None:
    """Team name overlay shown when switching."""
    canvas.rect(0, 24, 64, 16, (0, 0, 0), filled=True)
    color = _hex_to_led(fav["color"], 0.5)
    _centered_text(canvas, 27, fav["abbr"], color)
    league = fav["league"].upper()
    _centered_text(canvas, 34, f"{league} {index + 1}/{total}", DIM_GRAY)


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def _submenu() -> list[dict]:
    """Interactive submenu: manage favorites, then confirm to start display."""
    favorites = _load_favorites()

    if not favorites:
        print("  No favorites yet. Let's add some teams.\n")
        favorites = _run_setup()
        if not favorites:
            print("  No favorites added.")
            sys.exit(1)

    while True:
        print("\n  SPORTS TRACKER")
        print("  " + "=" * 30)
        _show_favorites(favorites)
        print("\n  Options:")
        print("    a) Add a team")
        print("    r) Remove a team")
        print("    s) Start display")
        print("    q) Quit")

        choice = input("\n  Choice: ").strip().lower()

        if choice == "a":
            favorites = _run_setup(existing=favorites)
        elif choice == "r":
            favorites = _run_remove(favorites)
        elif choice == "s":
            if not favorites:
                print("  Add at least one team first.")
                continue
            break
        elif choice == "q":
            sys.exit(0)
        else:
            print("  Invalid choice.")

    return favorites


def main():
    favorites = _submenu()

    print(f"\n  Starting tracker for {len(favorites)} teams...")
    print()

    # Pre-warm own logos
    for fav in favorites:
        _get_logo(fav["league"], fav["abbr"], fav.get("logo_url"))

    # Initialize game data before starting fetch thread
    for fav in favorites:
        key = _data_key(fav)
        all_game_data[key] = _make_game_data(fav)
        all_game_data[key]["our_logo"] = _logo_cache.get(f"{fav['league']}_{fav['abbr']}")

    # Start fetch thread
    threading.Thread(target=_fetch_loop, args=(favorites,), daemon=True).start()

    # Display loop
    canvas = Canvas()
    sim = Simulator(canvas, title="Sports Tracker")
    sender = Sender()
    btn_sock = _create_button_listener()

    current = 0
    overlay_until = time.monotonic() + OVERLAY_DURATION
    last_switch = time.monotonic()
    key_up_prev = False
    key_down_prev = False
    start = time.monotonic()
    frame = 0

    try:
        while True:
            t = time.monotonic() - start
            now = time.monotonic()

            # --- Button / keyboard input ---
            switched = False
            if btn_sock is not None:
                try:
                    while True:
                        data, _ = btn_sock.recvfrom(16)
                        if len(data) >= 1:
                            if data[0] == BTN_UP_CODE:
                                current = (current - 1) % len(favorites)
                                switched = True
                            elif data[0] == BTN_DOWN_CODE:
                                current = (current + 1) % len(favorites)
                                switched = True
                except BlockingIOError:
                    pass

            keys = pygame.key.get_pressed()
            if keys[pygame.K_UP] and not key_up_prev:
                current = (current - 1) % len(favorites)
                switched = True
            if keys[pygame.K_DOWN] and not key_down_prev:
                current = (current + 1) % len(favorites)
                switched = True
            key_up_prev = keys[pygame.K_UP]
            key_down_prev = keys[pygame.K_DOWN]

            if not switched and len(favorites) > 1 and now - last_switch >= AUTO_ROTATE:
                current = (current + 1) % len(favorites)
                switched = True

            if switched:
                overlay_until = now + OVERLAY_DURATION
                last_switch = now

            # --- Render current team ---
            fav = favorites[current]
            key = _data_key(fav)
            gd = all_game_data.get(key, _make_game_data(fav))

            canvas.clear()
            _render_game(canvas, gd, t)

            if now < overlay_until:
                _draw_overlay(canvas, fav, current, len(favorites))

            # --- Update display ---
            if not sim.update():
                break
            sender.send_frame(canvas)
            sim.tick(10)
            frame += 1

    except KeyboardInterrupt:
        pass
    finally:
        if btn_sock is not None:
            btn_sock.close()
        sender.close()
        sim.close()


if __name__ == "__main__":
    main()
