# Zaminex Real Estate CRM

Simple real estate CRM. Django backend + React frontend. Language: Persian (RTL).

---

## 1. Requirements

- Python 3.11+
- Node.js 18+ (only if you edit frontend)
- PostgreSQL 18+

## 2. Install PostgreSQL

### Windows
1. Download: https://sbp.enterprisedb.com/getfile.jsp?fileid=1260302
2. Run installer. Set password to `zaminex` when asked.
3. When you see **StackBuilder** checkbox at the end, **uncheck it** (disable).
4. Set PATH permanently - open PowerShell as Administrator and run:
```powershell
setx PATH "$env:PATH;C:\Program Files\PostgreSQL\18\bin" /M
```
Close and reopen PowerShell. Check:
```powershell
psql --version
```

### Linux (Ubuntu/Debian)
```bash
sudo apt update
sudo apt install postgresql postgresql-contrib -y
sudo systemctl start postgresql
sudo systemctl enable postgresql
```
Set PATH permanently:
```bash
echo 'export PATH=$PATH:/usr/lib/postgresql/18/bin' >> ~/.bashrc
source ~/.bashrc
psql --version
```
During install, set postgres user password to `zaminex` or run:
```bash
sudo -u postgres psql -c "ALTER USER postgres WITH PASSWORD 'zaminex';"
```

### Mac
1. Download: https://sbp.enterprisedb.com/getfile.jsp?fileid=1260319
2. Run installer. Set password to `zaminex`. Uncheck StackBuilder at end.
3. Set PATH permanently:
```bash
echo 'export PATH=$PATH:/Library/PostgreSQL/18/bin' >> ~/.zshrc
source ~/.zshrc
psql --version
```

### Create Database User and Database (Run Once)
```bash
psql -U postgres -c "CREATE USER zaminex WITH PASSWORD 'zaminex';"
psql -U postgres -c "CREATE DATABASE zaminex OWNER zaminex;"
```

---

## 3. Default Accounts

| Role | Username | Password |
|------|----------|----------|
| Admin | `ZaminexAdmin` | `SSASAZPT4` |
| Consultant | `consultant` | `123456789` |

---

## 4. Important Note About seed_data.json

`ZaminexB/fixtures/seed_data.json` contains **only basic reference data** (property usages, types, deal types, attributes, provinces/cities/districts, company settings - 234 objects).

**Use it only when you need basic data.** For real data moving, always use `zaminex_backup.sql` (full database backup).

- **First time:** If you have `zaminex_backup.sql`, use backup (recommended). If you have no backup and start empty, use `seed_data.json`.
- **When you change database:** Don't use `seed_data.json`. Take new backup with `pg_dump`.
- **When you get new version of project:** Don't use `seed_data.json` again. Keep your existing database and only run `migrate`.

---

## 5. Three Situations - What Commands to Run

### Situation A: First Time You Get The Project

**Option 1 - You have `zaminex_backup.sql` (Recommended):**
```bash
cd ZaminexB
pip install -r requirements.txt
psql -U postgres -c "CREATE USER zaminex WITH PASSWORD 'zaminex';"
psql -U postgres -c "CREATE DATABASE zaminex OWNER zaminex;"
psql -U postgres -d zaminex -h localhost -p 5432 -f ../zaminex_backup.sql
python manage.py migrate
python manage.py runserver
```
Open http://localhost:8000/

**Option 2 - No backup, fresh empty database:**
```bash
cd ZaminexB
pip install -r requirements.txt
psql -U postgres -c "CREATE USER zaminex WITH PASSWORD 'zaminex';"
psql -U postgres -c "CREATE DATABASE zaminex OWNER zaminex;"
python manage.py migrate
python manage.py loaddata fixtures/seed_data.json
python manage.py seed_basics
python manage.py runserver
```
Open http://localhost:8000/ and login with accounts above.

### Situation B: You Made Changes to Project and Database and Want to Save

**After code changes (models changed):**
```bash
cd ZaminexB
python manage.py makemigrations
python manage.py migrate
```

**After entering real data (properties, listings, etc.) - Take backup immediately:**
```bash
# Plain SQL backup (readable, recommended for moving)
pg_dump -U postgres -d zaminex -h localhost -p 5432 -f zaminex_backup.sql

# Or custom compressed format
pg_dump -U postgres -d zaminex -h localhost -p 5432 --format=custom -f zaminex_backup.dump
```
Keep this file safe. This is your real data. Use it for moving to new server.

**Do NOT overwrite `seed_data.json` with real data.** `seed_data.json` should stay only basics.

### Situation C: You Have Project But Receive New Version of Code

```bash
# 1. Backup your current database first
pg_dump -U postgres -d zaminex -h localhost -p 5432 -f zaminex_backup_before_update.sql

# 2. Replace project files with new version (keep your zaminex_backup.sql)

# 3. Inside ZaminexB
cd ZaminexB
pip install -r requirements.txt
python manage.py migrate

# 4. If frontend changed, rebuild (see section 6)

# 5. Run
python manage.py runserver
```

Your data stays. No need to use `seed_data.json` again.

---

## 6. Ticket Workspace

The internal ticket workspace is available to both roles under the `تیکت‌ها` menu.

