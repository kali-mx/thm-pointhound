#!/usr/bin/env python3
"""
THM PointHound — rank your uncompleted TryHackMe labs by point value.

Usage:
    python thm-pointhound.py <username> [--top N] [--difficulty TIER] [--min-points P]
                             [--no-cache] [--cookie SID]
"""

import re
import sys
import time
import random
import json
import argparse
from pathlib import Path
from statistics import mode, StatisticsError
from datetime import datetime, timedelta

import cloudscraper
from rich.console import Console
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, MofNCompleteColumn
from rich.panel import Panel
from rich import box

console = Console()

BASE_URL          = "https://tryhackme.com/api/v2"
ROOMS_SITEMAP_URL = "https://tryhackme.com/sitemaps/rooms.xml"

CACHE_DIR          = Path.home() / ".cache" / "thm_pointhound"
SITEMAP_CACHE      = CACHE_DIR / "sitemap_codes.json"
DETAILS_CACHE      = CACHE_DIR / "room_details.json"
OVERRIDES_FILE     = CACHE_DIR / "manual_done.json"
SITEMAP_TTL_HOURS  = 24      # re-fetch sitemap daily
DETAILS_SAVE_EVERY = 50      # persist cache every N rooms fetched
POINTS_TTL_DAYS    = 7       # re-fetch scoreboard after this many days
TODAY              = datetime.now().date()

HEADERS = {
    "accept": "application/json, text/plain, */*",
    "accept-language": "en-US,en;q=0.9",
    "referer": "https://tryhackme.com/",
    "user-agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
}
HTML_HEADERS = {**HEADERS, "accept": "text/html,application/xhtml+xml,*/*;q=0.8"}

DIFF_COLOR = {
    "easy": "green", "medium": "yellow", "hard": "red",
    "insane": "magenta", "info": "blue",
}


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def make_scraper(session_cookie=None):
    s = cloudscraper.create_scraper(
        browser={"browser": "chrome", "platform": "windows", "mobile": False}
    )
    if session_cookie:
        s.cookies.set("connect.sid", session_cookie, domain="tryhackme.com")
    return s


def safe_get(scraper, url, retries=3, base_delay=2.0):
    for attempt in range(retries):
        try:
            resp = scraper.get(url, headers=HEADERS, timeout=20)
            if resp.status_code == 200:
                if not resp.text.strip():
                    return None
                try:
                    return resp.json()
                except Exception:
                    return None
            if resp.status_code == 429:
                delay = base_delay * (2 ** attempt) + random.uniform(1, 3)
                console.print(f"[yellow]Rate limited — waiting {delay:.1f}s[/yellow]")
                time.sleep(delay)
                continue
            return None
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(base_delay)
    return None


# ---------------------------------------------------------------------------
# Platform stats + SOC scenarios
# ---------------------------------------------------------------------------

def fetch_site_stats(scraper):
    data = safe_get(scraper, f"{BASE_URL}/site-stats")
    if data and data.get("status") == "success":
        return data.get("data", {})
    return {}


def fetch_soc_scenarios(scraper):
    data = safe_get(scraper, f"{BASE_URL}/soc-sim/content/scenarios")
    if data and data.get("status") == "success":
        scenarios = data.get("data", [])
        if isinstance(scenarios, list):
            return sorted(scenarios, key=lambda s: s.get("experience_points", 0), reverse=True)
    return []


def print_platform_banner(stats):
    total_labs  = stats.get("totalHandsOnLabs", "?")
    total_users = stats.get("totalUsers", 0)
    total_paths = stats.get("totalPathsCount", "?")
    premium     = stats.get("totalPremiumUsers", 0)
    console.print(Panel(
        f"[bold cyan]TryHackMe Platform Stats[/bold cyan]\n"
        f"  Hands-on labs  : [bold yellow]{total_labs}[/bold yellow]\n"
        f"  Learning paths : [bold]{total_paths}[/bold]\n"
        f"  Total users    : {total_users:,}\n"
        f"  Premium users  : {premium:,}",
        expand=False, border_style="dim"
    ))
    console.print()


