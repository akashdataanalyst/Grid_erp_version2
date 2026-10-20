# Grid ERP

Frappe/ERPNext app for **Calco PolyTechnik Pvt Ltd**. One app, five modules:

| Module | Folder | What it does | Docs |
| --- | --- | --- | --- |
| Grid ERP | `grid_erp/grid_erp/` | Branding of login, desk home and workspaces; the "Grid ERP" apps tile | this README |
| Grid WhatsApp | `grid_erp/grid_whatsapp/` | WhatsApp messages and notification rules | `grid_erp/grid_whatsapp/README.md`, `docs/whatsapp.md` |
| Recruitment Assessment | `grid_erp/recruitment_assessment/` | Online candidate assessments for HRMS Job Applicants | `docs/recruitment.md`, `docs/recruitment-internals.md` |
| Grid AI | `grid_erp/grid_ai/` | AI Assistant: questions on ERP data in English / Hindi / Hinglish, within each user's permissions | `docs/ai.md` |
| Grid Interview | `grid_erp/grid_interview/` | Video interviews on HRMS Interview: scheduling, emails, room, recording, AI draft, results | `docs/interview.md`, `docs/interview-internals.md` |

Needs `erpnext` and `hrms`. Grid WhatsApp, Recruitment Assessment, Grid AI and Grid Interview were
separate apps (`grid_whatsapp`, `akash_recruitment_assessment`, `grid_ai`, `grid_interview`) and are
now modules of this app. The merge moved files and renamed import paths only; the logic is unchanged.

Candidates use **one portal, `/candidate-portal`**: their assessments, interviews, applications,
results, profile and references. Taking an assessment still opens `/assessment-portal`.

At a glance:

```text
grid_erp/
├── README.md                     module table + full file tree (below)
├── docs/                         whatsapp.md, recruitment*.md, ai.md, interview.md, interview-internals.md
├── deploy/                       docker/ (image layer), grid_ai/ (egress proxy)
└── grid_erp/
    ├── hooks.py                  one section per module
    ├── grid_erp/                 Branding module
    ├── grid_whatsapp/            WhatsApp module
    ├── recruitment_assessment/   Assessment module (doctypes, reports, workflow, api…)
    ├── grid_ai/                  AI Assistant module (doctypes, chat page, providers, tools…)
    ├── grid_interview/           Interview module (doctypes, pages, api, services, video…)
    ├── www/candidate-portal/     Candidate portal (assessments + interviews)
    ├── www/assessment-portal/    Taking an assessment (proctored)
    ├── www/interview/            Interview join link + video room
    ├── templates/                Interview emails, portal header
    ├── dock/                     the "Grid ERP" tile's module rail
    └── public/js|css/recruitment_assessment/, public/js|css|images/grid_interview/
```

On the desk (`/apps`) all five modules sit under one tile, **Grid ERP**. Its rail lists
WhatsApp, Recruitment Assessment, Interviews, AI Assistant and Branding; each opens its module's sidebar.

## Desk navigation (Frappe 16.51)

Frappe 16.51 builds the desk from three pieces of app data; grid_erp ships all three:

| Piece | File | What it does |
| --- | --- | --- |
| Apps screen tile | `hooks.py` → `add_to_apps_screen` | One tile, "Grid ERP", on `/apps`; opens `/desk/recruitment-assessment` |
| Dock (rail) | `grid_erp/dock/grid_erp/grid_erp.json` | Entries on the tile's rail, one per module sidebar |
| Sidebar | `<module>/sidebar/<module>/<module>.json` | The left menu of a module: Grid WhatsApp, Recruitment Assessment, Grid AI, Grid ERP |

A rail entry is shown only to users who may see its module (Role → Modules). After changing a
sidebar or the dock, bump its `modified` and run `bench migrate`. Older versions used a
"Business Suite" desktop folder and Workspace Sidebars, which Frappe 16.51 no longer loads;
`desktop.py` removes their leftovers on migrate.

## Branding

The branding changes presentation only: native routes, permissions and workspace
data stay as ERPNext defines them.

## Changing logo, text or colours

Everything visible is edited from the desk, with no code change:

1. Log in as `Administrator`.
2. Open **Grid Branding Settings** (`/desk/grid-branding-settings`).
3. Click **Unlock to Edit** and enter the branding password.
4. Edit, **Save**, then refresh the page.

| Section | Controls |
| --- | --- |
| Company | Company name, ERP name, footer version, browser tab titles |
| Images | Logo, favicon, home banner image (blank = built-in Calco image) |
| Banner Text | Tagline, capabilities, values, description, footer line, workspaces heading |
| Login Page | Heading, subtitle prefix, access and connection notices |
| Colours | Primary, primary dark, dark header, text, page background |
| Workspace Order | Optional home-page order, one workspace per line |

