# ps_2_internet

Reads grades for each student on a PowerSchool parent account and pauses or resumes existing UniFi QoS rules on a gateway. It does not create or delete rules.

Students are discovered from the parent account. Each student has their own assignments, excuses, cutoff, grace period, rules, override, and private link. After signing in, pick a student. The home page does not show grades. A private link shows one student's status only after that student's shared page is turned on.

## How internet is decided

Only individual assignments count. An assignment counts against internet when it is missing, or when its score is under the cutoff. The cutoff starts at 80% and can be changed per student. A teacher exemption or a parent excuse takes that assignment out of the count. Work that has no score yet does not count. A bad assignment still inside the grace period does not count either.

Quarter grades are shown only. A low quarter grade does not turn internet off, and a high one does not turn it on. Year and semester grades are not shown.

Each discovered rule can follow grades, follow the parent override, or both.

- **Rule based.** When assignments allow internet, the rule is put in the position you chose (resume or pause). When assignments do not allow internet, it is put in the other position.
- **Override.** While a parent override is active, these rules are held in the override's on or off position. When the override ends, a rule that also follows grades goes back to the grade position. A rule that only follows the override goes back to the position it had before the override.

Each student has a QoS name prefix, set on that student's settings page. Anything that does not start with that student's prefix is left alone. An empty prefix refuses changes. Pause and resume flip the rule's enabled flag. The rest of the rule is sent back unchanged.

New rules stay unused until a parent marks them. Two students should not share a prefix, because each student's check writes only the rules that start with their own prefix.

## Excuses and grace

A bad assignment is missing work, or a score under the cutoff. Those rows stay red. The Excuse button turns green and keeps the assignment out with no end date. Click it again and the assignment counts again.

Beside that, 1 through 5 days keeps the same assignment out until that time. The length is that many 24-hour periods from the click, shown in the household time zone. When the time ends, the assignment counts again unless PowerSchool now shows it is no longer missing or under the cutoff, or a parent excuses it again. Clicking Excuse while a day count is active makes the excuse permanent. Clicking a day count while Excuse is on replaces the permanent excuse with that many days. Clicking the date clears the timed excuse.

Each student has a grace period, set in business days on that student's settings page. It starts at 0, which means a bad assignment counts right away. Weekends are skipped, and the due date itself does not count. A grace of 5 means a Monday assignment is still inside grace on the following Sunday, and it counts starting the next Monday. There is no school-holiday calendar. An assignment with no due date is not in grace.

While a bad assignment is inside grace, the row stays red but lighter. It does not turn internet off. After it falls out of grace it uses the regular red and counts, unless it is excused.

## Shared page

`/` does not show grades or student names. Each student has a private link, `/s/` plus a token that does not change. The shared page is off until a parent turns on "Show grades on the private link." Clicking the link in admin copies it. It does not open the page.

When the shared page is on, it shows whether internet is on and why, the current quarter grades, and every problem assignment that is not permanently excused. That list includes work still in the grace period and work excused for a few days. A timed excuse shows the day it ends. Permanently excused work is left off. The same colors are used: lighter red inside grace, regular red after that. The list stays up even when a parent override has internet on. The headline still says a parent turned internet on until a time.

A missing or turned-off link looks the same as the home page.

## Admin

After signing in, each discovered student is a card. Opening a card goes to that student's page. Students goes back to the cards.

The student page leads with the internet override: on or off, until a chosen time. Clearing it is a separate action. Grade checks do not clear an override.

Assignments are the full school year, newest due date first. A blank due date stays at the bottom. Each row shows its quarter when the due date falls in a quarter. Click a quarter card to show that class. Click it again to show every class. A class with no assignments all year is not listed.

Colors match the quarter cards. Green is 85% and above. Yellow is 80% through 84%. Red is below 80%, and missing work is red. Work with no score yet has no color. Hide passing work starts on. It hides scores at or above the cutoff, and work that is not graded yet. Missing work and scores under the cutoff stay, including excused rows and rows still in grace.

Excuse and the day buttons appear only on bad assignments. The row stays red. Only the active excuse control turns green. Cutoff, grace, the shared-page switch, the QoS prefix, and each rule's checkboxes save as you change them. A short "Saved" mark confirms it. Override, Check PowerSchool now, and Discover new rules still submit the page.

Settings holds the things that change rarely: the QoS prefix, the cutoff, the grace period, the household time zone, and the rule list. The time zone starts as US/Eastern and applies to every student. Clocks include the abbreviation. Assignment due dates stay calendar dates.

## Quarter grades

A student's shared page and admin page list the quarter that is in session. A quarter whose dates contain today wins. If that quarter has no published grade, the class still shows "No grade yet" and stays uncolored. Green is 85% and above. Yellow is 80% through 84%. Red is 79% and below.

## What you need

- Python 3.11 or newer for a local run, or Docker
- A PowerSchool parent login. The portal must not require a second factor
- A UniFi Network API key on the gateway, with existing QoS rules you are willing to pause and resume
- An admin password for the website

## Configure

```bash
cp .env.example .env
```

Fill in `.env`. Leave it on the machine that runs the app. Do not commit it.

| Name | Purpose |
| --- | --- |
| `PS_USER`, `PS_PASSWORD`, `PS_LOGIN_URL` | Parent portal login |
| `PS_STUDENT` | Optional. Used once, to attach an older one-student database to this first name |
| `UNIFI_URL` | Gateway base URL, such as `https://unifi.example` |
| `UNIFI_SITE_ID` | Integration site id. QoS calls use the local site name `default` |
| `UNIFI_API_KEY` | Network API key, sent as `X-API-KEY` |
| `UNIFI_RULE_MATCH` | Optional. Used once, as the QoS prefix for `PS_STUDENT`. After that, set each prefix in admin |
| `ADMIN_PASSWORD` | Password for `/admin` |
| `ADMIN_SESSION_SECRET` | Signs the admin session cookie |

Optional process settings:

| Name | Purpose |
| --- | --- |
| `APP_DB` | SQLite path. Default is `data/app.db` |
| `PS_SCHEDULER` | Set to `0` to disable the background checks |

The background loop checks PowerSchool every hour, refreshes each student's rule list every five minutes, and about every thirty seconds ends overrides and timed excuses that have run out. Times on the site use one time zone for the household, chosen on a student's settings page. That starts as US/Eastern.

## Run locally

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
python -m unittest
uvicorn ps_internet.web:app --host 0.0.0.0 --port 8080
```

The home page is `/`. A student's private page is `/s/` plus that student's token. The admin page is `/admin`.

The command line can list students or write a snapshot. The snapshot contains portal data, so keep it under `data/`, which is not committed.

```bash
ps-internet students
ps-internet sync
```

## Docker

The image is Python 3.12. It serves the site on port 8080. The SQLite file lives in `/app/data`.

```bash
docker compose up -d --build
```

`docker-compose.yml` uses a Docker volume named `data`. For a persistent share, add a gitignored `docker-compose.override.yml` that replaces that volume. Compose merges the override automatically. An example:

```yaml
volumes:
  data:
    driver_opts:
      type: "nfs"
      o: "addr=storage.example,rw"
      device: ":/path/to/ps_2_internet"
```

`.env` is read by Compose for the container and is not copied into the image.

## What is not in git

These stay on the machine that runs the app:

- `.env` — logins, the API key, the admin password, and the session secret
- `docker-compose.override.yml` — host paths, share addresses, and anything else specific to one deployment
- `data/` — the SQLite database and any grade snapshots
- `.venv/` — the local virtualenv

Tests use made-up students, courses, and rule names.
