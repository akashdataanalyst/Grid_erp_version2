# Grid AI

A ChatGPT-style AI Assistant for ERPNext / Frappe v16 that answers questions about ERP data in
English, Hindi and Hinglish — *"Is month total sales kitni hui?"*, *"Sabse zyada purchase kis supplier se hui?"* —
while respecting each user's ERPNext permissions.

Grid AI is the **Grid AI** module of `grid_erp` (folder `grid_erp/grid_ai/`). It was a separate
app (`grid_ai`); the merge moved files and renamed import paths only, the logic is unchanged.
It does not touch the other modules, and they do not depend on it.

## Code layout

```text
grid_erp/grid_ai/                 Module "Grid AI"
├── doctype/                      AI Settings, AI Tool Configuration (+ AI Tool Field),
│                                 AI Conversation, AI Chat Message, AI Query Log,
│                                 AI User Settings, AI Allowed Role
├── page/ai_assistant/            The chat page (/desk/ai-assistant)
├── report/ai_usage/              AI Usage report
├── api/                          chat.py (send / stream / history), my_ai.py (user's own model)
├── ai/                           orchestrator, prompts, provider base, user_choice
│   └── providers/                openrouter, ollama, openai, claude, gemini
├── tools/                        registry, discovery, permissions, query_builder, engine, …
├── install.py                    after_install / after_migrate, starter configurations
├── utils.py, exceptions.py       query log, rate limits, error types
└── tests/                        query whitelist, permissions, user choice

Elsewhere in grid_erp:
  hooks.py                        "Grid AI" section + entries in after_install/after_migrate,
                                  doc_events (DocType), scheduler_events (daily)
  patches/grid_ai/                adopt_grid_ai_app (old app → module) + the former v1 patches
  grid_ai/sidebar/grid_ai/        desk menu of the module (chat, usage, settings)
  dock/grid_erp/grid_erp.json     "AI Assistant" entry on the Grid ERP tile's rail
  public/images/grid_ai.svg
  deploy/grid_ai/egress_proxy.py  optional HTTPS egress proxy for AI provider APIs
```

## How it works

```
User ─► AI Assistant page (/desk/ai-assistant)
          │  grid_erp.grid_ai.api.chat.send_message  (user = frappe.session.user, nothing else trusted)
          ▼
     Orchestrator ── provider (OpenRouter / Ollama / OpenAI / Claude / Gemini)
          │  LLM returns a *structured* tool call, never SQL:
          │  {"doctype": "Sales Invoice", "operation": "sum", "field": "grand_total",
          │   "date_range": {"period": "this_month"}}
          ▼
     Engine: registry (is it enabled?) → DocType read permission → whitelist validation
             (operation / fields / filters) → field permlevel + record-filter checks
             → frappe.get_list as the session user (permission-filtered SQL, DB-side aggregation)
          ▼
     Aggregated result → LLM → answer
```

* **Dynamic**: any DocType — standard, custom, from other apps, added later — is used through
  configuration only. There is no per-DocType Python code.
* **Permission-first**: queries always run through `frappe.get_list` *without* `ignore_permissions`,
  so role permissions, User Permissions, permission query conditions, if-owner rules and sharing
  are applied in SQL. Restricted records are never fetched. Explicit filters on records the user may
  not see (e.g. another salesperson) are denied before querying.
* **No arbitrary SQL**: the LLM can only choose from whitelisted operations
  (count, list, search, sum, average, min, max, group_by), fields and operators.

## DocTypes

| DocType | Purpose |
|---|---|
| **AI Settings** (single) | Provider, API URL, API key (encrypted), model (dropdown per provider, live list after saving), temperature, tokens, timeout, streaming, tool-calling mode, context size, request and daily token limits, allowed roles, system prompt. |
| **AI Tool Configuration** | One record per DocType: enabled, AI description, allowed operations, allowed fields (display / filter / group-by / aggregate), default date field, limits. |
| AI Tool Field | Child table of allowed fields. Child table fields are written `items.item_code`. |
| AI Allowed Role | Child table of AI Settings: roles allowed to use the assistant (any one is enough). |
| AI Conversation / AI Chat Message | Chat history, visible only to its owner through the API, with the data views (source, record link, rows) and the user's 👍 / 👎 feedback. |
| AI Query Log | Audit log: user, conversation, DocType, operation, status, timing, provider, model, tokens. Never API keys, prompts or ERP values. |
| **AI Usage** (report) | Questions, failures, tokens and answer time per user or per day. |

Only **System Manager** can read or change AI Settings and AI Tool Configuration.

### Answers run in the background

`send_message` stores the question and queues the answer on the `short` queue; it returns at once.
The job runs as the same user, streams over realtime and fills the stored reply; the page also polls
`get_message`, so replies arrive even without realtime. A slow AI provider therefore never holds a
web worker. **At least one `short` queue worker must be running.**

### Company default and each user's own AI