A blank field falls back to the built-in default in `calco_workspace_config.js`.

### Access and password

- Only the `Administrator` user can open the settings (`branding.py: has_permission`).
- Editing needs the branding password; a correct password unlocks editing for
  15 minutes in that login session. Five attempts are allowed per 5 minutes.
- The code stores only a hash of the password (`branding.py: DEFAULT_PASSWORD_HASH`).
  To use a different password on one site, set `grid_branding_password_hash` in
  that site's `site_config.json` to a hash from `frappe.utils.password.passlibctx.hash()`.

## Code layout

```text
grid_erp/                                  repository root
├── README.md                              this file
├── pyproject.toml
├── docs/
│   ├── whatsapp.md                        Grid WhatsApp guide
│   ├── recruitment.md                     Recruitment process, step by step (Hinglish)
│   ├── recruitment-internals.md           Recruitment internals: documents, fields, flow
│   ├── ai.md                              Grid AI: how it works, setup, migrating from the grid_ai app
│   ├── interview.md                       Interview process + candidate portal, step by step (Hinglish)
│   └── interview-internals.md             Interview internals: code, flows, security, deploy
├── deploy/
│   ├── docker/                            Image layer (Containerfile, build.sh)
│   └── grid_ai/egress_proxy.py            Optional HTTPS egress proxy for AI provider APIs
└── grid_erp/                              the Python package (the app)
    ├── hooks.py                           All hooks, one section per module
    ├── modules.txt                        Grid ERP, Grid WhatsApp, Recruitment Assessment, Grid AI, Grid Interview
    ├── patches.txt / patches/             Data patches (patches/recruitment_assessment/, patches/grid_ai/, patches/grid_interview/)
    ├── fixtures/                          "Assessment Invitation" Email Template
    │
    │   ── shared app files ──
    ├── assets.py                          Serves public/ files through the backend
    ├── branding.py                        Serves branding settings to desk/login; edit lock
    ├── desktop.py                         Removes desk icons / sidebars of older versions on migrate
    ├── dock/grid_erp/grid_erp.json        "Grid ERP" tile rail: WhatsApp, Recruitment, Interviews, AI Assistant, Branding
    │
    │   ── modules (each has sidebar/<module>/ = its left menu on the desk) ──
    ├── grid_erp/                          Module "Grid ERP"
    │   └── doctype/grid_branding_settings/, sidebar/grid_erp/
    ├── grid_whatsapp/                     Module "Grid WhatsApp"
    │   ├── doctype/                       WhatsApp Instance, Settings, Template, Notification Rule/Log …
    │   ├── sidebar/grid_whatsapp/         Desk menu
    │   ├── services/                      notification engine, dispatcher, providers (Evolution, eValidation)
    │   ├── api/                           webhook, notification APIs
    │   └── install.py, seed.py, seed_data/, tests/, utils/
    ├── recruitment_assessment/            Module "Recruitment Assessment"
    │   ├── doctype/                       Assessment Template/Assignment/Attempt/Result, Question Bank, Settings …
    │   ├── report/                        Leaderboard, pending/completed/expired, violations, analytics
    │   ├── workspace/                     "Recruitment Assessment" workspace page (the tile's home)
    │   ├── sidebar/recruitment_assessment/  Desk menu (assessments, results, reports, setup)
    │   ├── workflow.py                    Template choice + auto-send on Shortlist
    │   ├── service.py                     Assignment, login, start, autosave, submit, scoring
    │   ├── api.py                         Whitelisted APIs for the portal and HR
    │   ├── notifications.py               Invitation email
    │   ├── install.py, seed.py            Custom fields, roles, starter templates
    │   └── scheduler.py, evaluation.py, ai_evaluation.py, certificate.py, …
    ├── grid_ai/                           Module "Grid AI" (details: docs/ai.md)
    │   ├── doctype/                       AI Settings, AI Tool Configuration, AI Conversation, AI Query Log …
    │   ├── page/ai_assistant/             Chat page (/desk/ai-assistant)
    │   ├── sidebar/grid_ai/               Desk menu (chat, usage, settings)
    │   ├── report/ai_usage/               Questions, failures, tokens per user / day
    │   ├── api/                           chat.py, my_ai.py (whitelisted APIs for the page)
    │   ├── ai/                            Orchestrator, prompts, providers (OpenRouter, Ollama, OpenAI, Claude, Gemini)
    │   ├── tools/                         Registry, discovery, permissions, query builder, engine
    │   └── install.py, utils.py, exceptions.py, tests/
    ├── grid_interview/                    Module "Grid Interview" (details: docs/interview-internals.md)
    │   ├── doctype/                       Video Session, Recording, Transcript, Analysis, Result, Reference, Settings …
    │   ├── page/                          Interview Dashboard, Interview Scheduler
    │   ├── sidebar/grid_interview/        Desk menu
    │   ├── api/ → services/               Browser calls api/; rules, emails, status live in services/
    │   ├── integrations/hrms_adapter.py   The only code that reads HRMS
    │   └── video/, utils/, setup/, permissions.py, www_helpers.py, tests/
    │
    ├── www/candidate-portal/              Candidate portal (/candidate-portal): assessments, interviews, results, profile
    ├── www/assessment-portal/             Taking an assessment (/assessment-portal)
    ├── www/interview/                     Interview join link and video room
    ├── templates/emails/, templates/includes/  Interview emails, portal header
    └── public/
        ├── js/
        │   ├── calco_workspace_config.js  Branding: built-in defaults, merged with the settings
        │   ├── calco_branding.js          Branding: login panel, home banner, footer, brand bar
        │   ├── workspace_presentation.js  Branding: icon + description on workspace tiles
        │   └── recruitment_assessment/    Desk form scripts (job_applicant.js, assessment_*.js …)
        │                                  + proctoring.js (candidate portal)
        ├── css/
        │   ├── calco_branding.css, workspace_presentation.css
        │   └── recruitment_assessment/proctoring.css
        └── images/                        Default logo, favicon, banner, grid_erp.svg (tile), grid_ai.svg
```