# ---------------------------------------------------------------------------
# Room codes — from the rooms sitemap
# ---------------------------------------------------------------------------

def _load_json(path: Path):
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def get_room_codes(scraper, force=False) -> tuple[list[str], dict[str, str]]:
    """
    Return (ordered_codes, lastmod_by_code) from the THM rooms sitemap.
    lastmod_by_code maps code → ISO date string (e.g. "2023-04-12"), empty str if absent.
    Cached for 24 h.
    """
    if not force and SITEMAP_CACHE.exists():
        payload = _load_json(SITEMAP_CACHE)
        if payload:
            cached_at = datetime.fromisoformat(payload["cached_at"])
            if datetime.now() - cached_at < timedelta(hours=SITEMAP_TTL_HOURS):
                codes = payload["codes"]
                lastmod = payload.get("lastmod", {})
                console.print(
                    f"[dim]Sitemap cache: {len(codes)} room codes "
                    f"(refreshes every {SITEMAP_TTL_HOURS}h)[/dim]"
                )
                return codes, lastmod

    console.print(f"[dim]Fetching {ROOMS_SITEMAP_URL}…[/dim]")
    try:
        resp = scraper.get(ROOMS_SITEMAP_URL, headers=HTML_HEADERS, timeout=30)
    except Exception as e:
        console.print(f"[red]Sitemap fetch failed: {e}[/red]")
        return [], {}

    if resp.status_code != 200:
        console.print(f"[red]Sitemap returned HTTP {resp.status_code}[/red]")
        return [], {}

    xml = resp.text
    # Parse <url> blocks: extract code from <loc> and date from <lastmod>
    codes: list[str] = []
    lastmod: dict[str, str] = {}
    for block in re.finditer(r'<url>(.*?)</url>', xml, re.DOTALL):
        loc_m  = re.search(r'tryhackme\.com/room/([a-zA-Z0-9_\-]+)', block.group(1))
        date_m = re.search(r'<lastmod>([^<]+)</lastmod>', block.group(1))
        if loc_m:
            code = loc_m.group(1)          # preserve original case — AoC codes are mixed-case
            if code.lower() not in {k.lower() for k in lastmod}:   # dedupe case-insensitively
                codes.append(code)
                lastmod[code] = date_m.group(1).strip()[:10] if date_m else ""

    console.print(f"[green]✓[/green] {len(codes)} room codes from sitemap")

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    SITEMAP_CACHE.write_text(json.dumps({
        "cached_at": datetime.now().isoformat(),
        "codes": codes,
        "lastmod": lastmod,
    }))
    return codes, lastmod


# ---------------------------------------------------------------------------
# Room details — fetched per-room, cached indefinitely
# ---------------------------------------------------------------------------

def load_details_cache() -> dict:
    if DETAILS_CACHE.exists():
        payload = _load_json(DETAILS_CACHE)
        if isinstance(payload, dict):
            return payload
    return {}


def save_details_cache(cache: dict):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    DETAILS_CACHE.write_text(json.dumps(cache))


def _points_stale(entry: dict) -> bool:
    fetched = entry.get("points_fetched_at")
    if not fetched:
        return True
    try:
        return (datetime.now() - datetime.fromisoformat(fetched)).days >= POINTS_TTL_DAYS
    except Exception:
        return True


def _parse_room_details(data: dict) -> tuple[str, str, str, int]:
    """Return (name, difficulty, release_date_iso, score_type) from a rooms/details API response.
    score_type 1 = fixed-value walkthrough; 0 = per-flag challenge room.
    """
    d = data.get("data", {})
    name       = d.get("title") or d.get("name") or ""
    diff       = str(d.get("difficulty") or "unknown").lower()
    score_type = int(d.get("scoreType", 1))
    release    = ""
    for key in ("createdAt", "created", "publishedAt", "releaseDate", "addedAt", "updatedAt"):
        v = d.get(key)
        if v and isinstance(v, str) and len(v) >= 10:
            release = v[:10]
            break
    return name, diff, release, score_type


