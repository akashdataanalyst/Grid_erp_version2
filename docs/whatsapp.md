# Grid WhatsApp: pura flow aur kya kya chahiye

> **5 Oct 2026:** `grid_whatsapp` ab alag app nahi hai. Poora code `grid_erp` app ke
> andar module **Grid WhatsApp** mein hai: `grid_erp/grid_erp/grid_whatsapp/`.
> Neeche jahan bhi `services/...`, `api/...`, `utils/...` likha hai, woh isi folder ke andar hai.
> Hooks `grid_erp/grid_erp/hooks.py` mein hain.

Yeh document batata hai ki WhatsApp notification module shuru se aakhir tak kaise kaam
karta hai, aur ise live chalane ke liye kya kya chahiye. Technical detail (saare
fields, condition syntax, tests) ke liye [README.md](README.md) dekhein.

---

## 1. App kya karta hai

ERPNext mein koi document save, submit, cancel ya approve hota hai, to yeh app
apne aap sahi logon ko WhatsApp message bhejta hai.

- **Kaunsa document, kab, kise, kya message:** yeh sab desk (UI) se set hota hai.
  Iske liye code change nahi karna padta.
- ERPNext, HRMS aur grid_erp ka code bilkul nahi chhua gaya. Yeh app alag se
  baitha hai aur sirf document events sunta hai.

---

## 2. Ek example se pura flow

**Setup:** ek rule hai: *"Purchase Order submit ho to Supplier ko WhatsApp jaye"*.

```text
 User ne Purchase Order PO-0001 SUBMIT kiya
          │
          ▼
 ① EVENT AAYA            hooks.py → notification_engine.handle_doc_event
          │               "Purchase Order ke liye koi enabled rule hai?"
          │               (Redis cache se check, DB query nahi)
          │               Nahi hai → yahin khatam. Hai → aage.
          ▼
 ② RULE MATCH            Rule ka event "After Submit" hai aur abhi submit hua hai → match
          │
          ▼
 ③ CONDITION CHECK       condition_engine.py
          │               e.g.  grand_total > 50000  →  True / False
          │               False → kuch nahi bhejna. True → aage.
          ▼
 ④ JOB QUEUE             frappe.enqueue (document save hone ke BAAD)
          │               User ka submit yahin poora ho jata hai.
          │               WhatsApp ka kaam ab background worker karega.
          ▼
 ─────────────── background worker (queue-short) ───────────────
          ▼
 ⑤ MESSAGE BANA          template_renderer.py
          │               Template:  "Dear {{supplier_name}}, PO {{po_number}} ..."
          │               Variables document se:  supplier_name ← supplier_name
          │                                       po_number     ← name
          │               Result:    "Dear ABC Traders, PO PO-0001 ..."
          ▼
 ⑥ NUMBER DHOONDHA       recipient_resolver.py
          │               PO → Supplier "ABC Traders"
          │                  → Primary Contact → Mobile No → 98765 43210
          │               (na mile to Supplier ka mobile_no, phir koi bhi linked Contact)
          ▼
 ⑦ NUMBER SAAF KIYA      utils/phone.py
          │               "98765 43210" + country code 91  →  "+919876543210"
          │               Do rows ka ek hi number nikla → sirf ek message
          ▼
 ⑧ LOG BANA              WhatsApp Notification Log  (status: Queued)
          │               Unique key = rule + document + trigger + number
          │               Yeh message pehle ja chuka hai → doobara nahi bhejta
          ▼
 ⑨ API CALL              dispatcher.py → providers/evalidation.py → eValidation API
          │
          ├── Success           → Log: Sent  (message id save hota hai)
          ├── Timeout / 5xx     → Log: Queued + next_retry_at  (scheduler har minute retry karta hai)
          └── Galat number/key  → Log: Failed  (retry nahi, error log mein likha jata hai)
          ▼
 ⑩ DELIVERY RECEIPT      (optional) eValidation webhook bheje
                          → api/webhook.py → Log: Delivered → Read
```

**Test Mode on ho to** step ⑨ mein API call nahi hoti. Log seedha "Sent" ho jata
hai, saath mein note hota hai `test_mode: true`. Isse bina message bheje poora
setup check ho jata hai.

---

## 3. Kaunsa kaam kaunsi file karti hai

