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

```bash
python thm-pointhound.py <username> --cookie '<your connect.sid>'
```

### Arguments

| Flag | Default | Description |
|------|---------|-------------|
| `username` | required | Your TryHackMe username |
| `--cookie SID` | required | `connect.sid` session cookie |
| `--top N` | 50 | Show top N uncompleted rooms |
| `--difficulty` | all | Filter by `easy`, `medium`, `hard`, `insane`, or `info` |
| `--min-points P` | 0 | Hide rooms worth less than P points |
| `--no-cache` | off | Force a fresh fetch of room data |
| `--verify` | off | Attempt to detect completions the API missed by scraping room pages |
| `--mark-done NAME [NAME ...]` | off | Manually mark a room as completed. Accepts the room name or code |
| `--list-done` | off | Show all manually marked rooms |
| `--debug` | off | Print raw API fields for troubleshooting |

### Examples

```bash
# Standard run
python thm-pointhound.py KaliMax --cookie 's:abc123...'

# Top 10 hard rooms only
python thm-pointhound.py KaliMax --cookie 's:abc123...' --top 10 --difficulty hard

# Skip rooms under 100 pts, force fresh data
python thm-pointhound.py KaliMax --cookie 's:abc123...' --min-points 100 --no-cache

# Mark a room done by name (no need to know the room code)
python thm-pointhound.py KaliMax --mark-done "Defensive Security Intro"
python thm-pointhound.py KaliMax --mark-done hydra sustah
```

---

## Notes

- **First run is slow.** Room details are fetched one at a time to avoid rate limits. All subsequent runs use a local cache at `~/.cache/thm_pointhound/`.
- **Learning path completions** are not returned by TryHackMe's completion API. This is a known platform limitation. Use `--mark-done "Room Name"` to flag any room that shows as uncompleted when you know you have finished it. The room name from the output table works directly.
- **Room points are refreshed every 7 days** to catch any scoring changes THM makes after the initial cache.
- **Business plan rooms** (AWS, Azure, XDR, Sentinel labs) appear in the sitemap but are paywalled. They are listed separately at the bottom so you can evaluate the cost/benefit.
