> **Merged into grid_erp (5 Oct 2026).** This module now lives in `grid_erp/grid_erp/grid_whatsapp/`;
> Python paths are `grid_erp.grid_whatsapp.*`, hooks are in `grid_erp/hooks.py`, and there is no
> separate `grid_whatsapp` app to install. Install/run-tests commands below that name `grid_whatsapp`
> now use `grid_erp` (e.g. `run-tests --module grid_erp.grid_whatsapp.tests.test_notification_engine`).

# Grid WhatsApp

A configuration-driven WhatsApp notification engine for Frappe / ERPNext.

Administrators decide in the desk **which document, which event, which
condition, which template, which WhatsApp account and which people**. The code
contains no DocType names, field names, people, numbers or business rules. It
reads all of them from the configuration.

This app is self-contained. It does not modify frappe, erpnext, hrms, grid_erp
or any other app. It listens to document events through one wildcard hook and
does nothing for DocTypes without an enabled rule.

Tested with Frappe 16.36, ERPNext 16.37 and HRMS 16.20 on Python 3.14.

---

## Contents

1. [Architecture](#architecture)
2. [Installation](#installation)
3. [DocTypes](#doctypes)
4. [Configuration walkthrough](#configuration-walkthrough)
5. [Template variables](#template-variables)
6. [Recipient resolution](#recipient-resolution)
7. [Conditions](#conditions)
8. [Events and duplicate prevention](#events-and-duplicate-prevention)
9. [API provider and the eValidation integration point](#api-provider-and-the-evalidation-integration-point)
10. [Background queue and retries](#background-queue-and-retries)
11. [Logs](#logs)
12. [Permissions and security](#permissions-and-security)
13. [Sample configurations](#sample-configurations)
14. [Testing](#testing)
15. [Troubleshooting](#troubleshooting)

---

## Architecture

```text
ERPNext / HRMS / Grid ERP            (unchanged)
        │  document events: after_insert, on_update, on_submit, on_cancel, on_change
        ▼
hooks.py  doc_events["*"]  ──►  services/notification_engine.handle_doc_event
        │  1 Redis read: does this DocType have enabled rules?  (no → return)
        │  match rule event · value change · workflow state
        │  services/condition_engine        safe condition check
        ▼
frappe.enqueue(after commit)         the document's save/submit never waits for WhatsApp
        ▼
notification_engine.process_trigger   (background worker)
        │  services/template_renderer       variables → message text
        │  services/field_resolver          field paths, links, child tables (metadata driven)
        │  services/recipient_resolver      people → mobile numbers, de-duplicated
        │  utils/phone                      number normalisation
        │  WhatsApp Notification Log        one per recipient, idempotency key
        ▼
services/dispatcher.send_log
        │  services/whatsapp_provider       provider interface + registry
        │  services/providers/evalidation   eValidation (request format: TODO, see below)
        ▼
WhatsApp   ──►  log: Sent / Failed / Queued for retry  ──►  Delivered / Read (webhook)
```

```text
grid_whatsapp/
├── hooks.py                      wildcard doc events, retry scheduler, provider registry
├── install.py                    creates the "WhatsApp Manager" role
├── api/
│   ├── notification.py           Preview, Send Now, Resend (desk buttons)
│   └── webhook.py                delivery-status callback endpoint
├── services/
│   ├── notification_engine.py    rule cache, event matching, idempotent log creation
│   ├── condition_engine.py       AST-based safe expression evaluator
│   ├── template_renderer.py      {{placeholder}} rendering and variable resolution
│   ├── field_resolver.py         dot paths through Link / Dynamic Link / Table fields
│   ├── recipient_resolver.py     recipient types, contact resolution, filters
│   ├── dispatcher.py             send, retry, status updates
│   ├── whatsapp_provider.py      provider base class, HTTP error mapping, registry
│   ├── errors.py                 permanent vs temporary error classes
│   └── providers/evalidation.py  eValidation provider (isolated)
├── utils/                        phone normalisation, log redaction, debug logger
├── grid_whatsapp/doctype/       the 7 DocTypes
└── tests/                        Frappe integration tests
```

## Installation

### On a normal bench

```bash
bench get-app <git URL or local path of grid_whatsapp>
bench --site <site> install-app grid_whatsapp
bench --site <site> migrate
bench restart
```

The app has no Python dependencies beyond Frappe. It does not need `bench build`,
because the desk scripts ship as DocType JS.

### With this frappe_docker setup (no custom image)

The app lives in `custom_apps/grid_whatsapp` and `pwd.yml` mounts it into every
container (`x-custom-apps-volumes` and `PYTHONPATH` in `x-custom-apps-env`).

```bash
docker-compose -f pwd.yml up -d            # or: down + up if compose v1 errors with 'ContainerConfig'
docker exec frappe_docker_backend_1 bench --site <site> install-app grid_whatsapp
docker exec frappe_docker_backend_1 bench --site <site> migrate
```

Every Python container needs the code: backend, queue-short, queue-long and
scheduler. The **queue workers send the messages** and the **scheduler runs the
retries**.

### After installing

1. **WhatsApp Instance**: add the API account (see [eValidation](#api-provider-and-the-evalidation-integration-point)).
2. **WhatsApp Settings**: set Default Country Code and the default instance. Switch on
   **Test Mode** until the eValidation request format is implemented.
3. **WhatsApp Template**, then **WhatsApp Notification Rule**.

## DocTypes

| DocType | Kind | Purpose |
| --- | --- | --- |
| WhatsApp Settings | Single | Default instance, default country code, background sending, retries, test mode, failure behaviour, debug logging |
| WhatsApp Instance | Master | One WhatsApp API account (provider, URL, key/token as Password fields, sender, timeout, SSL, webhook secret). Use as many as needed, e.g. Purchase / Sales / HR |
| WhatsApp Template | Master | Message text with `{{placeholders}}`, provider template name/id, language, category |
| WhatsApp Template Variable | Child of Template | Where each placeholder's value comes from |
| WhatsApp Notification Rule | Master | Document type, event, condition, template, instance, recipients, delivery options |
| WhatsApp Notification Recipient | Child of Rule | One source of people: field, link, employee, role, approver, fixed number... |
| WhatsApp Notification Log | Log | One row per message and recipient: status, message, provider response, retries, timestamps |

## Configuration walkthrough

### WhatsApp Template

| Field | Example |
| --- | --- |
| Template Name | Purchase Order Submitted |
| Used For DocType (optional) | Purchase Order. Field paths are then checked on save |
| Provider Template Name / ID | as approved at the provider |
| Language | en |
| Message Body | `Dear {{supplier_name}}, PO {{po_number}} for {{grand_total}} has been issued by {{company}}.` |

Click **Detect Variables** to add one row per placeholder, then fill in the
**Source Field** of each. The variable order in the table is the parameter order
sent to providers that use positional parameters (`{{1}}`, `{{2}}`, ...).

### WhatsApp Notification Rule

| Field | Meaning |
| --- | --- |
| Document Type | Any non-child DocType, standard or custom |
| Send When | See [Events](#events-and-duplicate-prevention) |
| Field / Workflow State | For On Value Change / Workflow State Change |
| Condition Type / Condition | `Always`, or an [expression](#conditions) |
| WhatsApp Template | Template to send |
| WhatsApp Instance | Blank = default instance from Settings |
| Priority | Higher first when several rules match |
| Recipients | One row per source of people |
| Send in Background | On by default; also needs the Settings switch |
| Send on Every Trigger | Off = once per document per trigger (see below) |
| Allow Duplicate Recipient | Off = one message per number even if several rows resolve to it |

The rule form has three buttons:
- **Preview** shows, for a chosen document, the condition result, the rendered
  message, the resolved numbers and every problem. It sends nothing.
- **Send Now** sends the rule's message for a chosen document, ignoring the event and condition.
- **View Logs** opens the log list for this rule.

## Template variables

| Source Type | Source Field | Value |
| --- | --- | --- |
| Document Field | a field path | the field's value, formatted as in the desk (currency, dates, numbers) unless **Raw Value** is ticked |
| Static Value | none | **Default Value** |
| System Value | `today`, `now`, `document_name`, `document_type`, `document_url`, `site_url`, `current_user` | |

**Field paths** follow the DocType metadata:

| Path | Reads |
| --- | --- |
| `grand_total` | field on the document |
| `supplier.supplier_name` | follow the Link field `supplier` into the Supplier |
| `supplier.supplier_primary_contact.mobile_no` | follow links as deep as 6 levels |
| `company.company_name` | any Link: Company, Employee, User, Customer, Lead, Contact, custom DocTypes... |
| `items.item_code` | every row of the child table, joined with ", " |
| `items.0.item_code` | first row only |

Dynamic Link fields are followed too. An empty link or missing row gives an
empty value, which falls back to **Default Value**. If the variable is
**Required** and still has no value, the message fails with a clear error.
Password fields can never be read. When a rule or template is saved, the paths
are checked against the DocType, and the saving user must be able to read every
DocType a path passes through.

Placeholders are replaced as plain text. Templates are not Jinja and cannot run code.

## Recipient resolution

Each recipient row is one source of people. **Source Field** is always a field
path on the document.

| Recipient Type | Configure | Resolves to |
| --- | --- | --- |
| Document Field | Source Field = field holding a number (`contact_mobile`, `supplier.mobile_no`) | that number |
| Document Link | Source Field = a Link / Dynamic Link field (`supplier`, `customer`, `party`) | the linked record's number, via Contact Resolution |
| Employee / User / Customer / Supplier / Lead / Contact | Source Field holding that record (`employee`, `owner`, `customer`), **or** a Fixed Record | the record's number. A User id given for Employee is mapped through the Employee's User link |
| Role | Fixed Record = the Role | every enabled user with the role |
| Workflow Approver | nothing | users allowed to take the next action from the document's current workflow state (self-approval rules respected) |
| Fixed Number | Fixed Number | that number |

**Contact Resolution** sets how a record's number is found:

| Option | Looks at |
| --- | --- |
| Primary Contact | the record's Link-to-Contact field (e.g. `supplier_primary_contact`, `customer_primary_contact`) → the Contact's mobile |
| Record Field | a phone field on the record itself (e.g. Supplier `mobile_no`, Employee `cell_number`, Lead `whatsapp_no`). For a User, their Employee / Contact if the User has none |
| Linked Contact | Contacts linked to the record (Contact > Links), primary contact first |
| **Auto** (default) | Primary Contact → Record Field → Linked Contact |

**Mobile Field** (fieldname or label) picks the field to read. When it is blank,
the first phone-like field is used: WhatsApp fields first, then mobile/cell, then
phone. Emergency and fax numbers come last.

**Filter People** (Role, Workflow Approver, Employee): notify only people whose
Employee record's *Employee Field* equals the document's *Document Field*, e.g.
`department` = `department`, or `company` = `company`.

**Required**: when a required row finds no number, a Failed log records why. A
row that is not required is skipped silently (with Debug Logging, the reason is
written to `logs/grid_whatsapp.log`). An invalid number is always logged.

**Duplicate recipients**: rows that resolve to the same number send one message,
and the log label shows both rows, e.g. `Purchase Manager + Buyer`. Tick
**Allow Duplicate Recipient** on the rule to send one message per row.

### Phone numbers

Spaces, brackets, hyphens, dots and slashes are removed. `+` and `00` mark
international numbers, which are kept as they are. Other numbers lose their
trunk `0` and get the **Default Country Code** from Settings. A number that
already starts with the country code and is longer than 10 digits is treated as
international (`919876543210` → `+919876543210`). Valid results have 8-15 digits.
No country is assumed: without a default country code, local numbers are rejected
with a clear error.

## Conditions

Conditions are parsed with Python's `ast` module and interpreted by
`condition_engine.py`. They are **never** passed to `eval`, `exec` or
`frappe.safe_eval`.

| Allowed | Examples |
| --- | --- |
| Fields and paths | `docstatus`, `grand_total`, `supplier.supplier_group`, `items.item_code` (list) |
| Previous value (before this save) | `previous.status != status` |
| Literals | `"text"`, `10`, `2.5`, `True`, `False`, `None`, `["A", "B"]` |
| Comparison | `== != < <= > >= in not in is None is not None`, chained `1000 < grand_total <= 5000` |
| Logic | `and or not ( )` |
| Arithmetic | `+ - * / // %` |

Examples:

```text
docstatus == 1
custom_approval_status == "Approved"
grand_total > 500000
company == "ABC" and supplier.supplier_group in ["Raw Material", "Packing"]
"ITEM-001" in items.item_code
transaction_date >= "2026-04-01"
previous.status == "Draft" and status == "To Receive and Bill"
```

Not allowed, and rejected when the rule is saved: function calls, `[ ]`
indexing, attribute access on anything but field names, double underscores,
lambdas and comprehensions. Missing fields or empty links evaluate to `None`.
Ordering comparisons against `None` are False. Date fields compare with date
strings, and number fields with numeric strings.

## Events and duplicate prevention

| Send When | Fires on | Default trigger key (one message per document and recipient) |
| --- | --- | --- |
| After Insert | document created | once |
| After Save | every save of a draft / non-submittable document | once (first matching save) |
| After Submit | submit | once |
| After Cancel | cancel | once |
| On Update | any change: save, submit, cancel, update after submit | once |
| On Value Change | a chosen field changed (also on creation) | once **per new value**, e.g. `status=Approved` |
| Workflow State Change | the workflow state changed (optionally to one state) | once per state |
| Manual | only the **Send Now** button | every time |

**Idempotency**: every message has a key built from rule + document + trigger
+ recipient number. It is stored in the log's unique `idempotency_key`. A
trigger whose key already has a Queued / Processing / Sent / Delivered / Read
log is skipped, so repeated saves, retried jobs and parallel workers cannot send
the same message twice. A Failed or Cancelled log is reused and tried again
when the trigger happens again.

**Send on Every Trigger** adds the document's modified time to the key, so every
matching save or change sends a new message.

**Explicit resend**: open a log and click **Resend** for a sent message, which
creates a new log, or **Retry Now** for a failed one. This needs **Allow Manual
Resend** in Settings.

Workflow approval events: use *Workflow State Change*, optionally with a target
state, or *On Value Change* on any approval field (`custom_approval_status`). Use
*Workflow Approver* recipients to notify whoever must act next.

Performance: for each document event the engine reads one cached list of
"DocTypes with enabled rules" from Redis. DocTypes without rules return
immediately with no database query (covered by a test). Rules are cached per
DocType and the cache is cleared whenever a rule is saved, renamed or deleted.
Events during install, migrate, patches, data import and setup wizard are
ignored. Code can set `doc.flags.skip_whatsapp_notifications = True` to
silence one save.

## API provider and the eValidation integration point

The engine only talks to `WhatsAppProvider.send(OutgoingMessage) -> ProviderResult`
(`services/whatsapp_provider.py`). Providers are registered through a hook, so
another app can add one without touching this app:

```python
# hooks.py of any app
whatsapp_providers = {"My Provider": "my_app.whatsapp.MyProvider"}
```

`OutgoingMessage` carries the recipient (`+919876543210`), rendered body,
variables (dict, template order), `parameters` (list), provider template
name/id, language and a unique reference (the log name). `http_request()` maps
HTTP and network failures to the engine's error classes:

| Situation | Error class | Retried? |
| --- | --- | --- |
| Timeout | ProviderTimeoutError | yes |
| Connection error, HTTP 429, HTTP 5xx | ProviderUnavailableError | yes |
| HTTP 401 / 403 | AuthenticationError | no |
| Other HTTP 4xx | ProviderError | no |
| Invalid / missing number | InvalidRecipientError | no |
| Template not approved / wrong parameters | TemplateRejectedError (raise it from the provider) | no |
| Missing required variable | VariableError | no |
| Inactive instance/template, bad configuration | ConfigurationError | no |

### eValidation: what still has to be filled in

`services/providers/evalidation.py` is the **only** eValidation-specific file.
The eValidation API documentation was not available, so its request format is
**deliberately not guessed**. Three clearly marked methods must be completed
from the documentation:

1. `build_send_request(message)`: endpoint path, auth header, payload (where the
   number, template name/id, language and parameters go, and the number format).
2. `parse_send_response(status_code, body)`: the message id field, and which
   error codes mean invalid number / template rejected.
3. `parse_status_callback(payload)`: optional, for delivered/read receipts.

Until then, every real send fails at once with *"The eValidation API request
format has not been configured yet ..."* and is not retried. Use **Test Mode**
in WhatsApp Settings to run rules end to end: messages are resolved, rendered
and logged as Sent with a `test_mode` response, without calling any API.

**WhatsApp Instance** fields: Provider, API Base URL, API Key and API Token
(encrypted Password fields), Provider Instance ID, Sender Number, Timeout,
Verify SSL, Webhook Secret.

**Delivery receipts**: give the provider
`https://<site>/api/method/grid_erp.grid_whatsapp.api.webhook.status_callback?instance=<Instance name>`
and have it send the instance's **Webhook Secret** in the `X-Webhook-Secret`
header. Requests with a wrong secret are refused. Status never moves backwards:
a late "delivered" receipt does not undo "read".

## Background queue and retries

- With **Send in Background** on (Settings and rule, the default), the document
  event only evaluates the rule and enqueues a job *after the document's
  transaction commits* (queue `short`, de-duplicated job id). Rendering,
  recipient lookup and the API call happen in the worker. A slow or unavailable
  API never delays or blocks a save/submit, and a rolled-back save sends
  nothing.
- With it off, the message is processed inside the save. Use this only for
  debugging.
- **Retries** (Settings): Retry Failed Messages, Max Retries (default 3), Retry
  Delay in minutes (default 5). A temporary failure puts the log back to
  *Queued* with `retry_count` and `next_retry_at`. The scheduler job
  `process_due_retries` (every minute) re-queues due logs, so retries survive
  worker restarts. When retries run out the log becomes *Failed* with the last
  error. Permanent errors fail at once.
- **Failure behaviour** (Settings) only applies to errors *while the document is
  saved*, such as a broken condition: Log Only (default), Show Message, or Block
  Document. API failures never affect documents.

## Logs

**WhatsApp Notification Log** has one row per message and recipient:
reference document, rule, trigger, template, instance, recipient number, label
and type, the rendered message and variables, provider message id / request
reference / response, error, retry count, next retry, and last attempt, sent,
delivered and read times.

Statuses: `Queued → Processing → Sent → Delivered → Read`, or `Failed` / `Cancelled`.

Configuration problems also appear here as Failed rows, so one list shows
everything: missing template, inactive instance, missing or invalid number,
missing variable, invalid condition. Unexpected exceptions also go to
**Error Log** with a traceback.

Responses and errors are **redacted** before they are stored. Values under keys
like `token`, `password`, `secret`, `api_key` and `authorization` are masked,
the instance's own key/token/webhook secret are replaced wherever they appear,
and text is truncated to 10,000 characters. Old logs are removed through
**Log Settings** (default 180 days; queued messages are kept).

## Permissions and security

| Role | Settings | Instance | Template / Rule | Log |
| --- | --- | --- | --- | --- |
| System Manager | full | full | full | read, delete |
| WhatsApp Manager (created by the app) | none | read (secrets stay hidden) | full | read |
| Others | none | none | none | none |

- API key, token and webhook secret are Frappe Password fields, stored encrypted
  and never sent to the browser. They are decrypted only inside the provider call.
- No user-entered text is ever executed: conditions go through the AST
  interpreter, templates through plain substitution.
- Field paths cannot read Password fields. The rule author must have read
  permission on every DocType a path touches.
- Preview / Send Now / Resend need System Manager or WhatsApp Manager and read
  permission on the document.
- Logs cannot be created or edited from the desk.

## Sample configurations

These are examples only. None of them exist in code. Create them in the desk.

### 1. Purchase Order submitted → supplier and purchase manager

**WhatsApp Instance**: `Purchase API` (provider eValidation, its URL and key).

**WhatsApp Template** `Purchase Order Submitted` (Used For DocType: Purchase Order):

```text
Dear {{supplier_name}}, Purchase Order {{po_number}} dated {{po_date}} for {{grand_total}} has been issued by {{company}}.
```

| Variable | Source Type | Source Field |
| --- | --- | --- |
| supplier_name | Document Field | supplier_name |
| po_number | Document Field | name |
| po_date | Document Field | transaction_date |
| grand_total | Document Field | grand_total |
| company | Document Field | company |

**Rule** `PO Submitted`: Document Type `Purchase Order`, Send When `After Submit`,
Condition `Always`, Template `Purchase Order Submitted`, Instance `Purchase API`.

| Label | Recipient Type | Source Field | Fixed Record | Contact Resolution | Mobile Field |
| --- | --- | --- | --- | --- | --- |
| Supplier | Document Link | supplier | | Auto | mobile_no |
| Purchase Manager | Role | | Purchase Manager | Record Field | |

Optionally add *Filter People*: Employee Field `company`, Document Field `company`.

### 2. Quality Inspection accepted → supplier

**Template** `Quality Accepted`: `Material {{item}} ({{reference}}) passed quality inspection on {{date}}.`
Variables: item → `item_code`, reference → `reference_name`, date → `report_date`.

**Rule**: Document Type `Quality Inspection`, Send When `After Submit` (or
`After Save`), Instance `QA WhatsApp`, Condition `Expression`:

```text
status == "Accepted" and reference_type in ["Purchase Receipt", "Purchase Invoice"]
```

| Label | Recipient Type | Source Field | Contact Resolution |
| --- | --- | --- | --- |
| Supplier | Document Link | reference_name.supplier | Auto |

Quality Inspection has no supplier field of its own. `reference_name` is a
Dynamic Link to the inspected receipt/invoice, and `.supplier` follows it to that
document's supplier. The condition limits the rule to references that have a
supplier. Use **Preview** on an existing inspection to check before enabling.

### 3. Sales Order approved → customer and sales person

Assuming a custom field `custom_approval_status` on Sales Order:

**Template** `Sales Order Approved`: `Dear {{customer}}, your order {{so}} for {{total}} is approved. Delivery by {{delivery}}.`
Variables: customer → `customer_name`, so → `name`, total → `grand_total`, delivery → `delivery_date`.

**Rule**: Document Type `Sales Order`, Send When `On Value Change`, Field
`custom_approval_status`, Condition `custom_approval_status == "Approved"`,
Instance `Sales WhatsApp`. With a Workflow, use `Workflow State Change` with
Workflow State `Approved` instead.

| Label | Recipient Type | Source Field | Contact Resolution |
| --- | --- | --- | --- |
| Customer | Document Link | customer | Auto |
| Sales Person | Employee | sales_team.0.sales_person.employee | Record Field |

`sales_team.0.sales_person.employee` follows the first Sales Team row to the
Sales Person record and then to its Employee.

## Testing

Tests use Frappe's test framework (`IntegrationTestCase`). They create a
submittable test DocType (`WA Test Order`) and replace the provider with a fake
one, so no API is called. Each test is rolled back.

```bash
# use a separate test site, not production
bench --site <test-site> set-config allow_tests true
bench --site <test-site> run-tests --app grid_whatsapp
```

Coverage (36 tests): variable resolution and formatting, linked and child-table
paths, Supplier primary-contact and fallback resolution, Employee mobile
(direct and via User), Role recipients with department filter, multiple
recipients and duplicate removal, missing/invalid numbers, phone normalisation,
condition evaluation (including rejection of unsafe expressions), rule matching
by event, value-change triggers, disabled rule, disabled instance, duplicate
notification prevention, Send on Every Trigger, permanent vs temporary API
failures, retry then fail, retry then succeed, secret redaction, resend,
delivery receipts, test mode, rule/template validation, zero DB queries for
DocTypes without rules, and the unconfigured eValidation provider.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| Nothing happens, no log | Rule enabled? Right Document Type and Send When? Condition true (use **Preview**)? Already sent once for this trigger (see [idempotency](#events-and-duplicate-prevention))? Turn on **Debug Logging** and read `logs/grid_whatsapp.log` |
| Logs stay *Queued* | Background workers (`queue-short`) and the scheduler must run with this app's code. Is the scheduler enabled for the site? |
| *eValidation API request format has not been configured* | Expected until `providers/evalidation.py` is completed. Use Test Mode meanwhile |
| *No mobile number found for ...* | Open the record: has a primary contact / phone field / linked contact? Set **Mobile Field** or **Contact Resolution** |
| *has no country code* | Set Default Country Code in WhatsApp Settings, or store numbers as +<code><number> |
| *Required variable(s) ... have no value* | The source field is empty on that document. Add a Default Value or untick Required |
| *WhatsApp Instance ... is inactive* | Activate it, or choose another instance on the rule |
| Rule cannot be saved: field does not exist | The path must use fieldnames (not labels) of the selected DocType; check with Customize Form |
| Changed code not picked up | Restart backend, queue and scheduler containers (or `bench restart`) and run `bench --site <site> clear-cache` |