| Step | File | Kaam |
| --- | --- | --- |
| ① ② ④ ⑧ | `services/notification_engine.py` | Event sunna, rule dhoondhna, job queue karna, duplicate rokna, log banana |
| ③ | `services/condition_engine.py` | Condition safe tareeke se check karna (`eval` nahi, koi code nahi chalta) |
| ⑤ | `services/template_renderer.py` | `{{variable}}` ki jagah value lagana |
| ⑤ ⑥ | `services/field_resolver.py` | `supplier.supplier_name` jaise path padhna (Link aur child table ke through) |
| ⑥ | `services/recipient_resolver.py` | Supplier, Employee, Role, Workflow Approver wagairah se mobile number nikalna |
| ⑦ | `utils/phone.py` | Number ko `+<country code><number>` format mein laana |
| ⑨ | `services/dispatcher.py` | Bhejna, retry karna, status update karna |
| ⑨ | `services/whatsapp_provider.py` | Provider ka common interface, HTTP errors ko samajhna |
| ⑨ | `services/providers/evolution.py` | **Evolution API wala code** (chal raha hai, Apps Script jaisa hi call) |
| ⑨ | `services/providers/evalidation.py` | **Sirf eValidation wala code** (abhi TODO) |
| ⑩ | `api/webhook.py` | Delivery/Read receipt lena |
| UI | `api/notification.py` + DocType `.js` files | Preview, Send Now, Resend buttons |

---

## 4. Kya kya chahiye

### 4.1 eValidation se (sabse zaroori, abhi pending)

Inke bina asli message nahi jayega. Abhi har asli send *"eValidation API request
format has not been configured"* error ke saath Failed ho jata hai.

| Kya chahiye | Kahan lagega |
| --- | --- |
| API Base URL | WhatsApp Instance > API Base URL |
| API Key / Token, aur yeh kis header mein jata hai | Instance > API Key / API Token, aur `evalidation.py` |
| Instance ID / Sender Number (agar API maange) | Instance > Provider Instance ID / Sender Number |
| Message bhejne ka endpoint aur request ka format (JSON payload) | `evalidation.py` > `build_send_request()` |
| Number ka format: `+919876543210` ya `919876543210` | `build_send_request()` |
| Template kaise bhejna hai: naam, id, language, parameters (named ya `{{1}} {{2}}`) | `build_send_request()` |
| Success response mein message id kis field mein aata hai | `evalidation.py` > `parse_send_response()` |
| Error codes: galat number, template reject, aur baaki errors | `parse_send_response()` |
| Approved templates ki list: naam/id, language, parameters ka order | WhatsApp Template form |
| (Optional) Delivery/Read webhook ka format | `evalidation.py` > `parse_status_callback()` |

> eValidation ke API docs, ya ek sample request aur response, mil jaane par
> yeh teen methods bharne hain. Baaki app mein kuch nahi badalna.

### 4.2 Server par

| Kya | Kyun |
| --- | --- |
| App ka code **har** Python container mein: backend, queue-short, queue-long, scheduler | Worker message bhejta hai, scheduler retry karta hai |
| Background workers chal rahe hon (`queue-short`) | Inke bina log "Queued" mein hi atka rahega |
| Site par scheduler enabled ho | Retry ke liye (`bench --site <site> enable-scheduler`) |
| Redis (queue + cache) | Job queue aur rule cache |
| Server se eValidation API tak internet/HTTPS access | API call ke liye |
| Webhook chahiye to site public HTTPS URL par ho | eValidation callback bhej sake |

Is frappe_docker setup mein `pwd.yml` app ko saare containers mein mount karta
hai. Doosre server par yahi setup, ya `bench get-app` + `install-app` + `migrate`.

### 4.3 ERPNext data mein

Message tabhi jayega jab number mile:

| Recipient | Number yahan hona chahiye |
| --- | --- |
| Supplier / Customer | Primary Contact ka **Mobile No**, ya Supplier/Customer ka mobile, ya koi linked Contact |
| Employee | Employee > **Cell Number** (`cell_number`) |
| User / Role | User > **Mobile No**, ya us User se linked Employee ka cell number |
| Lead | WhatsApp No / Mobile No |
| Document field | Document ke us field mein number |

Number bina country code ke hain (jaise `9876543210`) to WhatsApp Settings mein
**Default Country Code = 91** zaroor set karein.

### 4.4 Desk mein configuration (is order mein)