# Patterns in THM room RSC/HTML that confirm the *current user* finished the room.
# Deliberately conservative — "completed":true at field level, or the exact
# progress-bar phrase THM renders. Avoid broad matches that appear in descriptions.
_DONE_PATTERNS = [
    rb'"isCompleted"\s*:\s*true',
    rb'"userCompleted"\s*:\s*true',
    rb'"percentComplete"\s*:\s*100\b',
    rb'Room\s+completed\s*\(\s*100\s*%\s*\)',   # exact banner text from UI
]


def verify_room_completion(scraper, code: str) -> bool:
    """
    Fetch the room page with the authenticated session and scan for completion
    signals in both the RSC payload and raw HTML.
    Requires a valid connect.sid cookie in the scraper session.
    """
    url = f"https://tryhackme.com/room/{code}"
    for hdrs in [
        # RSC stream — structured, faster to scan
        {**HEADERS, "accept": "text/x-component", "RSC": "1",
         "Next-Router-Prefetch": "1", "Next-Url": f"/room/{code}"},
        # Full HTML — fallback
        HTML_HEADERS,
    ]:
        try:
            resp = scraper.get(url, headers=hdrs, timeout=20)
            body = resp.content          # bytes — avoid re-encoding issues
            for pat in _DONE_PATTERNS:
                if re.search(pat, body, re.IGNORECASE):
                    return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


def days_old(date_iso: str) -> str:
    """Convert an ISO date string to a human-readable age (e.g. '847d'). Returns '' if blank."""
    if not date_iso:
        return ""
    try:
        d = datetime.strptime(date_iso[:10], "%Y-%m-%d").date()
        return f"{(TODAY - d).days}d"
    except ValueError:
        return ""


def _scoreboard_points(scraper, code: str, score_type: int = 1) -> int:
    """
    Fetch the scoreboard for a room and return the current point value.

    score_type 1 (fixed-value walkthrough): use min() so point nerfs are caught
    immediately. Old completions keep their historical score, inflating mode/max,
    but new completions get the lower current value -- min() reflects that.

    score_type 0 (per-flag challenge room): scores vary by how many flags a user
    solved, so min() would just be a partial completion. mode() captures the most
    common full-completion score instead.
    """
    url = f"{BASE_URL}/rooms/scoreboard?roomCode={code}&limit=100"
    data = safe_get(scraper, url)
    if not (data and data.get("status") == "success"):
        return 0
    scores = [
        int(u.get("score", 0))
        for u in (data.get("data") or [])
        if u.get("score", 0) and int(u.get("score", 0)) > 0
    ]
    if not scores:
        return 0
    if score_type == 1:
        return min(scores)
    try:
        return mode(scores)
    except StatisticsError:
        return max(scores)


