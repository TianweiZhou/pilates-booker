#!/usr/bin/env python3
"""
Auto-book the "Reformer Group (lvl3-Inter./Chinese)" class at Neutral Pilates.

The studio releases the Saturday 11:00 a.m. class every Thursday at 11:00 a.m.
Eastern (America/Toronto). This script:

  1. Logs in with your member account (email + password).
  2. Waits until exactly 11:00:00 ET (skipped with WAIT_FOR_RELEASE=false).
  3. Scans the booking calendar for the FARTHEST-out Saturday with an open
     11:00 a.m. slot (i.e. the newly released class) and books it using your
     existing class package/plan.

It never enters card details. If the only payment option is buying a new
plan, it stops and saves a screenshot instead.

Configuration (environment variables):
  PILATES_EMAIL      member account email            (required)
  PILATES_PASSWORD   member account password         (required)
  SERVICE_URL        booking calendar URL            (default: the lvl3 Chinese class)
  WAIT_FOR_RELEASE   "true" = wait for Thu 11:00 ET  (default: true)
  DRY_RUN            "true" = do everything except the final booking click
  MAX_POLL_MINUTES   how long to keep retrying after 11:00 (default: 20)
  LOOKAHEAD_DAYS     how far ahead to look for Saturdays (default: 45)
  HEADLESS           "false" to watch the browser     (default: true)
  SCREENSHOT_DIR     where to save screenshots  (default: ./screenshots;
                      the Lambda Dockerfile sets this to /tmp/screenshots)

Screenshots are written to SCREENSHOT_DIR for every run.
"""

import os
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from playwright.sync_api import TimeoutError as PWTimeout
from playwright.sync_api import sync_playwright

# ---------------------------------------------------------------- config ---
TZ = ZoneInfo("America/Toronto")
SERVICE_URL = os.environ.get(
    "SERVICE_URL",
    "https://www.neutralpilates.com/booking-calendar/"
    "reformer-group-lvl3-inter-chinese?referral=service_list_widget",
)
EMAIL = os.environ.get("PILATES_EMAIL", "")
PASSWORD = os.environ.get("PILATES_PASSWORD", "")
WAIT_FOR_RELEASE = os.environ.get("WAIT_FOR_RELEASE", "true").lower() == "true"
DRY_RUN = os.environ.get("DRY_RUN", "false").lower() == "true"
MAX_POLL_MINUTES = int(os.environ.get("MAX_POLL_MINUTES", "20"))
SKIP_LOGIN = os.environ.get("SKIP_LOGIN", "false").lower() == "true"  # testing only
# The studio releases the Saturday that is 30 days after release Thursday.
TARGET_OFFSET_DAYS = int(os.environ.get("TARGET_OFFSET_DAYS", "30"))
# If the expected date hasn't opened after this many seconds, fall back to
# scanning every Saturday (in case the studio changes its release pattern).
FALLBACK_SCAN_AFTER = int(os.environ.get("FALLBACK_SCAN_AFTER", "180"))
LOOKAHEAD_DAYS = int(os.environ.get("LOOKAHEAD_DAYS", "45"))
HEADLESS = os.environ.get("HEADLESS", "true").lower() == "true"

RELEASE_HOUR, RELEASE_MINUTE = 11, 0
# GitHub cron fires unpredictably late, so runs start hours early and sleep.
# Only bail out if we somehow start earlier than this before release.
MAX_EARLY_MINUTES = int(os.environ.get("MAX_EARLY_MINUTES", "300"))

# /var/task is read-only on AWS Lambda; the Dockerfile points this at /tmp.
SHOTS = Path(os.environ.get("SCREENSHOT_DIR", "screenshots"))
SHOTS.mkdir(parents=True, exist_ok=True)


def log(msg: str) -> None:
    print(f"[{datetime.now(TZ):%Y-%m-%d %H:%M:%S %Z}] {msg}", flush=True)