How the settings reach the page: `branding.py` adds them to `frappe.boot` on
the desk and to an inline `<script>` in `<head>` on website/login pages.
`calco_workspace_config.js` merges them over its defaults into
`window.calcoWorkspaceViewConfig`, which `calco_branding.js` reads.

## How CSS, JS and images load

Branding files: `assets.py` serves everything under `public/` from the Python backend at
`/api/method/grid_erp.assets.serve?path=<file>&v=<hash>`, not from `/assets/`.
So nothing has to be built, copied or symlinked, and nginx needs no change:
the files load on any bench or Docker setup where the app is installed.

`<hash>` is computed from the files themselves, so after editing anything in
`public/` browsers fetch the new copy automatically. No version bump is needed.

Recruitment Assessment files load the normal Frappe way: desk form scripts through
`doctype_js` in `hooks.py`, and the portal page from `/assets/grid_erp/...`. After
changing `proctoring.js` or `proctoring.css`, bump `?v=` in
`www/assessment-portal/index.html`.

## Recruitment Assessment flow

```text
Job Applicant marked Shortlisted
  → hooks.py doc_events → recruitment_assessment/workflow.py (template: Job Opening, else default)
  → service.create_assignment → notifications.py emails the link immediately
  → candidate takes the test at /assessment-portal (proctoring.js, api.py)
  → Assessment Attempt (answers + one camera video with audio) → Assessment Result
  → HR scores on the Attempt form → Job Applicant "Assessment" tab updates
```

- Auto-send on Shortlist: **Assessment Settings > Auto-send Assessment on Shortlist**
  (on by default), or per Job Opening with *Auto-assign on Shortlist*.
- Manual: Job Applicant > Assessment > **Assign Assessment** (or **Assign Another Assessment**)
  to send any template.
- Step-by-step process: `docs/recruitment.md`. Documents, fields, code map:
  `docs/recruitment-internals.md`.

## Installing on any server

```bash
bench get-app https://github.com/akashdataanalyst/ErpNext-Demo.git
bench --site <site> install-app grid_erp   # existing site: bench --site <site> migrate
```

In Docker, the app code must be present in every Python container (backend,
queue-short, queue-long, scheduler), because they share one Redis cache.
After pulling new code, restart those containers and run
`bench --site <site> migrate`.

This setup (`~/frappe_docker/pwd.yml`) bind-mounts `custom_apps/hrms` and
`custom_apps/grid_erp` into every container, so code changes need no `docker cp`:

```bash
cd ~/frappe_docker
docker-compose -p frappe_docker -f pwd.yml restart backend queue-short queue-long scheduler
docker exec frappe_docker_backend_1 bench --site ampugerp.in migrate
docker exec frappe_docker_backend_1 bench --site ampugerp.in clear-cache
```

After editing `public/js/recruitment_assessment/proctoring.js` or its CSS, bump `?v=` in
`www/assessment-portal/index.html`.

## Version

1.4.0 (grid_interview merged as module Grid Interview; one candidate portal `/candidate-portal`).
On a site that had the `grid_interview` app: `bench --site <site> remove-from-installed-apps grid_interview`
before `bench migrate` (see `docs/interview-internals.md` › 11).

1.3.0 (desk navigation for Frappe 16.51; Drive removed from the image)


#test

