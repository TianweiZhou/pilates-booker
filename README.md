# Neutral Pilates auto-booker

Books the **Reformer Group (lvl3-Inter./Chinese)** Saturday 11:00 a.m. class
at [Neutral Pilates](https://www.neutralpilates.com) automatically.

The studio releases each Saturday class on **Thursday at 11:00 a.m. Eastern**.
Every Thursday this script logs into your member account, waits until exactly
11:00:00, finds the newly released Saturday, and books it with your existing
class package — no card is ever entered.

## How it works

1. Logs in with your email + password (from secrets/env vars).
2. Waits until 11:00:00 a.m. America/Toronto.
3. Scans upcoming Saturdays **farthest-out first** and books the first one
   with an open 11:00 a.m. slot (that's the newly released class).
4. Checks the waiver box, pays with your existing plan, clicks **Book**.
5. Saves screenshots of every step (uploaded as workflow artifacts).

If the form only offers "Buy a plan" (package used up), it stops without
buying anything and the workflow fails so you get notified.

## Setup — GitHub Actions (runs in the cloud every Thursday)

1. Create a **private** GitHub repository and push this folder to it:

   ```bash
   cd "~/Desktop/class regiester"
   git init && git add -A && git commit -m "pilates auto-booker"
   gh repo create pilates-booker --private --source=. --push
   ```

2. Add your login as repository secrets
   (**Settings → Secrets and variables → Actions → New repository secret**):
   - `PILATES_EMAIL`
   - `PILATES_PASSWORD`

3. Done. The workflow runs every Thursday, starting ~10:25 a.m. Toronto time
   and booking at 11:00:00 sharp. GitHub emails you automatically if a run
   fails.

### Test it first (recommended)

In the repo: **Actions → Book Saturday Pilates class → Run workflow**, keep
`dry_run = true` and `wait_for_release = false`. This does everything —
login, slot selection, waiver, plan selection — but stops before the final
booking click. Download the `screenshots` artifact to confirm it all looked
right, then you're set for Thursday.

## Run locally instead

```bash
cd "~/Desktop/class regiester"
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
cp .env.example .env    # then edit .env with your password
set -a; source .env; set +a
python book_class.py
```

## Configuration

| Env var | Default | Meaning |
|---|---|---|
| `PILATES_EMAIL` / `PILATES_PASSWORD` | — | member login |
| `DRY_RUN` | `false` | stop before the final booking click |
| `WAIT_FOR_RELEASE` | `true` | wait for Thursday 11:00:00 ET |
| `MAX_POLL_MINUTES` | `20` | keep retrying this long after 11:00 |
| `HEADLESS` | `true` | set `false` to watch the browser |
| `SERVICE_URL` | lvl3 Chinese class | book a different class page |

## Caveats

- **Slot time is hard-matched to 11:00 a.m.** If the studio changes the class
  time, update the regex in `open_slot_for()` in `book_class.py`.
- If the studio's booking layout changes, selectors may need updating —
  the screenshots artifact shows exactly where a run stopped.
- Booking consumes one session from your package. When the package runs out
  the run fails safely (nothing is purchased) and GitHub emails you.
