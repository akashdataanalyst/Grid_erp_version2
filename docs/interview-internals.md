# Interview System — Andar se poori samajh

Site: `griderp.in` · Last verified: 2026-10-20 (grid_erp merge ke baad, staging par)
Process steps (kaun kya kab kare) ke liye [interview.md](interview.md) dekho.
Yeh document batata hai **code kahan hai, kaunsa document kya karta hai, flows andar se kaise
chalte hain, security, settings aur kuch atke toh kya karna hai.**

---

## 0. Code kahan hai — ek app, paanch modules

Pehle `grid_interview` alag app tha. **2026-10-20 se yeh `grid_erp` ka module "Grid Interview" hai.**
Logic aur code wahi hai, sirf folder aur import path badle
(`grid_interview.services.…` → `grid_erp.grid_interview.services.…`).

```
grid_erp/                                  repo root (~/grid_erp)
├── docs/
│   ├── interview.md                       process: kaun kya kab kare
│   └── interview-internals.md             yeh file
└── grid_erp/                              Python package (asli app)
    ├── hooks.py                           section "Grid Interview" + scheduler / permissions / install
    ├── modules.txt                        … · Grid AI · Grid Interview
    ├── dock/grid_erp/grid_erp.json        "Grid ERP" tile ki rail → Interviews
    ├── patches/grid_interview/            adopt_grid_interview_app (purani app se migrate)
    ├── grid_interview/                    module "Grid Interview" ↓
    │   ├── doctype/                       16 DocTypes (section 2)
    │   ├── page/                          grid_interview_dashboard, interview_scheduler
    │   ├── sidebar/grid_interview/        desk ka left menu
    │   ├── api/                           browser yahin call karta hai (patla)
    │   ├── services/                      asli dimaag: rules, permissions, emails, status
    │   ├── integrations/hrms_adapter.py   HRMS se baat sirf yahin
    │   ├── video/                         Mock / Jitsi provider
    │   ├── utils/                         settings, tokens (security.py), audit
    │   ├── setup/install.py               install / migrate par 4 roles
    │   ├── permissions.py                 interviewer ko sirf apne interviews; portal home page
    │   ├── www_helpers.py                 portal pages ka common context (nav, login check)
    │   ├── exceptions.py
    │   └── tests/                         177 tests (section 11)
    ├── recruitment_assessment/portal.py   portal ke liye candidate ke assessments
    ├── www/
    │   ├── candidate-portal/              EK portal: index, profile, references, account, register, password
    │   ├── interview/                     join.html (link), room.html (video)
    │   ├── interview-reference.html       referee ka form
    │   └── assessment-portal/             test dene ka page (Recruitment Assessment)
    ├── templates/
    │   ├── emails/                        interview_* , portal_* email templates
    │   └── includes/gi_portal_*.html      portal header + "blocked" message
    └── public/
        ├── js/grid_interview/             grid_interview.js (desk), interview_portal.js, interview_room.js
        ├── css/grid_interview/            interview_portal.css, interview_room.css
        └── images/grid_interview/         grid_interview.svg
```

**Teen layer ka niyam** (hamesha isi direction mein):

```
Browser (desk page / portal / room JS)
   │  sirf API call
   ▼
api/*.py            request lo, service ko do, jawab lautao (yahan koi rule nahi)
   ▼
services/*.py       permission checks, business rules, emails, status changes
   ▼
integrations/hrms_adapter.py   HRMS padhne wala akela code
video/*.py                     video provider se baat karne wala akela code
DocTypes                       data
```

| `api/` file | Kiske liye |
|---|---|
| `scheduling.py` | Scheduler page: schedule, reschedule, cancel, attach, interviewer search |
| `dashboard.py` | Dashboard: counts, list, timeline, decision, recording delete |
| `hr.py` | Result publish / unpublish, reference verification / clarification |
| `portal.py` | Candidate portal: register, profile, documents, references |
| `video.py` | Room: info, join, heartbeat, leave, end, join link |
| `recording.py` | Consent, recording start / stop, Jibri upload, AI review, job retry |