def shot(page, name: str) -> None:
    try:
        page.screenshot(path=str(SHOTS / f"{datetime.now(TZ):%H%M%S}-{name}.png"),
                        full_page=True)
    except Exception:
        pass


def ordinal(n: int) -> str:
    if 11 <= n % 100 <= 13:
        return f"{n}th"
    return f"{n}{ {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th') }"


def day_label(d: datetime) -> str:
    # Calendar buttons are labelled like "Saturday, July 25th, 2026"
    return f"{d:%A}, {d:%B} {ordinal(d.day)}, {d.year}"


# ------------------------------------------------------------ page helpers ---
def hide_chat_widget(page) -> None:
    """The Wix Chat bubble auto-opens and sits over the booking buttons."""
    try:
        page.add_style_tag(
            content="#pinnedBottomRight{display:none !important;}")
    except Exception:
        pass


def dismiss_cookie_banner(page) -> None:
    for label in ["Decline All", "Decline", "Essential Only", "Accept"]:
        try:
            btn = page.get_by_role("button", name=re.compile(label, re.I)).first
            if btn.is_visible(timeout=1500):
                btn.click()
                log(f"Dismissed cookie banner via '{label}'")
                return
        except Exception:
            continue


def login(page) -> None:
    """Open the member login modal from the calendar page and sign in."""
    log("Logging in…")
    page.goto(SERVICE_URL, wait_until="domcontentloaded")
    page.wait_for_timeout(3000)
    dismiss_cookie_banner(page)

    # The page has several "Log in" texts (header, booking widget); try each
    # until the login modal's email field actually appears.
    links = page.get_by_text(re.compile(r"^\s*Log ?in\s*$", re.I))
    count = links.count()
    if count == 0:
        shot(page, "no-login-link")
        log(f"DIAG: url={page.url!r} title={page.title()!r}")
        log(f"DIAG: page text: {page.locator('body').inner_text()[:600]!r}")
        raise RuntimeError("Could not find any 'Log in' link — maybe already "
                           "logged in, blocked, or the page layout changed.")

    email_box = page.locator('input[type="email"]').first
    opened = False
    for i in range(min(count, 3)):
        try:
            links.nth(i).click(timeout=8000)
            email_box.wait_for(state="visible", timeout=8000)
            opened = True
            break
        except PWTimeout:
            log(f"Login link #{i + 1} of {count} didn't open the modal…")
    if not opened:
        shot(page, "login-modal-missing")
        log(f"DIAG: url={page.url!r} title={page.title()!r}")
        log(f"DIAG: page text: {page.locator('body').inner_text()[:600]!r}")
        raise RuntimeError("The login modal never appeared — the site may be "
                           "blocking this runner. See the screenshot artifact.")

    email_box.fill(EMAIL)
    page.locator('input[type="password"]').first.fill(PASSWORD)
    shot(page, "login-filled")
    page.get_by_role("button", name=re.compile(r"^\s*Log ?in\s*$", re.I)).last.click()

    # Wait for the modal to close / member state to load.
    try:
        page.locator('input[type="password"]').first.wait_for(state="hidden",
                                                              timeout=20000)
    except PWTimeout:
        shot(page, "login-failed")
        raise RuntimeError("Login did not complete — check PILATES_EMAIL / "
                           "PILATES_PASSWORD. Screenshot saved.")
    page.wait_for_timeout(3000)
    shot(page, "logged-in")
    log("Logged in.")


def wait_until_release() -> None:
    now = datetime.now(TZ)
    release = now.replace(hour=RELEASE_HOUR, minute=RELEASE_MINUTE,
                          second=0, microsecond=0)
    delta = (release - now).total_seconds()

    if delta > MAX_EARLY_MINUTES * 60:
        log(f"More than {MAX_EARLY_MINUTES} min before release "
            f"({release:%H:%M %Z}) — refusing to wait that long. Exiting.")
        sys.exit(0)
    if delta < -MAX_POLL_MINUTES * 60:
        log("Release window already passed — exiting.")
        sys.exit(0)
    if delta > 0:
        log(f"Waiting {delta:.0f}s until release at {release:%H:%M:%S %Z}…")
        time.sleep(delta)
    log("Release time reached.")


