# THM PointHound

Fetches every public TryHackMe room, diffs it against your completed list, and ranks what's left by point value - so you always know the highest-value lab to hit next.

---

## Setup

**Requirements:** Python 3.10+

```bash
git clone https://github.com/kali-mx/thm-pointhound
cd thm-pointhound
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

**Staying current:**

```bash
git pull
```

No reinstall needed unless `requirements.txt` changes.

---

## Getting Your `connect.sid` Cookie

Required on every run to bypass TryHackMe's bot detection.

1. Log in to [tryhackme.com](https://tryhackme.com) in your browser
2. Press `F12` → **Application** → **Cookies** → `https://tryhackme.com`
3. Copy the **Value** of `connect.sid`

> Treat this like a password - it authenticates as you on the platform.

---

## Basic Usage

```bash
python thm-pointhound.py <username> --cookie '<your connect.sid>'
```

This fetches the full room list, diffs it against your completions, and prints the **top 50 uncompleted rooms by point value**.

---

## All Flags

| Flag | Default | Description |
|------|---------|-------------|
| `username` | required | TryHackMe username to look up |
| `--cookie SID` | required | `connect.sid` session cookie |
| `--top N` | 50 | Show top N rooms in the main table |
| `--difficulty` | all | Filter main table to `easy`, `medium`, `hard`, `insane`, or `info` |
| `--min-points P` | 0 | Exclude rooms worth less than P points |
| `--max-points P` | off | Exclude rooms worth more than P points |
| `--no-ctf` | off | Exclude CTF/challenge rooms from the main table |
| `--quick-wins` | off | Show a second table: easy + medium walkthrough rooms within the points range |
| `--soc` | off | Show the SOC Simulator scenarios table |
| `--paywalled` | off | Show paywalled/unlisted rooms (business plan content) |
| `--no-cache` | off | Force a full re-fetch of all room data (slow - avoid unless needed) |
| `--verify` | off | Scrape each room page to catch completions the API missed (requires `--cookie`) |
| `--mark-done NAME` | off | Manually mark a room as completed by name or code |
| `--list-done` | off | List all manually marked rooms |
| `--debug` | off | Print raw API fields for troubleshooting |

---

## Examples

```bash
# Standard run - top 50 uncompleted rooms by points
python thm-pointhound.py KaliMax --cookie 's:abc123...'

# Top 20 hard rooms only
python thm-pointhound.py KaliMax --cookie 's:abc123...' --top 20 --difficulty hard

# Exclude CTF rooms, show only rooms above 100 pts
python thm-pointhound.py KaliMax --cookie 's:abc123...' --min-points 100 --no-ctf

# Quick wins: easy + medium walkthroughs between 60 and 250 pts
python thm-pointhound.py KaliMax --cookie 's:abc123...' --quick-wins --min-points 60 --max-points 250

# Full output: main table + quick wins + SOC scenarios + paywalled rooms
python thm-pointhound.py KaliMax --cookie 's:abc123...' --quick-wins --soc --paywalled

# Run for another user (shares the room detail cache - only fetches their uncompleted rooms)
python thm-pointhound.py Christie994 --cookie 's:abc123...'
```

---

## Quick Wins Table

`--quick-wins` appends a focused table below the main one. It always:

- Filters to **easy and medium** rooms only
- Excludes **CTF/challenge rooms** (per-flag scoring - time-consuming regardless of difficulty)
- Respects `--min-points` and `--max-points` to define the points range
- Sorts by points descending and shows all matches within the range

Use it to identify low-effort, good-value labs when you want to grind points without a heavy time commitment:

```bash
python thm-pointhound.py KaliMax --cookie '...' --quick-wins --min-points 60 --max-points 250
```

---

## How Points Are Calculated

THM has no direct points-per-room API. Points are read from each room's public scoreboard (`/api/v2/rooms/scoreboard`), which shows the top 100 completion scores.

Two scoring models exist and are handled differently:

**Walkthrough rooms** (linear, single point value) use `min()` of scoreboard scores. When THM reduces a room's value, old completions retain the higher historical score - `min()` always reflects the current value new completers would earn.

**Challenge rooms** (CTF-style, points per flag) use `mode()`. Scores vary by how many flags were captured, so `mode()` captures the most common full-completion total.

Point values are cached and **refreshed every 7 days** automatically.

---

## Cache

Room data is cached in `.cache/` inside the project directory (git-ignored). Three files:

| File | Scope | TTL |
|------|-------|-----|
| `sitemap_codes.json` | All room codes from the sitemap | 24 hours |
| `room_details.json` | Name, difficulty, points, type - shared across all usernames | Points refresh every 7 days |
| `manual_done_<username>.json` | Your `--mark-done` overrides | Permanent until removed |

**The room details cache is shared across usernames on the same machine.** Running the tool for a user with many uncompleted rooms (first run can take 10–15 minutes) warms the cache for everyone on that machine. Subsequent runs skip already-cached rooms and complete in seconds.

> **Note for new installs:** The cache is not included in the repo (it's git-ignored). Every fresh clone starts cold - expect a slow first run while room details are fetched. After that, runs are fast.

To clear everything and start fresh:

```bash
rm -rf .cache/
```

---

## Handling Missed Completions

TryHackMe's completion API sometimes misses rooms finished via learning paths. Two ways to fix this:

**Option 1 - Manual override** (instant, permanent):
```bash
python thm-pointhound.py KaliMax --mark-done "Defensive Security Intro"
python thm-pointhound.py KaliMax --mark-done hydra sustah
python thm-pointhound.py KaliMax --list-done
```

**Option 2 - Page verification** (thorough but slow - checks every uncompleted room):
```bash
python thm-pointhound.py KaliMax --cookie '...' --verify
```
Any rooms confirmed as completed are auto-saved to your overrides file.

---

## Notes

- **Business plan rooms** (AWS, Azure, XDR, Sentinel labs) appear in the sitemap but return no API data. They are hidden by default and shown with `--paywalled`.
- **Completed rooms for other users** are fetched from a public API endpoint - no special auth needed beyond your cookie for bot detection.
- **Bot detection:** THM uses Vercel bot protection. `cloudscraper` handles the challenge automatically. If fetches start failing, the `browser` config in `make_scraper()` may need a newer Chrome version string.