- Consultants have sent, received, and create-ticket tabs.
- Admins additionally have an all-tickets monitoring tab with server-side filters and CSV export.
- A ticket is linked to exactly one property, listing, follow-up, task, or existing ticket.
- Subject choices are resolved server-side using the current user's object-level access; consultants cannot use or discover another consultant's private task, listing, follow-up, or ticket as a subject.
- Messages are append-only. Multi-recipient tickets use private recipient branches, with per-user unread state, protected attachments, in-app notifications, SLA deadlines, tags, and an audit trail.
- Ticket attachment downloads go through an authenticated permission-checked endpoint and are not exposed as public media URLs.

After pulling this feature, apply the database migrations before running the server:

```bash
cd ZaminexB
python manage.py migrate
```

## 7. Frontend Build (Only If You Edit React)

You don't need this to run the project. Bundles are already built and served by Django.

If you edit files in `ZaminexF/src/`, rebuild once so the changes reach the backend:

```bash
cd ZaminexF
npm install
npm run build
```

That's it. The build writes straight into `ZaminexB/static/frontend/` (replacing the old
bundle automatically), and `base.html` picks up the new file names automatically from the
Vite manifest — no copying, no deleting old files, no editing `base.html` by hand.

After building, just run the backend as usual:

```bash
cd ZaminexB
python manage.py runserver
```

> The generated frontend bundle in `ZaminexB/static/frontend/` is intentionally not
> committed to Git (it is built locally). After cloning, if the bundle is missing, run the
> `npm run build` step above once.

---

## 8. Run Server

```bash
cd ZaminexB
python manage.py runserver
```
Open http://localhost:8000/

---

## 9. Restore Backup (When Moving Server)

```bash
psql -U postgres -c "CREATE USER zaminex WITH PASSWORD 'zaminex';"
psql -U postgres -c "CREATE DATABASE zaminex OWNER zaminex;"
psql -U postgres -d zaminex -h localhost -p 5432 -f zaminex_backup.sql
# Or for custom format:
# pg_restore -U postgres -d zaminex -h localhost -p 5432 --clean zaminex_backup.dump
cd ZaminexB
python manage.py migrate
python manage.py runserver
```

---

## 10. Redis (Optional — Caching, Phase 2+)

Redis is an **optimisation, never a dependency**:

- **Without** `REDIS_URL` the app runs on in-process LocMem — nothing extra
  to install, plain checkouts are unchanged.
- **With** `REDIS_URL` the default cache backend becomes django-redis. The
  configuration is fail-open (`IGNORE_EXCEPTIONS`): a dead or slow Redis
  degrades to a cache miss, never to a 500.

```bash
# Local Redis (optional):
docker compose up -d redis
export REDIS_URL=redis://localhost:6379/0
python manage.py runserver
```

What uses the cache: DRF's throttle counters (rate limits become accurate
across workers as soon as Redis is present) and the cache helpers in
`apps/common/cache_utils.py` (versioned `zaminex:v1:…` keys, JSON payloads
with exact `Decimal` round-trips, `cache_or_compute` with a per-key lock for
thundering-herd protection). See `benchmarks/README.md` for the phase plan.

---

## 11. Map Place Search (Geocoding)

The map picker resolves Persian place names through the app's own endpoint,
`GET /common/api/geocode/?q=…[&viewbox=w,n,e,s][&bounded=1]`, rather than by
calling a public geocoder from the browser — the browser only ever talks to
its own origin, which is what the Content-Security-Policy allows. The upstream
is called from one place (`apps/common/geocode.py`), which caches the result,
paces itself to roughly one request per second and sends a descriptive
`User-Agent`.

Every knob is optional; the defaults work against the public Nominatim:

| Setting | Default | Why you would change it |
| --- | --- | --- |
| `GEOCODE_UPSTREAM` | `https://nominatim.openstreetmap.org/search` | Point at a self-hosted Nominatim — this is also what makes a deployment with no internet access possible. |
| `GEOCODE_USER_AGENT` | `Zaminex-CRM/1.0 (+…)` | The public instance's usage policy requires identifying yourself. |
| `GEOCODE_TIMEOUT` | `8` (seconds) | A lookup is interactive; a hung upstream must not hold the request. |
| `GEOCODE_PACING_SECONDS` | `1.1` | `0` disables pacing, which is what a private upstream wants. The budget is global across workers when Redis is present, per-process otherwise. |

```bash
# Air-gapped or self-hosted geocoder:
export GEOCODE_UPSTREAM=http://nominatim.internal:8080/search
export GEOCODE_PACING_SECONDS=0
```

Coordinates are effectively permanent, so a hit is cached for 30 days and a
miss for 7 (`GEOCODE_CACHE_TTL` / `GEOCODE_NEGATIVE_CACHE_TTL` in
`config/settings.py`) — coverage of Iran improves, and a stale "not found"
should not outlive the fix.

The two failure modes are deliberately distinct, and the UI says so: `200`
with an empty array means *no such place*, while `503` means *the geocoder
could not be reached*. Conflating them was the original bug — a blocked
request reported itself as an address that does not exist, so the operator
kept re-typing something that was never looked up.

---

## Quick Checklist

- [ ] PostgreSQL installed, password `zaminex`, PATH set permanently
- [ ] DB user and database created
- [ ] `pip install -r requirements.txt`
- [ ] Restored from `zaminex_backup.sql` OR fresh install with `seed_data.json`
- [ ] `python manage.py runserver`
- [ ] After real data entry, immediately `pg_dump` backup