MONTHS = ["January", "February", "March", "April", "May", "June", "July",
          "August", "September", "October", "November", "December"]


def displayed_month(page):
    """(year, month) currently shown, parsed from a day button's aria-label."""
    btn = page.locator('button[aria-label*=", 20"]').first
    try:
        label = btn.get_attribute("aria-label", timeout=5000) or ""
    except PWTimeout:
        return None
    m = re.search(rf"({'|'.join(MONTHS)}) \d+\w*, (\d{{4}})", label)
    if not m:
        return None
    return int(m.group(2)), MONTHS.index(m.group(1)) + 1


def goto_month(page, target: datetime) -> bool:
    """Navigate the calendar (both directions) to the target month."""
    for _ in range(14):
        cur = displayed_month(page)
        if cur is None:
            return False
        if cur == (target.year, target.month):
            return True
        direction = "next month" if cur < (target.year, target.month) \
            else "previous month"
        try:
            page.get_by_role("button", name=direction).click(timeout=4000)
        except PWTimeout:
            return False  # nav button missing/disabled (e.g. at current month)
        page.wait_for_timeout(800)
    return False


def open_slot_for(page, d: datetime):
    """Select date `d`; return the clickable open 11:00 slot or None."""
    if not goto_month(page, d):
        return None
    day_btn = page.get_by_role("button",
                               name=re.compile(re.escape(day_label(d)))).first
    try:
        day_btn.click(timeout=4000)
    except PWTimeout:
        return None
    page.wait_for_timeout(1200)

    body = page.locator("body").inner_text()
    if "No availability" in body:
        return None

    # Open slots render as a selectable chip named like "11:00 a.m.".
    # Closed ones are named "The 11:00 a.m. slot is closed for booking".
    slot = page.get_by_role("checkbox",
                            name=re.compile(r"^11:00", re.I)).first
    try:
        if slot.is_visible(timeout=2000):
            return slot
    except Exception:
        pass
    slot = page.get_by_role("radio", name=re.compile(r"^11:00", re.I)).first
    try:
        if slot.is_visible(timeout=1000):
            return slot
    except Exception:
        pass
    return None


def booked_dates(page) -> set:
    """Upcoming bookings as (year, month, day), to avoid double-booking.

    Wix allows booking the same session twice (it just burns another
    session credit), so we must skip Saturdays that are already booked.
    """
    dates = set()
    try:
        # The members area 403s on direct navigation; go via the member menu.
        page.locator("header").get_by_role("button").filter(
            has=page.locator("img")).first.click(timeout=8000)
        item = page.get_by_role("menuitem", name=re.compile("My Bookings", re.I))
        item.first.click(timeout=8000)
        page.wait_for_timeout(5000)
        body = page.locator("body").inner_text()
        for month, day, year in re.findall(
                rf"({'|'.join(MONTHS)}) (\d{{1,2}}), (\d{{4}}), \d", body):
            dates.add((int(year), MONTHS.index(month) + 1, int(day)))
        log(f"Existing upcoming bookings: {sorted(dates) or 'none'}")
    except Exception as e:
        log(f"WARNING: could not read existing bookings ({e}) — "
            "double-booking protection unavailable this run.")
    return dates


def candidate_saturdays() -> list[datetime]:
    """Upcoming Saturdays, farthest first, within the lookahead window."""
    today = datetime.now(TZ).replace(hour=0, minute=0, second=0, microsecond=0)
    days = [today + timedelta(days=i) for i in range(1, LOOKAHEAD_DAYS + 1)]
    sats = [d for d in days if d.weekday() == 5]
    return list(reversed(sats))