1. **WhatsApp Settings:** Default Country Code `91`. Pehle **Test Mode on**
   rakhein. Retry ki defaults (3 retries, 5 minute) theek hain.
2. **WhatsApp Instance:** naam (jaise *Purchase WhatsApp*), provider eValidation,
   URL aur key. *Default Instance* tick karein.
3. **WhatsApp Template:** message likhein, **Detect Variables** dabayein, phir har
   variable ka Source Field bharein (jaise `supplier_name`, `name`, `grand_total`).
4. **WhatsApp Notification Rule:** Document Type, Send When, Condition, Template,
   aur Recipients (jaise Document Link → `supplier`).
5. Rule par **Preview** dabakar ek purana document chunein. Message, number aur
   koi problem ho to woh sab dikh jayega.
6. Ek document submit karke **View Logs** mein status dekhein.
7. eValidation wala code poora hone ke baad **Test Mode off** karein.

Permission: yeh sab **System Manager** kar sakta hai. Templates aur Rules ke liye
**WhatsApp Manager** role bhi diya ja sakta hai. Settings aur API keys sirf
System Manager ke paas rehti hain.

---

## 5. Kab message jata hai (Send When)

| Option | Kab | Default mein kitni baar |
| --- | --- | --- |
| After Insert | Naya document bana | Ek baar |
| After Save | Draft save hua | Ek baar (pehli matching save par) |
| After Submit | Submit hua | Ek baar |
| After Cancel | Cancel hua | Ek baar |
| On Update | Koi bhi badlaav | Ek baar |
| On Value Change | Chuna hua field badla (jaise `status`) | Har **nayi value** par ek baar |
| Workflow State Change | Workflow state badla (jaise *Approved*) | Har state par ek baar |
| Manual | Sirf **Send Now** button se | Har baar |

Har save par message chahiye to rule mein **Send on Every Trigger** tick karein.

---

## 6. Log ka status aur kya karein

| Status | Matlab | Kya karein |
| --- | --- | --- |
| Queued | Line mein hai, ya retry ka intezaar | Kuch nahi. Bahut der tak atka rahe to worker/scheduler check karein |
| Processing | Abhi bheja ja raha hai | Kuch nahi |
| Sent | Provider ne le liya | ✔ |
| Delivered / Read | Phone par pahuncha / padha gaya (webhook se) | ✔ |
| Failed | Nahi gaya, wajah **Error** field mein hai | Wajah theek karke **Retry Now** dabayein |
| Cancelled | Roka gaya | Retry Now se phir bhej sakte hain |

Aam errors:

| Error | Hal |
| --- | --- |
| eValidation API request format has not been configured | Section 4.1 poora hona baaki hai. Tab tak Test Mode use karein |
| No mobile number found for ... | Supplier/Employee par number ya primary contact daalein |
| has no country code | Settings mein Default Country Code daalein |
| Required variable(s) ... have no value | Document mein woh field khaali hai: Default Value dein ya Required hatayein |
| WhatsApp Instance ... is inactive | Instance active karein |
| Condition: ... | Rule ki condition mein field ka naam galat hai |

---

## 7. Abhi ki sthiti (5 Oct 2026)

| Kaam | Status |
| --- | --- |
| App, 7 DocTypes, engine, UI buttons | ✔ Ban gaya |
| 36 automated tests | ✔ Pass |
| Background worker ke saath end-to-end (Test Mode) | ✔ Chala |
| Local site `ampugerp.in` par install | ✔ Ho gaya |
| Evolution API provider + asli test message (XXXXXXXXXX) | ✔ Sent |
| eValidation API ka asli request/response | ✘ API docs ka intezaar |
| Server (3.110.156.185) par deploy | ✘ Baaki |
| GitHub par push | ✘ Baaki (abhi sirf local git) |
| `grid_whatsapp` app ko `grid_erp` mein merge | ✔ Ho gaya (5 Oct 2026), 36 tests pass |

---

## 8. Evolution API setup (jo abhi bana hai) aur har DocType kya karta hai

Google Sheet wala Apps Script jo call karta tha, wahi call ab ERPNext se jaati hai:

```text
POST http://<evolution-server>:8081/message/sendText/<instance-name>
header  apikey: <API Key>
body    {"number": "+91XXXXXXXXXX", "text": "Hello ..."}
```

Neeche wale 7 DocTypes ek chain banate hain. Upar se neeche padhein:

