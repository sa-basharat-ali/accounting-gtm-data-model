"""The GTM data model: identity resolution, canonical entities, declared metric rules,
reconciliation checks, and a permission-aware context layer for agents."""
import re
from collections import defaultdict
from datetime import timedelta

from world import TODAY

# ----------------------------------------------------------------------------------------------
# Ontology: what the data means, written down where people and agents can both read it
# ----------------------------------------------------------------------------------------------
ONTOLOGY = {
    "entities": {
        "firm": {"grain": "one row per real-world organization (accounting firm or corporate finance team)",
                 "key": "firm_id", "built_from": "salesforce.accounts resolved by name + domain"},
        "buying_group": {"grain": "one row per firm x practice (CAS, Tax, Audit, Corporate Close)",
                         "key": "(firm_id, practice)", "why": "firms buy practice by practice; the lead partner of each practice is the buyer"},
        "person": {"grain": "one row per human, keyed on email", "key": "email"},
        "opportunity": {"grain": "one row per firm x product x sales cycle", "key": "opportunity_id"},
        "activity": {"grain": "one row per real touch, deduplicated on message_id or call_id",
                     "key": "(source, message_id | call_id)", "kinds": "attempted (sent) vs engaged (reply, call held)"},
    },
    "relationships": [
        "firm 1:many buying_group", "buying_group 1:many opportunity", "firm 1:many person",
        "person 1:many activity", "opportunity many:1 motion (enterprise_direct | midmarket_outbound | channel_partner | inbound_waitlist)",
    ],
    "field_owners": {  # which system controls each field; everything else may only read it
        "opportunity.stage": "salesforce", "opportunity.amount": "salesforce",
        "opportunity.motion": "warehouse (declared rule)", "opportunity.is_expansion": "warehouse (declared rule)",
        "firm.firm_id": "warehouse (identity resolution)", "firm.last_engaged_at": "warehouse (max of gong, outreach replies)",
        "partner.registration": "partner_portal", "activity.*": "outreach | gong (append-only)",
        "person.title": "salesforce",
    },
    "metric_rules": {
        "new_logo_arr": "Closed Won ARR on opportunities whose firm had no earlier Closed Won. Uses resolved firms, never the SF Type field.",
        "expansion_arr": "Closed Won ARR on opportunities whose firm already had a Closed Won before this one was created.",
        "sourced_pipeline": "Each opportunity has exactly one motion. A partner registration dated before the opportunity was created wins; otherwise the SF lead source.",
        "engaged_account": "A firm with an email reply or a held call in the last 90 days. Sends and opens are attempts, not engagement.",
        "dark_customer": "A customer firm with no engagement in 60 days, measured across Gong and Outreach, not SF last_activity_date.",
    },
    "agent_permissions": {
        "sdr_agent": {"read": ["firm", "person", "activity", "buying_group"], "write": ["task.create", "activity.note"]},
        "revops_agent": {"read": ["*"], "write": ["task.create", "opportunity.next_step"],
                         "escalate": ["firm.merge", "opportunity.motion", "opportunity.amount"]},
    },
}

LEGAL = {"llp", "pllc", "ps", "cpas", "cpa", "co", "llc", "pc", "and", "inc", "tax"}


def name_key(name):
    s = re.sub(r"\(.*?\)", " ", name.lower()).replace("&", " and ")
    return " ".join(t for t in re.sub(r"[^a-z ]", " ", s).split() if t not in LEGAL)


def domain_root(d):
    return re.sub(r"cpa(?=\.com$)", "", d.lower())


# ----------------------------------------------------------------------------------------------
# 1. Identity resolution: SF accounts -> firms
# ----------------------------------------------------------------------------------------------
def resolve(accounts, strategy):
    """Union-find over accounts. strategy: 'name', 'domain' or 'both'."""
    parent = {a["account_id"]: a["account_id"] for a in accounts}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    for keyfn, on in ((lambda a: name_key(a["name"]), strategy in ("name", "both")),
                      (lambda a: domain_root(a["website"]), strategy in ("domain", "both"))):
        if not on:
            continue
        seen = {}
        for a in accounts:
            k = keyfn(a)
            if k in seen:
                union(seen[k], a["account_id"])
            else:
                seen[k] = a["account_id"]
    return {a["account_id"]: find(a["account_id"]) for a in accounts}


def score_resolution(accounts, mapping, truth_dups):
    true_firm = {a["account_id"]: a["_firm"] for a in accounts}
    merged = [a for a in mapping if mapping[a] != a]
    caught = sum(1 for d in truth_dups if mapping[d] != d and true_firm[mapping[d]] == true_firm[d])
    false_merges = sum(1 for a in merged if true_firm[mapping[a]] != true_firm[a])
    return caught, false_merges


# ----------------------------------------------------------------------------------------------
# 2. Activity: dedup and the attempted / engaged split
# ----------------------------------------------------------------------------------------------
def dedup_events(events, key):
    seen, out = set(), []
    for e in events:
        k = tuple(e[x] for x in key)
        if k not in seen:
            seen.add(k)
            out.append(e)
    return out


# ----------------------------------------------------------------------------------------------
# 3. Declared metric rules vs what the CRM fields say
# ----------------------------------------------------------------------------------------------
def classify_expansion(opps, firm_of):
    wins = defaultdict(list)
    for o in opps:
        if o["stage"] == "Closed Won":
            wins[firm_of[o["account_id"]]].append(o["close_date"])
    out = {}
    for o in opps:
        prior = [d for d in wins[firm_of[o["account_id"]]] if d < o["created_date"]]
        out[o["opportunity_id"]] = bool(prior)
    return out


def assign_motion(opps, regs, accounts):
    by_domain = defaultdict(list)
    for r in regs:
        by_domain[domain_root(r["domain"])].append(r)
    site = {a["account_id"]: domain_root(a["website"]) for a in accounts}
    src = {"Partner": "channel_partner", "Waitlist": "inbound_waitlist", "Outbound": "midmarket_outbound",
           "AE Sourced": "enterprise_direct"}
    out, conflicts = {}, []
    for o in opps:
        reg = [r for r in by_domain[site[o["account_id"]]] if r["registered_at"] <= o["created_date"]
               and o["created_date"] - r["registered_at"] <= timedelta(days=60)]
        if reg:
            out[o["opportunity_id"]] = "channel_partner"
            if o["lead_source"] != "Partner":
                conflicts.append(o["opportunity_id"])
        else:
            out[o["opportunity_id"]] = src[o["lead_source"]]
    return out, conflicts


# ----------------------------------------------------------------------------------------------
# 4. Context for agents: traceable reads, controlled writes
# ----------------------------------------------------------------------------------------------
def agent_write(role, action, payload):
    perms = ONTOLOGY["agent_permissions"][role]
    if action in perms.get("write", []):
        return {"status": "applied", "action": action, "payload": payload}
    if action in perms.get("escalate", []):
        return {"status": "escalated", "action": action, "payload": payload,
                "reason": "needs human approval: changes a governed field or merges entities"}
    owner = ONTOLOGY["field_owners"].get(action.replace(".update", ""), "unknown")
    return {"status": "blocked", "action": action, "reason": f"field is owned by {owner}; agents may read it, not write it"}