def expected_target() -> datetime:
    """The Saturday expected to be released today (Thursday + 30 days)."""
    d = datetime.now(TZ).replace(hour=0, minute=0, second=0, microsecond=0) \
        + timedelta(days=TARGET_OFFSET_DAYS)
    while d.weekday() != 5:
        d += timedelta(days=1)
    return d


def seconds_to_release() -> float:
    now = datetime.now(TZ)
    release = now.replace(hour=RELEASE_HOUR, minute=RELEASE_MINUTE,
                          second=0, microsecond=0)
    return (release - now).total_seconds()


def complete_booking(page) -> None:
    """From the booking form: waiver + plan payment + final book click."""
    page.wait_for_timeout(2500)
    hide_chat_widget(page)
    shot(page, "booking-form")

    # Waiver / agreement checkboxes.
    boxes = page.locator('input[type="checkbox"]')
    for i in range(boxes.count()):
        box = boxes.nth(i)
        try:
            if not box.is_checked():
                box.check(force=True)  # styled overlay intercepts normal clicks
                log("Checked an agreement checkbox.")
        except Exception:
            continue

    body = page.locator("body").inner_text()

    # Prefer a payment option that is an existing plan: Wix labels active
    # plans with remaining-session text like "5 sessions left".
    plan = page.get_by_text(re.compile(r"session[s]? left|membership",
                                       re.I)).first
    try:
        if plan.is_visible(timeout=3000):
            plan.click()
            log("Selected existing plan as payment.")
            page.wait_for_timeout(800)
    except Exception:
        log("No 'sessions left' plan label found — relying on the "
            "pre-selected payment option.")

    # Find the final CTA. With a valid plan it reads "Book Now" (or similar).
    cta = None
    for pattern in [r"^\s*book\s*(now)?\s*$", r"^\s*confirm\s*"]:
        loc = page.get_by_role("button", name=re.compile(pattern, re.I)).last
        try:
            if loc.is_visible(timeout=2000):
                cta = loc
                break
        except Exception:
            continue

    if cta is None:
        shot(page, "no-book-button")
        if re.search(r"buy a plan", body, re.I):
            raise RuntimeError(
                "The form only offers 'Buy a plan' — your package may be "
                "used up or not detected. NOT purchasing anything. "
                "Screenshot saved.")
        raise RuntimeError("Could not find the final booking button. "
                           "Screenshot saved.")

    if DRY_RUN:
        shot(page, "dry-run-stop")
        log("DRY_RUN=true — stopping before the final booking click. "
            "Everything up to payment worked.")
        return

    cta.click()

    # Booking takes a while to process; poll for the outcome.
    success_re = re.compile(r"confirmed|you.?re booked|thank you|"
                            r"booking number|see you|added to (your )?calendar",
                            re.I)
    already_re = re.compile(r"already (have|booked)", re.I)
    deadline = time.time() + 90
    while time.time() < deadline:
        page.wait_for_timeout(3000)
        body = page.locator("body").inner_text()
        if success_re.search(body):
            shot(page, "confirmed")
            log("✅ Booking confirmed!")
            return
        if already_re.search(body):
            shot(page, "already-booked")
            log("ℹ️ You already have a booking for this session — nothing to do.")
            return
        # Still on the form with the spinner? keep waiting.
    shot(page, "after-book-click")
    raise RuntimeError("Clicked book, but saw no confirmation within 90s — "
                       "check the screenshots and your bookings page.")