```text
 WhatsApp Settings ──default──▶ WhatsApp Instance  ("KAHAN se bhejna": server + key)
                                       ▲
 WhatsApp Notification Rule ───────────┘   ("KAB bhejna": kaunsa document, kaunsa event)
   ├── template ──▶ WhatsApp Template      ("KYA bhejna": message ka text)
   │                  └── WhatsApp Template Variable (child)  {{naam}} ki value kahan se aaye
   └── WhatsApp Notification Recipient (child)                 ("KISE bhejna")
                         │
                         ▼
               WhatsApp Notification Log   (har bheje gaye message ka record)
```

### 8.1 WhatsApp Instance: "Calco Evolution"

Evolution server se judne ki details. Apps Script ke `API_URL` aur `API_KEY` yahan hain.

| Field | Value | Matlab |
| --- | --- | --- |
| Provider | Evolution API | Kaunsa code bhejega (`providers/evolution.py`) |
| API Base URL | `http://<evolution-server>:8081` | Server ka address (bina `/message/...` ke) |
| Instance ID | `<instance-name>` | Evolution mein instance ka naam (URL ke end wala hissa) |
| API Key | (password field) | `apikey` header mein jaati hai |
| Default Instance | ✔ | Rule mein instance na chuna ho to yahi use hoga |
| Verify SSL | ✘ | Server `http` par hai, SSL nahi |
| Webhook Secret | random | Sirf delivery receipt ke liye (8.7 dekhein) |

Doosra WhatsApp number jodna ho to Evolution mein naya instance banao aur yahan ek
aur WhatsApp Instance banao. Usme sirf Instance ID alag hoga.

### 8.2 WhatsApp Settings (ek hi record, poore system ke liye)

| Field | Value | Matlab |
| --- | --- | --- |
| Default Instance | Calco Evolution | |
| Default Country Code | 91 | `XXXXXXXXXX` khud `+91XXXXXXXXXX` ban jata hai |
| Test Mode | ✘ | ✔ karo to message **nahi** jata, sirf log "Sent" ban jata hai (testing ke liye) |
| Send in Background | ✔ | Document save turant ho jata hai, message worker bhejta hai |
| Retry / Max Retries / Delay | ✔ / 3 / 5 min | Server down ya timeout ho to dobara try. Galat number par retry nahi hota |
| Failure Behaviour | Log Only | Fail ho to sirf log. "Block Document" karo to document save hi nahi hoga |

### 8.3 WhatsApp Template: "Calco Test Message"

Message ka text. `{{naam}}` placeholder hai, jiski value **Variables** table batati hai.

```text
Hello {{recipient_name}}, this is a test message from Calco Bot.

Company: {{company}}
Date: {{today}}
```

### 8.4 WhatsApp Template Variable (template ke andar ki table)

Har `{{placeholder}}` ki ek row. Value 3 jagah se aa sakti hai:

| Variable | Source Type | Source Field / Default | Kahan se value aayi |
| --- | --- | --- | --- |
| recipient_name | Static Value | `Akash` | Fixed text, hamesha same |
| company | Document Field | `company_name` | Jis document par rule chala, uska field. `customer.customer_name` jaise Link ke through bhi chalta hai |
| today | System Value | `today` | System se: `today`, `now`, `document_name`, `document_url`, `site_url`, `current_user` |

Required ✔ ho aur value khaali mile to message nahi jata, log "Failed" ho jata hai.
Template mein koi `{{x}}` ho jo table mein nahi hai, to template save hi nahi hoga.

### 8.5 WhatsApp Notification Rule: "Calco Test - Manual"

Yeh batata hai ki **kab** aur **kis document par** message jaye.

| Field | Value | Matlab |
| --- | --- | --- |
| Document Type | Company | Is DocType ke documents par rule lagta hai |
| Event | Manual | Apne aap kabhi nahi chalta. Sirf **Send Now** button se |
| Template | Calco Test Message | |
| Condition | Always | "Expression" karke `grand_total > 50000` jaisi shart laga sakte ho |
| Allow Duplicate | ✘ | Ek document + event + number par message sirf ek baar |

Asli kaam ke liye Event badlo: `After Submit` (Sales Order submit hote hi), `After Insert`
(naya Lead banne par), `On Value Change` (koi field badle, jaise `status`),
`Workflow State Change` (approve hone par) wagairah.