AI Settings holds the **company default** (provider, model, key) that every user gets. With
*Users Can Choose Their Own Provider / Model* on, a user can open **AI Model** on the AI Assistant
page and pick OpenRouter, OpenAI, Claude or Gemini, a model, and their own API key (stored
encrypted in AI User Settings, never shown again; official API URLs only).

| User picks | Key used |
|---|---|
| Company default | company key |
| Same provider as the company, no own key | company key, if *Other Models on Company Key* allows that model (*Free models only* = OpenRouter free models; *Any model*; *Not allowed* = only the company model) |
| Anything, with own key | the user's key (their usage is not counted against *Tokens per User per Day*) |
| Another provider, no own key | not allowed |

If the admin later tightens the rules, affected users fall back to the company default; the dialog
says why. To reset a user, delete their AI User Settings record.

### Item-wise questions

Fields of a DocType's child tables can be added as `<table>.<field>` (e.g. `items.item_code`,
`items.qty`, `items.amount`) and used to filter, group and sum: *"Is month sabse zyada kaunsa item
bika?"*. One child table per query; document totals (grand_total) cannot be combined with line
fields, because the join repeats them per line. Sales / Purchase Invoice and Order come with item
fields preconfigured.

## Setup

1. **AI Settings** → choose provider, set API key and model, tick *Enabled*, *Save*, then *Test Connection*.
   * OpenRouter: `https://openrouter.ai/api/v1`, model e.g. `openrouter/free`.
   * Ollama: `http://PRIVATE_OLLAMA_IP:11434/v1`, no key. If the model has no function calling, set
     *Tool Calling Mode* = *Prompt JSON*.
   * Claude: uses the official `anthropic` SDK, default model `claude-opus-5-5`.
2. **AI Tool Configuration**: every DocType is discovered automatically as *disabled*. On first install,
   reviewed starter configurations are enabled for Lead, Customer, Supplier, Sales Invoice,
   Purchase Invoice, Sales Order, Purchase Order, Item, Bin, Employee and Attendance (when present).
3. Open **/desk/ai-assistant**, or on the desk: `/apps` → **Grid ERP** → **AI Assistant**. The
   rail entry follows module visibility (Role → Modules, module "Grid AI"); the page and the API
   additionally allow only users in AI Settings > Allowed Roles.

### Adding a DocType (incl. custom apps)

1. *AI Tool Configuration* list → *Discover DocTypes* (also runs daily, after migrate, and when a
   DocType is created).
2. Open the DocType's record → *Load Fields* → untick anything the AI should not see.
3. Write an *AI Description* (Hindi / Hinglish synonyms help), tick the operations, set the
   *Default Date Field*, tick *Enabled*, save.

No code changes or deploys are needed. Sensitive DocTypes (salary, bank, credentials, settings…)
need an extra review confirmation; security-critical DocTypes (User, Role, OAuth, logs…) and
password / secret / token fields can never be enabled.

## Deployment

Grid AI ships with `grid_erp`: install or migrate `grid_erp` as described in the main README.
`anthropic` (Claude provider) is a dependency of `grid_erp` in `pyproject.toml`.

### Sites that had the old `grid_ai` app

Data stays as it is (same DocTypes, same tables). On such a site:

1. Deploy a `grid_erp` that contains this module, on a bench **without** `apps/grid_ai`
   (remove it from the image and from `sites/apps.txt`).
2. Before migrating: `bench --site <site> remove-from-installed-apps grid_ai`.
   `bench migrate` reads the installed apps list before any patch runs and would fail on an app
   that is no longer on the bench.
3. `bench --site <site> migrate`. The patch `patches/grid_ai/adopt_grid_ai_app.py` moves
   Module Def "Grid AI" to `grid_erp` and marks grid_ai's already-run patches as done;
   `desktop.remove_old_desk_items` removes the old "Grid AI" app icon; the assistant is reached
   through the Grid ERP tile.

Never install the old `grid_ai` app next to this module: both would define module "Grid AI".

Optional egress proxy for hosts where containers cannot reach the AI provider directly:
`deploy/grid_ai/egress_proxy.py` (usage in its docstring).

## Security summary

* User identity comes only from `frappe.session.user`; the chat API accepts no user / role / key parameters.
* API keys are stored as Password fields, read only server-side, never logged or returned.
* Never "fetch then hide": permission checks happen before and inside the query.
* Prompt injection cannot widen access: the prompt is guidance, the server enforces everything.
* User-facing errors are short; technical details go to the Error Log.
* Questions and query results are sent to the configured AI provider. Free models (e.g. on
  OpenRouter) may log or train on them: use a paid provider with no data retention, or a private
  Ollama server, for sensitive data.

## Tests

`grid_erp/grid_ai/tests` checks the query whitelist, line-item rules, field permissions and model
validation. They only read metadata:

```bash
bench --site <site> run-tests --module grid_erp.grid_ai.tests.test_query_builder
bench --site <site> run-tests --module grid_erp.grid_ai.tests.test_permissions
bench --site <site> run-tests --module grid_erp.grid_ai.tests.test_user_choice
```