def get_all_room_details(scraper, codes: list[str], sitemap_dates: dict[str, str] | None = None, force=False) -> dict:
    """
    Return {code: {name, difficulty, points, points_fetched_at}} for every code.
    - missing: no cache entry or placeholder retry -> full fetch (details + scoreboard)
    - stale:   cached but points older than POINTS_TTL_DAYS -> scoreboard-only refresh
    Saves incrementally every DETAILS_SAVE_EVERY rooms.
    """
    cache = {} if force else load_details_cache()

    missing = [
        c for c in codes
        if c not in cache
        or (cache[c].get("name") == c and cache[c].get("points") == 0)
    ]
    stale = [
        c for c in codes
        if c not in missing and not cache.get(c, {}).get("placeholder") and _points_stale(cache.get(c, {}))
    ]
    to_fetch = missing + stale

    if not to_fetch:
        return cache

    stale_set = set(stale)
    parts = []
    if missing:
        parts.append(f"{len(missing)} new")
    if stale:
        parts.append(f"{len(stale)} refreshing points (>{POINTS_TTL_DAYS}d old)")
    cached_count = len(codes) - len(to_fetch)
    console.print(
        f"\n[bold]Fetching room data:[/bold] {', '.join(parts)}"
        f"  [dim](cached: {cached_count})[/dim]"
    )

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        console=console,
    ) as prog:
        task = prog.add_task(f"Fetching details for {len(to_fetch)} uncompleted rooms…", total=len(to_fetch))

        for i, code in enumerate(to_fetch):
            if code in stale_set:
                # Points-only refresh — reuse cached name/difficulty/release/score_type
                score_type = cache[code].get("score_type", 1)
                points = _scoreboard_points(scraper, code, score_type)
                cache[code]["points"] = points
                cache[code]["points_fetched_at"] = datetime.now().isoformat()
            else:
                # Full fetch: details + scoreboard
                details_data = safe_get(scraper, f"{BASE_URL}/rooms/details?roomCode={code}")
                if details_data and details_data.get("status") == "success":
                    name, diff, api_date, score_type = _parse_room_details(details_data)
                    release = api_date or (sitemap_dates or {}).get(code, "")
                    is_placeholder = False
                else:
                    name, diff, release, score_type = code, "unknown", (sitemap_dates or {}).get(code, ""), 1
                    is_placeholder = True

                time.sleep(0.4 + random.uniform(0, 0.2))

                points = _scoreboard_points(scraper, code, score_type) if not is_placeholder else 0
                cache[code] = {
                    "name":              name,
                    "difficulty":        diff,
                    "points":            points,
                    "release":           release,
                    "placeholder":       is_placeholder,
                    "score_type":        score_type,
                    "points_fetched_at": datetime.now().isoformat(),
                }

            prog.advance(task)

            if (i + 1) % DETAILS_SAVE_EVERY == 0:
                save_details_cache(cache)

            time.sleep(0.4 + random.uniform(0, 0.2))

    save_details_cache(cache)
    return cache


# ---------------------------------------------------------------------------
# User's completed rooms
# ---------------------------------------------------------------------------

def load_overrides() -> set[str]:
    """Load manually-marked-done room codes from the local override file."""
    if OVERRIDES_FILE.exists():
        data = _load_json(OVERRIDES_FILE)
        if isinstance(data, list):
            return {c.lower() for c in data}
    return set()


def save_overrides(codes: set[str]):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    OVERRIDES_FILE.write_text(json.dumps(sorted(codes)))


def get_completed_rooms(scraper, username: str, debug: bool = False) -> tuple[set[str], set[str]]:
    """
    Returns (completed_codes, completed_titles) — both lowercased.
    We match on both because THM occasionally changes room codes; title is more stable.
    """
    url = f"{BASE_URL}/public-profile/completed-rooms?username={username}&limit=10000&page=1"
    data = safe_get(scraper, url)
    if not data:
        console.print(
            f"[red]Failed to fetch completed rooms for '{username}'. "
            "Is the profile public?[/red]"
        )
        sys.exit(1)
    docs = (data.get("data") or {}).get("docs", [])

    if debug and docs:
        console.print("\n[bold yellow]DEBUG — raw completed-room doc fields (first 3):[/bold yellow]")
        for doc in docs[:3]:
            console.print(f"  keys: {list(doc.keys())}")
            console.print(f"  sample: {dict(list(doc.items())[:6])}")
        console.print("\n[bold yellow]DEBUG — sample of completed codes (first 30):[/bold yellow]")
        sample_codes = sorted({r.get("code","").lower() for r in docs if r.get("code")})[:30]
        for i in range(0, len(sample_codes), 5):
            console.print("  " + "  ".join(sample_codes[i:i+5]))

    codes:  set[str] = set()
    titles: set[str] = set()
    for r in docs:
        for field in ("code", "roomCode", "slug", "roomSlug"):
            val = r.get(field)
            if val and isinstance(val, str):
                codes.add(val.lower())
        title = r.get("title")
        if title and isinstance(title, str):
            titles.add(title.lower())
    return codes, titles


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------