### 8.6 WhatsApp Notification Recipient (rule ke andar ki table)

Message **kise** jaye. Ek rule mein kitni bhi rows. Ek number do baar aaye to bhi
message ek hi jata hai.

| Recipient Type | Kya karta hai | Example |
| --- | --- | --- |
| Fixed Number | Hamesha yahi number | `XXXXXXXXXX` (abhi yahi laga hai) |
| Document Field | Document ka mobile wala field | Lead ka `mobile_no` |
| Document Link | Document ka Link field, uske record ka mobile | Sales Order ka `customer`, phir Customer ka contact |
| Employee / User / Customer / Supplier / Lead / Contact | Ek chuna hua record | Ek specific Employee |
| Role | Us role ke saare users | "Sales Manager" wale sab |
| Workflow Approver | Jo agle step par approve karega | |

### 8.7 WhatsApp Notification Log

Har message ka ek record. Isi se pata chalta hai ki kya gaya, kise gaya, aur kyun fail hua.

- **Status:** Queued → Processing → Sent → (Delivered → Read, agar webhook laga ho)
  ya Failed / Cancelled
- **Message:** jo text gaya, **Recipient:** jis number par gaya
- **Message ID:** Evolution ka id (`key.id`)
- **Response:** Evolution ka poora jawab (API key kabhi log mein nahi aati)
- **Error:** fail hone ki wajah
- Failed log par **Resend** button hai

Delivered/Read status chahiye to Evolution mein instance ka webhook set karo:
URL `https://<site>/api/method/grid_erp.grid_whatsapp.api.webhook.status_callback?instance=Calco Evolution`,
header `X-Webhook-Secret: <Instance ka Webhook Secret>`, event `MESSAGES_UPDATE`.
(Server public URL par hona chahiye, localhost par Evolution pahunch nahi payega.)

### 8.8 Test kaise kiya aur dobara kaise karein

5 Oct 2026 ko ek message bheja gaya: `+91XXXXXXXXXX`, status **Sent**,
Message ID `3EB03F5D6BF1BB71F320B6`.

Dobara test karna ho to: **WhatsApp Notification Rule > Calco Test - Manual** kholo,
**Preview** dabao (kuch nahi bhejta, sirf dikhata hai), phir **Send Now** dabao aur
Company `Ampug Solutions` chuno. Har click par ek message jaata hai.

---

## 9. Leave Application ke rules (5 Oct 2026, abhi DISABLED)

Instance alag nahi banaya: dono rules wahi **Calco Evolution** instance use karte hain
(ek hi WhatsApp number se HR ke message bhi jayenge).

```text
 Employee leave daalta hai (Leave Application save)
        │  Rule: "Leave Applied - Notify Approver"   Event: After Insert
        ▼
 Approver ko WhatsApp  ── Template: "Leave Application - Approver"
        │                  Number: leave_approver (User) ka Mobile No,
        │                  na ho to us User se linked Employee ka cell_number
        ▼
 Approver status Approved/Rejected karke SUBMIT karta hai
        │  Rule: "Leave Approved/Rejected - Notify Employee"   Event: After Submit
        │  Condition: status in ("Approved", "Rejected")
        ▼
 Employee ko WhatsApp  ── Template: "Leave Application - Status to Employee"
                          Number: Employee ka cell_number (Mobile)
```

Approver wala message aisa dikhega:

```text
Dear Akash Sharma,

*Rahul Kumar* (HR-EMP-00001) has applied for leave.

Leave Type: Casual Leave
From: 10-10-2026
To: 11-10-2026
Total Days: 2
Leave Balance: 8
Reason: Family function

Application: HR-LAP-2026-00001
Please approve or reject: http://ampugerp.in/desk/leave-application/HR-LAP-2026-00001

- Calco HR
```

### Chalu karne se pehle

1. Har Leave Approver ke **User > Mobile No** mein number daalo (ya uska Employee
   record User se linked ho aur Employee mein **Mobile** bhara ho).
2. Har Employee ke **Mobile (cell_number)** mein number daalo.
3. Rule kholo, **Preview** dabao aur ek asli Leave Application chuno. Message aur number
   dikhega, bhejega kuch nahi.
4. Theek lage to rule mein **Enabled** ✔ karke save karo. Uske baad har nayi leave par
   message apne aap jayega.

Number na mile to message nahi jata, sirf Log mein "Failed: no mobile number" aata hai.
