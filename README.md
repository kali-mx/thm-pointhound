# THM PointHound

Dynamically fetches every public TryHackMe room, diffs it against your completed list, and ranks what's left by point value so you always know the highest-value lab to hit next.

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

---

## Getting Your `connect.sid` Cookie

Required for every run to bypass TryHackMe's bot detection.

1. Log in to [tryhackme.com](https://tryhackme.com) in your browser
2. Press `F12` to open DevTools
3. Go to **Application** -> **Cookies** -> `https://tryhackme.com`
4. Find the cookie named `connect.sid` and copy its **Value**

> Treat this like a password. It authenticates as you.

---

## Usage

The recommended way to run is with `--verify`. TryHackMe's API does not track rooms completed through learning paths, so without it you will see false positives -- rooms you already finished showing up as uncompleted.

```bash
python thm-pointhound.py <username> --cookie '<your connect.sid>' --verify
```

`--verify` scrapes each uncompleted room page for a completion signal and auto-saves any it finds. Once a room is saved it is not re-checked on future runs, so the overhead only applies to rooms that are genuinely uncompleted.

### Arguments

| Flag | Default | Description |
|------|---------|-------------|
| `username` | required | Your TryHackMe username |
| `--cookie SID` | required | `connect.sid` session cookie |
| `--verify` | off | Recommended. Scrapes room pages to catch completions the API misses (learning path rooms) |
| `--top N` | 50 | Show top N uncompleted rooms |
| `--difficulty` | all | Filter by `easy`, `medium`, `hard`, `insane`, or `info` |
| `--min-points P` | 0 | Hide rooms worth less than P points |
| `--no-cache` | off | Force a fresh fetch of room data |
| `--mark-done CODE [CODE ...]` | off | Manually mark a room as completed when `--verify` is not enough |
| `--list-done` | off | Show all manually marked rooms |
| `--debug` | off | Print raw API fields for troubleshooting |

### Examples

```bash
# Recommended: full run with verification
python thm-pointhound.py KaliMax --cookie 's:abc123...' --verify

# Quick run without verification (may show false positives for learning path rooms)
python thm-pointhound.py KaliMax --cookie 's:abc123...'

# Top 10 hard rooms only
python thm-pointhound.py KaliMax --cookie 's:abc123...' --verify --top 10 --difficulty hard

# Skip rooms under 100 pts, force fresh data
python thm-pointhound.py KaliMax --cookie 's:abc123...' --verify --min-points 100 --no-cache

# Manually mark a room the API and --verify both miss
python thm-pointhound.py KaliMax --mark-done hydra sustah
```

---

## Notes

- **First run is slow.** Room details are fetched one at a time to avoid rate limits. All subsequent runs use a local cache at `~/.cache/thm_pointhound/`.
- **Learning path completions** are not returned by TryHackMe's completion API. This is a known platform limitation. Always use `--verify` to catch them, or `--mark-done` as a fallback if page verification fails.
- **Room points are refreshed every 7 days** to catch any scoring changes THM makes after the initial cache.
- **Business plan rooms** (AWS, Azure, XDR, Sentinel labs) appear in the sitemap but are paywalled. They are listed separately at the bottom so you can evaluate the cost/benefit.
