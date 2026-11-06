# ps_2_internet

Reads one student's grades from the PowerSchool parent portal and pauses or resumes existing UniFi QoS rules on a gateway. It does not create or delete rules.

The public page shows whether internet is on, why, and the latest quarter grades. A parent signs in to excuse assignments, choose which rules follow grades or an override, and turn internet on or off for a set time.

## How internet is decided

Only individual assignments count. An assignment counts against internet when it is missing, or when its score is under the cutoff. The cutoff starts at 80% and can be changed on the admin page. A teacher exemption or a parent excuse takes that assignment out of the count. Work that has no score yet does not count.

Quarter grades are shown only. A low quarter grade does not turn internet off, and a high one does not turn it on. Year and semester grades are not shown.

Each discovered rule can follow grades, follow the parent override, or both.

- **Rule based.** When assignments allow internet, the rule is put in the position you chose (resume or pause). When assignments do not allow internet, it is put in the other position.
- **Override.** While a parent override is active, these rules are held in the override's on or off position. When the override ends, a rule that also follows grades goes back to the grade position. A rule that only follows the override goes back to the position it had before the override.

Rules are matched by a name prefix from `UNIFI_RULE_MATCH`. Anything that does not start with that prefix is left alone. Pause and resume flip the rule's enabled flag. The rest of the rule is sent back unchanged.

New rules stay unused until a parent marks them. The first time the app sees rules whose names contain `uptime`, it marks those for both grades and override. That happens once.

## Quarter grades

The public page and the admin page list the quarter that is in session. Green is 85% and above. Yellow is 80% through 84%. Red is 79% and below. A class with no published quarter grade is left uncolored.

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
| `PS_STUDENT` | First name in the portal's student switcher |
| `UNIFI_URL` | Gateway base URL, such as `https://unifi.example` |
| `UNIFI_SITE_ID` | Integration site id. QoS calls use the local site name `default` |
| `UNIFI_API_KEY` | Network API key, sent as `X-API-KEY` |
| `UNIFI_RULE_MATCH` | Name prefix of the QoS rules this app may pause or resume |
| `ADMIN_PASSWORD` | Password for `/admin` |
| `ADMIN_SESSION_SECRET` | Signs the admin session cookie |

Optional process settings:

| Name | Purpose |
| --- | --- |
| `APP_DB` | SQLite path. Default is `data/app.db` |
| `PS_SCHEDULER` | Set to `0` to disable the background checks |

The background loop checks PowerSchool every hour, refreshes the rule list every five minutes, and expires an override about every thirty seconds. Times on the site are shown in `America/New_York`.

## Run locally

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
python -m unittest
uvicorn ps_internet.web:app --host 0.0.0.0 --port 8080
```

The public page is `/`. The admin page is `/admin`.

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

- `.env` — logins, the API key, the admin password, the session secret, the student first name, and the rule prefix
- `docker-compose.override.yml` — host paths, share addresses, and anything else specific to one deployment
- `data/` — the SQLite database and any grade snapshots
- `.venv/` — the local virtualenv

Tests use made-up students, courses, and rule names.