| `services/` file | Kaam |
|---|---|
| `scheduling.py` | HRMS Interview banana / badalna (HR ke naam se), Video Session, 5-minute milan (`run_cycle`) |
| `notifications.py` | Saari emails; ek email ek hi baar; delivery status |
| `portal_emails.py` | Result published, reference emails |
| `candidates.py` | Candidate account, self-registration, CSRF |
| `portal.py` | Candidate ko kya dikhe (`get_dashboard`, `own_applicants`) |
| `references.py`, `results.py` | Referees; result publish / unpublish |
| `meeting.py` | Kaun, kab join kare; room ka status; join links |
| `consent.py`, `recording.py` | Consent; recording start / stop, Jibri upload, file verify |
| `storage.py` | File kahan: Local disk (abhi) ya S3 (baad mein) |
| `transcription.py`, `analysis.py` | Text banana; AI draft + check + human review |
| `jobs.py` | Background queue (retry ke saath): finalize → transcribe → analyse |
| `dashboard.py` | Dashboard data, timeline, decision |
| `retention.py` | Roz purana data hatana |

**hooks.py mein Grid Interview ka hissa:**

| Hook | Kya |
|---|---|
| `app_include_js` | `js/grid_interview/grid_interview.js` (reschedule / cancel dialogs, desk) |
| `after_install` / `after_migrate` | `grid_interview.setup.install` → 4 roles |
| `scheduler_events` | `*/5 * * * *` → `services.scheduling.run_cycle`; daily → `services.retention.run` |
| `has_permission`, `permission_query_conditions` | Video Session / Transcript / Analysis: interviewer sirf apne |
| `get_website_user_home_page` | Candidate login ke baad `/candidate-portal` |
| `portal_menu_items` | "Candidate Portal" |
| `website_redirects` | `/interview-portal…` → `/candidate-portal…` (purane email links) |

HRMS DocTypes par **koi** `doc_events`, `doctype_js`, override **nahi** (test se pakka). Recruitment
Assessment ke apne Job Applicant hooks alag hain aur pehle jaise hain.

---

## 1. Desk par kahan milega

`/apps` → **Grid ERP** tile → rail mein **Interviews** → sidebar:

| Group | Kya milega |
|---|---|
| (upar) | **Dashboard**, **Interview Scheduler**, Interviews (HRMS), Job Applicants, Interview Feedback, Results |
| Video & Recording | Video Session, Consent, Recording, Transcript |
| AI Review | AI Analysis, Processing Job |
| Candidate Portal | Portal Profile, Reference, Join Link, Open Portal |
| Logs | Notification Log, Audit Log |
| (neeche) | Settings |

Pehle alag **Interviews** app tile thi; merge ke baad `desktop.remove_old_desk_items`
(`REMOVED_APPS = ["grid_interview"]`) use hata deta hai. Sidebar ya dock badlo to uska
`modified` badhao aur `bench migrate` chalao.

---

## 2. Data model — 16 DocTypes

| # | DocType | Type | Links to | Abhi chalu? |
|---|---|---|---|---|
| 1 | Settings | single | — | ✅ |
| 2 | Video Session | normal | Interview, Job Applicant (Dynamic Link) | ✅ |
| 3 | Participant Event | child (Video Session) | — | ✅ |
| 4 | Notification Log | normal | kisi bhi record se | ✅ |
| 5 | Portal Profile | normal | User | ✅ |
| 6 | Result | normal | Job Applicant, Interview | ✅ |
| 7 | Reference | normal | candidate User, Job Applicant | ✅ |
| 8 | Audit Log | normal | kisi bhi record se | ✅ |
| 9 | Access Session | normal | Video Session | ⏸ join links band |
| 10 | Consent | normal | Video Session, Interview | ⏸ recording band |
| 11 | Recording | normal | Video Session, Interview | ⏸ |
| 12 | Transcript | normal | Recording, Video Session, Interview | ⏸ |
| 13 | Transcript Segment | child (Transcript) | — | ⏸ |
| 14 | Analysis | normal | Interview, Transcript | ⏸ |
| 15 | Analysis Finding | child (Analysis) | — | ⏸ |
| 16 | Processing Job | normal | kisi bhi record se | ⏸ |