# ------------------------------------------------------------------- main ---
def main() -> None:
    if not SKIP_LOGIN and (not EMAIL or not PASSWORD):
        sys.exit("Set PILATES_EMAIL and PILATES_PASSWORD.")

    with sync_playwright() as p:
        # Full Chromium (new headless) rather than the stripped headless
        # shell — closer to a regular browser, fewer site compatibility gaps.
        #
        # The extra flags below are the standard, widely-documented
        # workaround for running full Chromium inside AWS Lambda's
        # restricted container (no CAP_SYS_ADMIN, tiny/no /dev/shm, and
        # process-spawning restrictions that break Chromium's normal
        # multi-process model — surfaces as "Target crashed" on new_page
        # without them). All are harmless in normal environments too
        # (GitHub Actions, local runs), so we always pass the full set.
        launch_args = [
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--disable-software-rasterizer",
            "--single-process",   # Lambda can't support Chromium's normal
            "--no-zygote",        # multi-process model; run it as one.
        ]
        try:
            browser = p.chromium.launch(headless=HEADLESS, channel="chromium",
                                        args=launch_args)
        except Exception:
            browser = p.chromium.launch(headless=HEADLESS, args=launch_args)
        page = browser.new_page(viewport={"width": 1440, "height": 1000},
                                locale="en-CA",
                                timezone_id="America/Toronto")
        page.set_default_timeout(20000)

        already_booked = set()
        if SKIP_LOGIN:
            log("SKIP_LOGIN=true — testing without an account.")
            page.goto(SERVICE_URL, wait_until="domcontentloaded")
            page.wait_for_timeout(3000)
            dismiss_cookie_banner(page)
        else:
            login(page)
            # Skip the bookings check only in the last seconds before release
            # (never eat into the booking race). A run that starts after
            # release — e.g. a delayed backup run — must still check, so it
            # can't double-book what the primary run just grabbed.
            if not WAIT_FOR_RELEASE or not (0 <= seconds_to_release() <= 90):
                already_booked = booked_dates(page)

        target = expected_target()
        if (target.year, target.month, target.day) in already_booked:
            log(f"Expected release date {target:%B %d} is already booked — "
                "nothing to do.")
            browser.close()
            return
        log(f"Target: {target:%A %B %d, %Y} at 11:00 a.m. "
            f"(fallback scan after {FALLBACK_SCAN_AFTER}s)")

        if WAIT_FOR_RELEASE:
            wait_until_release()

        start = time.time()
        deadline = start + MAX_POLL_MINUTES * 60
        fallback_at = start + FALLBACK_SCAN_AFTER
        attempt = 0
        while True:
            attempt += 1
            page.goto(SERVICE_URL, wait_until="domcontentloaded")
            page.wait_for_timeout(1500)
            hide_chat_widget(page)

            slot, chosen = None, None
            if time.time() < fallback_at:
                # Fast path: check only the expected release date.
                slot, chosen = open_slot_for(page, target), target
            else:
                # Fallback: scan every unbooked Saturday, farthest first.
                log(f"Attempt {attempt}: expected date not open — full scan.")
                for sat in candidate_saturdays():
                    if (sat.year, sat.month, sat.day) in already_booked:
                        log(f"Skipping {sat:%B %d} — already booked.")
                        continue
                    slot = open_slot_for(page, sat)
                    if slot is not None:
                        chosen = sat
                        break

            if slot is not None:
                log(f"Open 11:00 a.m. slot on {chosen:%A %B %d, %Y} — booking!")
                # Wix renders a styled chip over the real input, so a normal
                # click gets intercepted; it also auto-checks the input when
                # the day has a single slot.
                try:
                    already = slot.is_checked()
                except Exception:
                    already = False
                if not already:
                    slot.click(force=True)
                page.wait_for_timeout(400)
                shot(page, "slot-selected")
                page.get_by_role("button",
                                 name=re.compile(r"^\s*next\s*$", re.I)).first.click()
                complete_booking(page)
                break

            if time.time() > deadline:
                shot(page, "gave-up")
                raise RuntimeError(
                    f"No open Saturday slot appeared within "
                    f"{MAX_POLL_MINUTES} minutes. Screenshot saved.")
            if attempt % 10 == 0:
                log(f"Attempt {attempt}: not open yet, still retrying…")
            time.sleep(2)

        browser.close()


if __name__ == "__main__":
    main()