def styled_diff(diff: str) -> str:
    color = DIFF_COLOR.get(diff, "white")
    return f"[{color}]{diff.capitalize()}[/{color}]"


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        prog="thm-pointhound",
        description="Rank your uncompleted TryHackMe labs by point value.",
    )
    parser.add_argument("username",  help="TryHackMe username")
    parser.add_argument("--top",     type=int, default=50, metavar="N",
                        help="Show top N rooms (default: 50)")
    parser.add_argument("--difficulty", choices=["easy","medium","hard","insane","info"],
                        help="Filter by difficulty")
    parser.add_argument("--min-points", type=int, default=0, metavar="P",
                        help="Exclude rooms below P points")
    parser.add_argument("--no-cache", action="store_true",
                        help="Force fresh fetch of sitemap + room details")
    parser.add_argument("--cookie",  metavar="SID",
                        help="connect.sid cookie value (bypasses Vercel bot detection)")
    parser.add_argument("--debug",     action="store_true",
                        help="Print raw API response fields to diagnose matching issues")
    parser.add_argument("--mark-done", nargs="+", metavar="NAME_OR_CODE",
                        help="Mark room(s) as completed by name or code (bypasses API mismatch)")
    parser.add_argument("--list-done", action="store_true",
                        help="Show manually marked-done overrides and exit")
    parser.add_argument("--verify",   action="store_true",
                        help="For each uncompleted room, fetch its page and check for "
                             "'Room completed 100%%' — auto-saves any found to --mark-done")
    args = parser.parse_args()

    scraper = make_scraper(session_cookie=args.cookie)

    # Handle --mark-done / --list-done before hitting any API
    overrides = load_overrides()
    if args.mark_done:
        # Build name->code lookup from local cache so users can pass room names
        cache = load_details_cache()
        name_to_code = {
            d["name"].lower(): code
            for code, d in cache.items()
            if d.get("name") and not d.get("placeholder")
        }
        new_codes: set[str] = set()
        for inp in args.mark_done:
            inp_lower = inp.lower()
            if inp_lower in name_to_code:
                resolved = name_to_code[inp_lower]
                console.print(f"[dim]Resolved '{inp}' -> {resolved}[/dim]")
                new_codes.add(resolved)
            else:
                new_codes.add(inp_lower)
        overrides |= new_codes
        save_overrides(overrides)
        console.print(f"[green]✓[/green] Marked as done: {', '.join(sorted(new_codes))}")
        console.print(f"[dim]Total manual overrides: {len(overrides)}[/dim]")
        return
    if args.list_done:
        cache = load_details_cache()
        code_to_name = {code: d.get("name", code) for code, d in cache.items()}
        if overrides:
            console.print("[bold]Manually marked done:[/bold]")
            for c in sorted(overrides):
                label = code_to_name.get(c, c)
                console.print(f"  {label}  [dim]({c})[/dim]")
        else:
            console.print("[dim]No manual overrides set.[/dim]")
        return

    console.print(f"\n[bold cyan]THM Room Point Optimizer[/bold cyan]  "
                  f"[dim]user: {args.username}[/dim]\n")

    # 1. Platform stats banner
    site_stats = fetch_site_stats(scraper)
    if site_stats:
        print_platform_banner(site_stats)
    platform_total = site_stats.get("totalHandsOnLabs")

    # 2. All room codes from sitemap
    console.print("[bold]Loading room list from sitemap…[/bold]")
    all_codes, sitemap_dates = get_room_codes(scraper, force=args.no_cache)
    if not all_codes:
        console.print("[red]Could not retrieve room list from sitemap.[/red]")
        sys.exit(1)
    console.print(f"[green]✓[/green] {len(all_codes):,} rooms in sitemap"
                  + (f" (platform reports {platform_total})" if platform_total else "") + "\n")

    # 3. User's completed rooms
    console.print("[bold]Fetching completed rooms…[/bold]")
    completed_codes, completed_titles = get_completed_rooms(scraper, args.username, debug=args.debug)

    if args.debug:
        check = ["hydra", "sustah", "splunk2gcd5", "splunk 2", "hydra brute force"]
        console.print("\n[bold yellow]DEBUG — spot-check codes + titles in completed set:[/bold yellow]")
        for c in check:
            in_codes  = c in completed_codes
            in_titles = c in completed_titles
            status = "[green]code match[/green]" if in_codes else ("[blue]title match[/blue]" if in_titles else "[red]NOT FOUND[/red]")
            console.print(f"  '{c}' → {status}")
    console.print(f"[green]✓[/green] {len(completed_codes):,} labs completed\n")

    # Merge manual overrides into completed set
    if overrides:
        console.print(f"[dim]Applying {len(overrides)} manual override(s) (--mark-done)[/dim]")
        completed_codes |= overrides
        completed_titles |= {o.lower() for o in overrides}

    # 4. Identify uncompleted codes — match on code OR title (handles recodified rooms)
    uncompleted_codes = [c for c in all_codes if c.lower() not in completed_codes]

    # 5. Fetch / load details for all uncompleted rooms
    details = get_all_room_details(scraper, uncompleted_codes, sitemap_dates=sitemap_dates, force=args.no_cache)

    # 6. Build, filter, sort — skip placeholders and title-matched rooms
    uncompleted = []
    placeholder_rooms = []
    for code in uncompleted_codes:
        d = details.get(code, {})
        if d.get("placeholder"):
            placeholder_rooms.append({"code": code, "release": d.get("release", "")})
            continue
        name = d.get("name") or code
        diff   = d.get("difficulty", "unknown")
        points = d.get("points", 0)
        release = d.get("release", "")
        if args.difficulty and diff != args.difficulty:
            continue
        if points < args.min_points:
            continue
        uncompleted.append({
            "name": name, "code": code, "points": points,
            "difficulty": diff, "release": release,
        })

    uncompleted.sort(key=lambda x: x["points"], reverse=True)

    # 6b. Optional page-level verification — catches API-missed completions
    if args.verify:
        if not args.cookie:
            console.print("[yellow]--verify requires --cookie to check authenticated room pages.[/yellow]")
        else:
            console.print(
                f"\n[bold]Verifying {len(uncompleted)} uncompleted rooms against live pages…[/bold]\n"
                f"[dim]Checks each room page for 'Room completed (100%)' signal.[/dim]"
            )
            auto_done: list[str] = []
            with Progress(SpinnerColumn(), TextColumn("{task.description}"),
                          BarColumn(), MofNCompleteColumn(), console=console) as prog:
                task = prog.add_task("Checking rooms…", total=len(uncompleted))
                for room in list(uncompleted):
                    prog.update(task, description=f"Checking {room['name'][:30]}…")
                    if verify_room_completion(scraper, room["code"]):
                        auto_done.append(room["code"])
                        uncompleted.remove(room)
                    prog.advance(task)

            if auto_done:
                overrides |= set(auto_done)
                save_overrides(overrides)
                console.print(
                    f"\n[green]✓ Auto-marked as completed (saved to overrides):[/green] "
                    + ", ".join(auto_done)
                )
            else:
                console.print("[dim]No additional completions detected via page verification.[/dim]")

    # 7. Summary
    total_for_pct       = platform_total or len(all_codes)
    total_pts_remaining = sum(r["points"] for r in uncompleted)
    paywalled_count     = len(placeholder_rooms)
    effective_done      = len(completed_codes) + paywalled_count
    pct_done            = effective_done / total_for_pct * 100 if total_for_pct else 0

    console.print("\n[bold]Your Progress[/bold]")
    console.print(
        f"  Completed : [bold green]{effective_done:>4}[/bold green]"
        f" / {total_for_pct} hands-on labs  ({pct_done:.1f}%)"
    )
    if paywalled_count:
        console.print(
            f"  [dim]  └─ {len(completed_codes)} completed  ·  "
            f"{paywalled_count} paywalled (business plan)[/dim]"
        )
    console.print(f"  Remaining : [bold]{len(uncompleted):>4}[/bold] accessible labs")
    console.print(f"  Points available : [bold yellow]{total_pts_remaining:,}[/bold yellow]\n")

    # 8. Main table
    display = uncompleted[:args.top]
    if display:
        table = Table(
            title=f"Top {len(display)} Uncompleted Labs — Highest Points First",
            show_lines=False, header_style="bold", box=box.SIMPLE_HEAD,
        )
        table.add_column("#",          style="dim", width=4, justify="right")
        table.add_column("Lab Name",   min_width=32)
        table.add_column("Pts",        justify="right", style="bold yellow", width=6)
        table.add_column("Difficulty", width=10)
        table.add_column("Age",        width=7, justify="right", style="dim")
        table.add_column("URL",        style="dim cyan")

        for i, room in enumerate(display, 1):
            table.add_row(
                str(i),
                room["name"],
                str(room["points"]),
                styled_diff(room["difficulty"]),
                days_old(room.get("release", "")),
                f"https://tryhackme.com/room/{room['code']}",
            )
        console.print(table)
    else:
        console.print("[yellow]No uncompleted rooms match your current filters.[/yellow]")

    # 9. SOC Simulator scenarios
    console.print("\n[bold]Fetching SOC Simulator scenarios…[/bold]")
    scenarios = fetch_soc_scenarios(scraper)
    if scenarios:
        soc = Table(
            title=f"SOC Simulator Scenarios ({len(scenarios)}) — Ranked by XP",
            show_lines=False, header_style="bold", box=box.SIMPLE_HEAD,
        )
        soc.add_column("#",          style="dim", width=4, justify="right")
        soc.add_column("Scenario",   min_width=30)
        soc.add_column("XP",         justify="right", style="bold yellow", width=6)
        soc.add_column("Difficulty", width=10)
        soc.add_column("Age",        width=7, justify="right", style="dim")

        for i, sc in enumerate(scenarios, 1):
            diff = str(sc.get("difficulty", "unknown")).lower()
            created = (sc.get("_createdAt") or sc.get("createdAt") or "")[:10]
            soc.add_row(
                str(i), sc.get("title", "Unknown"),
                str(sc.get("experience_points", 0)),
                styled_diff(diff),
                days_old(created),
            )
        console.print(soc)
        console.print(
            "[dim]SOC Simulator requires a premium subscription. "
            "Access at: https://tryhackme.com/soc-sim[/dim]\n"
        )

    if placeholder_rooms:
        plh = Table(
            title=f"Unlisted / Unreleased Rooms ({len(placeholder_rooms)}) — No API data; may be paywalled or upcoming",
            show_lines=False, header_style="bold", box=box.SIMPLE_HEAD,
        )
        plh.add_column("#",         style="dim", width=4, justify="right")
        plh.add_column("Room Code", min_width=32)
        plh.add_column("Age",       width=7, justify="right", style="dim")
        plh.add_column("URL",       style="dim cyan")
        for i, room in enumerate(placeholder_rooms, 1):
            plh.add_row(
                str(i), room["code"],
                days_old(room["release"]),
                f"https://tryhackme.com/room/{room['code']}",
            )
        console.print(plh)
        console.print("[dim]Links may redirect or require a paid business plan.[/dim]\n")


if __name__ == "__main__":
    main()