(Naam sab ke aage "Grid Interview " laga hai.) HRMS records **Dynamic Link** se jude hain, plain
Link nahi. Har DocType kisi ek feature ka **akela** data store hai, isliye koi bhi hatane se wo
feature band hota hai. Jo features Settings mein band hain, unke DocTypes khaali rehte hain.

```
HRMS Interview ──1:1── Video Session ──┬── Participant Event (child)      kaun kab aaya / gaya
   │                                   ├── Access Session               join links
   │                                   ├── Consent                      har vyakti ki permission
   │                                   └── Recording ── Transcript ── Analysis
   │                                                    └ Segment      └ Finding (child)
Job Applicant ── Result (HR publish karta hai)
              ── Reference (candidate jodta hai)
              ── Assessment Assignment (Recruitment Assessment module)
User (candidate) ── Portal Profile (+ private documents)
Notification Log · Processing Job · Audit Log ── kisi bhi record se (Dynamic Link)
```

---

## 3. Main flows

1. **Schedule:** HR → `scheduling.schedule_interview` → HRMS Interview (HR ke naam se) → Video
   Session → candidate account (invitation) → emails.
2. **Milan (har 5 minute, `run_cycle`):**

   | HRMS mein seedha badla | Grid Interview karta hai |
   |---|---|
   | Naya Interview (draft, aage ka, session nahi) | `manage_desk_interviews`: session, candidate account, invitations (setting `auto_manage_desk_interviews`) |
   | Interview time | Video Session update, purane join links revoke, "rescheduled" |
   | Interview cancelled | Video Session Cancelled, access revoke, "cancelled" |
   | Interview deleted | Video Session Cancelled, access revoke, audit; email nahi |
   | Interviewer joda | naye interviewer ko invitation |
   | Interviewer hataya | join nahi kar sakta (har join HRMS dobara check karta hai) |

   Phir due reminders (scheduler band raha ho to sirf latest), Email Queue status sync, failures
   par exponential backoff ke saath retry (*Max Email Attempts* tak). HR Settings ›
   *Send Interview Reminder* on ho to Grid Interview reminder nahi bhejta.
3. **Join:** portal ya join link → `meeting.join` (join window + participant check) → provider
   config (Jitsi JWT) → attendance events + presence (Redis) → lifecycle.
4. **Record:** sab consent → moderator start → Jibri upload (HMAC) → finalize job → storage →
   transcription job → analysis job → reviewers ko email.
5. **Decide:** HRMS feedback → reviewer AI draft dekhta hai → HR decision HRMS Interview par →
   HR Result publish → candidate portal par dekhta hai.

**Scheduling details:** HRMS Interview HR user ki apni permissions se banta / badalta hai. HRMS
time fields set-only-once hain, isliye reschedule write-permission check ke baad `db_set` karta
hai (HRMS ke apne Reschedule jaisa) aur timeline comment jodta hai. Schedule kar sakne wale roles:
`services/scheduling.py › SCHEDULER_ROLES`. Har email ek Notification Log row (unique idempotency
key) — dobara chalne par duplicate nahi.

---

## 4. Candidate portal — andar se

- `/candidate-portal` = `www/candidate-portal/` + `grid_interview/www_helpers.py`
  (`candidate_page`: portal on? login? candidate role? CSRF token banao) + `services/portal.py`.
- **Assessments section:** `www/candidate-portal/index.py` →
  `recruitment_assessment/portal.candidate_assessments(own_applicants(profile))`.
  Candidate ke Job Applicants (email se match) ke Assessment Assignments, status, `valid_till`.
  Button:
  - `token_used_on` khaali → `AssessmentAssignment.get_portal_link()` (token wala link);
  - warna `/assessment-portal?assignment=<name>` (browser session resume / temporary password).
  Assessment ka login, proctoring, scoring **bilkul nahi badla** — portal sirf dikhata hai.
