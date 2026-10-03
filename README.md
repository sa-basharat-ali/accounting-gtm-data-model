# A GTM data model for selling AI agents to accounting firms

```
python3 run.py      # stdlib only, no install, under a second
```

230 firms, 252 Salesforce accounts, 799 contacts, 139 opportunities, 2,647 Outreach webhook
events, 104 Gong calls, 30 partner registrations, 160 waitlist forms. Generated with a fixed
seed. No real firm, person or deal is represented. Failures are injected with ground truth so
every check is scored.

---

## The business this models

An AI-agent company selling to accounting firms has a GTM shape that breaks most CRM setups:

| | |
|---|---|
| **Buyers** | Firms buy **practice by practice**. The CAS lead partner, the tax partner and the audit partner are separate buying groups inside one firm. Corporate finance teams (CFO, controller) are a second buyer type. |
| **Motions** | Four, at once: enterprise direct to the largest firms, a new outbound motion into mid-market firms, **channel partners** who resell to their own clients, and an inbound **waitlist** that asks firm size, use case and ERP. |
| **Growth** | Land one practice, then expand to the next. Expansion is most of the revenue story, so mislabeling it as new business distorts every board metric. |
| **Systems** | Salesforce, Outreach, Gong, a partner portal and a web form, each believing it is the source of truth. Agents now read and write this data too. |

## Six findings

### 1. "Who is the customer" has no single right answer until you resolve identity

22 duplicate Salesforce accounts were injected: the same firm entered again with a name variant
("&" vs "and", "LLP" dropped, "(Tax)" appended), sometimes under a legacy domain from an acquired firm.

| strategy | duplicates caught | false merges |
|---|---|---|
| name only | 22/22 | 3 |
| domain only | 15/22 | 0 |
| name + domain | 22/22 | 3 |

There is no automatic setting that catches everything and merges nothing. The three false merges
are different companies that share a name. The working design is tiered: **auto-merge on domain
(15, none wrong), send name-only matches to a person (10 in the queue: 7 real, 3 not).** That queue
is exactly the kind of change an agent should propose and a human should approve.

### 2. The CRM's Type field overstates new-logo ARR by 44%

| new-logo ARR computed from | value | vs truth |
|---|---|---|
| Salesforce `Type = New Business` | $4.42M | **+44%** |
| purchase history per Salesforce account | $4.00M | +30% |
| purchase history per resolved firm | $3.06M | 0% |

Expansions land on duplicate accounts or get typed as new business by the rep. Fixing the field
alone gets you to +30%. Only resolving the firm first gets it right. The same fix changes the
customer count (24 accounts vs 20 firms) and the number of customers live in two or more practices
(6 vs 9), which is the expansion story leadership actually wants to see.

### 3. Summing the partner report and the CRM double-counts $0.75M

13 partner-registered deals were also marked AE-sourced. Add the partner portal's view to
Salesforce's and pipeline plus bookings reads $5.82M against a real $5.07M. The declared rule, a
registration dated before the opportunity was created wins, gives every opportunity exactly one
motion and flags all 13 conflicts with no false flags. That rule is a business decision about
partner credit, written down once, instead of an argument every quarter.

### 4. Webhook retries inflate activity, and a regenerated event ID hides from the usual dedup

Of 2,647 Outreach events, 150 were retries with the same event ID and 67 were retries that
regenerated it. Deduplicating on `event_id` catches the first kind and **none** of the second.
Deduplicating on `message_id` catches both. Then the definition question: 214 firms were "engaged"
in the last 90 days if any touch counts, **95** if engagement means a reply or a held call. Sends
are attempts.

### 5. A sync that stops silently makes healthy customers look dark

For 10 accounts the Salesforce `last_activity_date` sync stopped weeks ago while calls kept
happening in Gong. Measured off that field, 5 customers look dark; measured across the tools, 2 are.
A drift check comparing the field to its sources flags all 10 stalled syncs with no false flags.

### 6. Agents get context, not the keys

`model.ONTOLOGY` holds the grain of every entity, the relationships, **which system owns each
field**, the metric rules above in plain language, and per-agent permissions:

```
sdr_agent     task.create                  -> applied
revops_agent  firm.merge                   -> escalated  (needs human approval)
sdr_agent     firm.last_engaged_at.update  -> blocked    (owned by the warehouse; read-only for agents)
```

An agent can read a firm, its buying groups and its real last engagement with lineage back to
source records. It can create tasks. It cannot overwrite a derived field, and anything that merges
entities or moves money between motions goes to a person with the evidence attached.

## The part I got wrong

The first version caught every injected failure with zero false merges. That wasn't a result. It
was the generator agreeing with the detector, because every duplicate shared a domain. Adding
duplicates under a legacy domain dropped domain matching to 15 of 22 and exposed the name-matching
false merges, which is what produced the tiered design. Same lesson as before: a perfect score
usually means the test is too easy.

## Files

| | |
|---|---|
| `world.py` | the synthetic GTM world: firms, buying groups, four motions, five systems, injected failures |
| `model.py` | ontology, identity resolution, declared metric rules, dedup, drift checks, agent permissions |
| `run.py` | the report |

## Where this comes from

Through my own consulting practice I was the CEO's data person at a VC-backed computer vision
startup, where I found an 80% upstream data loss that every dashboard reported as healthy, then
built reconciliation against source systems that caught two more real sync failures on its first
run. Before that I spent two years on payments and settlement data at Geidea, Saudi Arabia's
largest payments processor, on its executive strategy team.

Syed Ahmed Basharat Ali · sabasharat.ali@gmail.com · basharat.net