- Candidates = Website Users, role *Grid Interview Candidate* (Desk nahi). Scheduling account
  khud banata hai; existing System User ko candidate role kabhi apne aap nahi milta.
- Documents = Portal Profile par private Files; download ownership check + audit.
- References: *Send Verification Email* reference lock karta hai; referee ko sirf candidate ka naam,
  position aur relationship dikhta hai. *Request Clarification* unlock karta hai. Link wali email
  body bhejne ke baad Email Queue se redact hoti hai.
- Results: published Result unpublish tak lock; email sirf "update available" kehti hai, text
  sign-in ke baad dikhta hai.

---

## 5. Video room

- `/interview/room?session=<id>`: device check, embedded meeting, details panel, Leave, aur
  moderators ke liye End interview. Interviewers ko candidate, round ki expected skills aur Job
  Applicant / HRMS Interview ke links bhi dikhte hain.
- **Providers** (`video/`): `Mock` (sirf apna camera, koi media nahi jaata; dev + tests) aur
  `Jitsi` (official IFrame API; har vyakti ka room-scoped HS256 JWT; moderator server decide karta
  hai; join window ke baad valid nahi; secret browser tak kabhi nahi jaata). Naya provider = ek class
  + `video/__init__.py` mein entry.
- **Kaun join kare:** jis candidate ka applicant email match ho, assigned interviewers (moderator),
  aur Grid Interview HR / Administrator (moderator) — sirf join window mein, non-terminal session mein.
  Har POST par CSRF token; passwordless link users ko custom header.
- **Lifecycle:** *In Progress* tab jab candidate aur interviewer dono aa jaayein; *Completed* jab
  moderator end kare (dono aaye the) ya window ke baad shaant ho; window ke baad interviewer akela
  → *No Show*, warna *Expired*.
- **Passwordless join links** (default band): ek vyakti, expiring, sirf hash. Link kholne se kuch
  nahi hota jab tak candidate *Continue* na dabaye (mail scanners use na kar sakein). Pehla use usi
  browser se HttpOnly cookie ke saath bind; baar-baar fail, reschedule, cancel, end → revoke.

---

## 6. Recording pipeline (default band)

**Consent.** Har badlav ek nayi Consent row (policy version ke saath); purana version count nahi
hota. Recording tabhi shuru jab meeting mein maujood har vyakti (candidate zaroori) ki current
consent ho. Bina consent koi aaye to recording rukti hai; withdrawal par rukti hai aur
*On Consent Withdrawal* ke hisaab se suspend / delete.

**Control.** Sirf moderator start / stop, aur sirf jab server maane: recording enabled, session
eligible + In Progress, consents poore, concurrency aur free-disk limits theek. Jitsi ka apna
record button chhupa hai; sabko server wala indicator dikhta hai.

**Jibri → server.** Jibri host HMAC-signed chunks mein `api/recording.ingest_*` par upload karta
hai (timestamp ±5 minute, replay protection, har chunk + poori file ka SHA-256). Bina authorised
recording wale room ka upload mana. Jibri ka uploader (`gi_finalize.py`) aur Jitsi / Jibri notes
purani `grid_interview` repo ke `deploy/` mein the, commit `ff19aa5` mein hataye gaye — recording
chalu karne se pehle wahan ki git history se wapas lao (`~/grid_interview`:
`git show ff19aa5^:deploy/jibri/gi_finalize.py`).

**Jobs.** Har step ek Processing Job (unique idempotency key + row lock):

```
Recording Finalize ──► Transcription ──► AI Analysis ──► interviewers + reviewers ko email
(reassemble, size,     (ffmpeg → mono     (schema, enums,
 SHA-256, ffprobe,      16 kHz, 10-min     timestamps, protected-
 max duration, store)   parts, provider)   trait check)
```

Fail → step rollback, exponential backoff ke saath retry (*Max Attempts per Job* tak), phir Failed
(administrator *Retry Now* kar sakta hai). Mare hue worker ke jobs scheduler wapas uthata hai.

**Transcription:** OpenAI-compatible `/audio/transcriptions`, ya *Mock*. Speaker pehchaan nahi,
isliye har segment *speaker uncertain*.

**AI draft:** input sirf transcript, job title, round aur expected skills. Prompt protected / job se
na jude traits mana karta hai; server schema, enums, timestamps validate karta hai, protected-trait
phrases check karta hai, transcript mein verbatim na mila "Transcript Fact" ko "Interpretation"
banata hai, bahut chhote transcript par "Insufficient Evidence". AI kabhi HRMS feedback nahi
likhta, Interview nahi badalta, candidate ka Result nahi banata.

**Storage:** *Local Private*: default `<site>/private/grid_interview_recordings` (`public/` aur
`private/files/` ke bahar, mode 640), `api/recording.stream` se (Range requests) permission check
ke baad, har play audit. *S3*: private bucket, SSE-AES256, size verify, presigned URLs; switch =
setting + purani files copy.

---

## 7. Security controls

| Area | Control |
|---|---|
| Identity | caller `frappe.session.user` ya hashed, browser-bound token se; user / role parameter nahi |
| Row level | `permission_query_conditions` + `has_permission` interviewers ko apne interviews tak |
| Tokens | 256-bit random, SHA-256 stored, expiring, revocable, GET redeem nahi karta |
| CSRF | portal website users ke liye token banata hai; link users ko custom header; `allow_cors` ko `*` kabhi nahi |
| Rate limits | registration, referee responses, link redemption, join / heartbeat / consent, uploads, ingest |
| Enumeration | registration aur password reset har email par same jawab |
| Uploads | extension allowlist, magic bytes, size / count limits, safe names, content check, optional malware hook (fail closed) |
| Recordings | web folders ke bahar, permission-checked streaming, audited plays, S3 SSE + chhote presigned URLs |
| Ingest | HMAC-SHA256 (timestamp, method, parameters); ±5 minute; replay cache; SHA-256 per chunk aur file |
| Secrets | Password fields, kabhi return / log nahi; Jitsi / webhook secrets ≥ 32 characters |
| Output | user text emails aur pages mein HTML-escaped |
| Audit | access, consent, recording, playback, publication, decisions, deletions, settings changes (sirf field names), refusals — append-only |

Guest endpoints (reviewed allowlist `tests/test_api_security.py` mein): `portal.register`,
`portal.submit_reference_response`, `video.room_info / join / heartbeat / leave / redeem_link`,
`recording.consent_status / set_consent`, `recording.ingest_start / ingest_chunk / ingest_complete`.

> `utils/security.py` ka fallback salt abhi bhi `"grid_interview"` hai (sirf jab site par
> `encryption_key` na ho). Ise **mat badlo** — pehle ke tokens / hashes tut jayenge.

Privacy: consent har vyakti aur purpose ke liye, policy version ke saath; external AI sirf settings
opt-in **aur** candidate ki External Processing consent se; retention har data type ke liye,
deletions audit. Production se pehle HR / legal consent text, privacy notice, retention aur external
provider approve kare.

---

## 8. Settings aur secrets

Sab **Grid Interview Settings** mein. Secrets Password fields hain (site ke `encryption_key` se
encrypted); image, git ya container env mein kuch secret nahi.

| Secret | Field | Kiske saath share | Kaise banao |
|---|---|---|---|
| Jitsi JWT secret | Jitsi JWT App Secret | Jitsi `JWT_APP_SECRET` | `openssl rand -hex 32` |
| Recording webhook secret | Recording Webhook Secret | Jibri host `GI_WEBHOOK_SECRET` | `openssl rand -hex 32` |
| S3 secret key (optional) | S3 Secret Access Key | AWS IAM | behtar: EC2 instance role |
| Transcription API key | Transcription API Key | provider | provider console |
| AI API key | AI API Key | provider | provider console |

Defaults: join window 15 minute pehle se 30 baad; reminders 1440, 60, 15 minute; emails 3
attempts; jobs 5 attempts, backoff 60 s se; recording 120 minute, 2048 MB, 1 concurrent;
retention: access sessions 90 din, recordings 180, transcripts + AI drafts 365, audit logs 1095,
notification logs 180.

**Features safely chalu karna** (hamesha pehle test site par):

| Step | Settings | Pehle chahiye |
|---|---|---|
| Portal | Public Portal Base URL, support contacts, privacy notice | HTTPS public address |
| Self-registration | Allow Candidate Self-Registration | approved privacy notice |
| Asli video | Video Provider = Jitsi, domain, app id, secret, audience | apna Jitsi host; do-browser test pass |
| Join links | Allow Passwordless Join Links | Jitsi chal raha ho |
| Recording | Enable Recording, consent text + version, webhook secret, limits | Jibri host (`snd-aloop` + uploader); ek poori recording test |
| S3 | Storage Backend = S3, bucket, region (IAM role) | private bucket, Block Public Access, SSE; purani files copy |
| Transcription / AI | Allow External Providers + provider fields | data processing terms; approved consent wording |

---

## 9. Retention

Roz ka job (`services/retention.run`) settings ke periods lagata hai (0 = rakho): recordings storage
se delete, row *Deleted* ke roop mein rehti hai (transcripts + AI drafts ke saath); expired
transcripts aur drafts erase; purane access sessions, notification logs, audit logs delete;
chhoote upload folders 48 ghante baad; 24 ghante se zyada Finalizing wali recordings Failed.
Suspended recordings kabhi auto-delete nahi. Har run sirf counts ke saath audit.

---

## 10. Operations — kuch atke toh

| Kahan | Theek | Kab action |
|---|---|---|
| Interview Dashboard tiles | failures = 0 | transcription / AI failures > 0: Processing Job kholo, fix, *Retry Now* |
| Processing Job | Succeeded | Failed, ya baar-baar Retry Scheduled |
| Notification Log | Sent | Failed: Email Account / Email Queue error |
| Recording | Stored | Failed ya 24 ghante se Finalizing: Jibri logs |
| Error Log | `grid_interview` entries nahi | koi bhi entry |
| Disk (Local Private) | *Minimum Free Disk* se upar | neeche gaya to nayi recording mana |

| Symptom | Wajah / fix |
|---|---|
| Site / `localhost:8080` timeout, websocket restart loop (WSL restart ke baad) | Docker ne griderp bridge ke iptables rules nahi banaye: Ubuntu mein `sudo systemctl restart docker` |
| Backend restart ke baad 502 | nginx ne purana backend IP cache kiya: frontend bhi restart |
| `KeyError: 'ContainerConfig'` on `docker-compose up` | compose v1 + nayi images: pehle app containers `docker rm -f` |
| Install / migrate par MariaDB deadlock 1213 | workers chal rahe the: scheduler + queue containers rok ke dobara |
| "not a candidate account" | email System User ka hai |
| Portal par interviews / assessments nahi | account email ≠ Job Applicant email |
| "You can join from …" | join window ke bahar |
| Room blank | Jitsi HTTPS par nahi, ya proxy X-Frame-Options / frame-ancestors bhejta hai |
| "Waiting for recording consent from …" | kisi ne consent nahi di (policy version match hona chahiye) |
| Jibri upload `Signature expired` | clock 5 minute se zyada aage / peeche: NTP |
| Reminders nahi | HR Settings › *Send Interview Reminder* on, ya offsets khaali |
| Do reschedule emails | HRMS ka Reschedule button use hua |

**Backups:** `bench backup --with-files` saare records aur portal documents leta hai. Recordings
nahi — Local Private folder alag backup karo (EBS snapshot) ya S3 use karo.

**Capacity (asli host par naapo):** backend par kam bojh (laptop test: 20–30 concurrent portal /
room requests par ~1.9 vCPU / 260 MB; heartbeats sirf Redis). Jitsi: kuch 1:1 interviews ke liye
2–4 vCPU, 4–8 GB RAM, UDP 10000 open. Jibri: har concurrent recording ~2 vCPU, 2–4 GB RAM.
Disk: 720p par ~0.5–1 GB per recorded ghanta × retention.

---

## 11. Merge aur deploy (grid_interview → grid_erp)

**Kya hua (2026-10-20, grid_erp 1.4.0, branch `merge-grid-interview`):**
- `~/grid_interview` (commit `4ea2a20`, wahi code jo griderp.in par chal raha tha) ka code
  `grid_erp/grid_interview/` mein copy; sirf import paths, template paths, asset paths, sidebar ka
  `app` aur `/interview-portal` → `/candidate-portal` badle.
- `hooks.py` mein Grid Interview ke hooks; `modules.txt` mein "Grid Interview"; dock mein Interviews.
- Candidate portal mein Assessments section (`recruitment_assessment/portal.py`).
- Patch `patches/grid_interview/adopt_grid_interview_app.py` (pre_model_sync): Module Def
  "Grid Interview" ka `app_name` = grid_erp, aur `grid_interview` installed apps se bahar.
  Koi DocType, table ya record delete nahi hota.

**Existing site par deploy ka order (zaroori):**
1. Backup: `bench --site <site> backup --with-files`.
2. Scheduler + queue containers rok do (background jobs purane `grid_interview.…` paths par na chalein).
3. Nayi image (bench par `apps/grid_interview` **nahi**, `apps/grid_erp` 1.4.0); `sites/apps.txt` se
   `grid_interview` hatao.
4. `bench --site <site> remove-from-installed-apps grid_interview` — **migrate se pehle**
   (`bench migrate` patch se pehle installed apps padhta hai).
5. `bench --site <site> migrate`, phir scheduler + queues chalu, `clear-cache`.

> **Dhyan:** Frappe 16 migrate mein "Removing orphan doctypes" hota hai — jis DocType ki file kisi
> installed app mein nahi milti, uska DocType record delete hota hai (table / data rehta hai).
> Isliye **grid_erp site par installed hona chahiye** jab grid_interview hataya jaye. Staging par
> grid_erp ke bina wala purana backup restore karke migrate kiya to saare Grid Interview DocTypes
> orphan maan ke hat gaye the; sahi backup (grid_erp installed) se sab theek raha.

> **Dhyan 2 (pehle se maujood logic, merge ne nahi badla):** Recruitment Assessment ka
> `after_migrate` (`install.sync_existing_job_applicant_assessments`) har migrate par saare Job
> Applicants check karta hai; **Shortlisted** applicant ka assignment na ho aur *Auto-send on
> Shortlist* ON ho to assignment banake **email bhej deta hai**. Migrate se pehle dekh lo kaun
> Shortlisted hai.

**Tests** (sirf test site par, kabhi production par nahi):

```bash
bench --site test.localhost set-config -p allow_tests 1
bench --site test.localhost run-tests --app grid_erp                                  # sab modules
bench --site test.localhost run-tests --module grid_erp.grid_interview.tests.test_portal  # ek file
```

---

## 12. Known limitations

- Whisper-style APIs se speaker labels nahi ("speaker uncertain").
- Reschedule sirf date + time badalta hai; interviewers HRMS Interview par badlo (agla cycle naye ko invite karega).
- Ek HRMS Interview = ek Video Session; panel round = ek session mein kai interviewers.
- Jitsi aur Jibri sirf configuration + unit tests se verify; asli do-browser call aur Jibri
  recording target infrastructure par test karni hai.
- Sirf-assessment wale candidate ka portal account apne aap nahi banta (interview schedule hone par
  banta hai, ya self-registration se); tab tak wo email link se test deta hai.
