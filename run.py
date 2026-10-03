"""python3 run.py  -- stdlib only, about a second."""
from collections import defaultdict
from datetime import timedelta

import model
from world import TODAY, build

systems, truth, firms = build()
sf = systems["salesforce"]
accounts, opps, contacts = sf["accounts"], sf["opportunities"], sf["contacts"]
firm_dom = {f["firm_id"]: f["domain"] for f in firms}


def money(x):
    return f"${x / 1e6:,.2f}M"


def pct(a, b):
    return f"{(a - b) / b * 100:+.0f}%" if b else "n/a"


print("=" * 78)
print("GTM data model for an AI-for-accounting company: synthetic world, real failure modes")
print("=" * 78)
print(f"firms {len(firms)} | SF accounts {len(accounts)} | contacts {len(contacts)} | opportunities {len(opps)}")
print(f"outreach webhook events {len(systems['outreach_events'])} | gong calls {len(systems['gong_calls'])} | "
      f"partner registrations {len(systems['partner_portal'])} | waitlist forms {len(systems['waitlist'])}")

# 1. identity resolution -----------------------------------------------------------------------
print("\n1. WHO IS THE CUSTOMER  (SF accounts -> real firms)")
print(f"   injected duplicate accounts: {len(truth['duplicate_account'])}")
print("   strategy        dups caught   false merges")
best = None
for strat in ("name", "domain", "both"):
    mp = model.resolve(accounts, strat)
    c, fm = model.score_resolution(accounts, mp, truth["duplicate_account"])
    print(f"   {strat:<14}  {c:>4}/{len(truth['duplicate_account']):<6}     {fm:>4}")
    if strat == "both":
        best = mp
# tiered: auto-merge on domain, send name-only matches to a human review queue
dom_map, both_map = model.resolve(accounts, "domain"), best
true_firm = {a["account_id"]: a["_firm"] for a in accounts}
queue = [a for a in both_map if both_map[a] != a and dom_map[a] == a]
q_true = sum(1 for a in queue if true_firm[both_map[a]] == true_firm[a])
print(f"   tiered: auto-merge {sum(1 for a in dom_map if dom_map[a] != a)} on domain (0 wrong); "
      f"{len(queue)} name-only matches go to review: {q_true} real, {len(queue) - q_true} different companies")
firm_of = best
n_firms_resolved = len(set(firm_of.values()))

# 2. new logo vs expansion -----------------------------------------------------------------------
print("\n2. NEW LOGO OR EXPANSION  (SF Type field vs declared rule on resolved firms)")
won = [o for o in opps if o["stage"] == "Closed Won"]
naive_per_account = model.classify_expansion(opps, {a["account_id"]: a["account_id"] for a in accounts})
declared = model.classify_expansion(opps, firm_of)
nl_sf = sum(o["amount"] for o in won if o["type"] == "New Business")
nl_acct = sum(o["amount"] for o in won if not naive_per_account[o["opportunity_id"]])
nl_dec = sum(o["amount"] for o in won if not declared[o["opportunity_id"]])
nl_true = sum(o["amount"] for o in won if o["_truth_type"] == "new_logo")
print(f"   new-logo ARR, SF Type field:              {money(nl_sf)}  ({pct(nl_sf, nl_true)} vs truth)")
print(f"   new-logo ARR, history per SF account:     {money(nl_acct)}  ({pct(nl_acct, nl_true)})")
print(f"   new-logo ARR, history per resolved firm:  {money(nl_dec)}  ({pct(nl_dec, nl_true)})")
mis = truth["expansion_as_new_logo"]
caught = sum(1 for o in mis if declared[o])
print(f"   expansions mislabeled 'New Business': {len(mis)}; declared rule recovers {caught}")

# practice penetration
prac_naive, prac_dec = defaultdict(set), defaultdict(set)
for o in won:
    prac_naive[o["account_id"]].add(o["product"])
    prac_dec[firm_of[o["account_id"]]].add(o["product"])
multi_naive = sum(1 for v in prac_naive.values() if len(v) > 1)
multi_dec = sum(1 for v in prac_dec.values() if len(v) > 1)
print(f"   customers live in 2+ practices: {multi_naive} by SF account, {multi_dec} by firm")
print(f"   customer count: {len(prac_naive)} SF accounts vs {len(prac_dec)} firms")

# 3. motion attribution ------------------------------------------------------------------------
print("\n3. WHO SOURCED IT  (one motion per opportunity)")
motion, conflicts = model.assign_motion(opps, systems["partner_portal"], accounts)
open_or_won = [o for o in opps if o["stage"] != "Closed Lost"]
sf_src = defaultdict(int)
for o in open_or_won:
    sf_src[o["lead_source"]] += o["amount"]
reg_opps = {r["_opp"] for r in systems["partner_portal"]}
partner_report = sum(o["amount"] for o in open_or_won if o["opportunity_id"] in reg_opps)
naive_total = sum(sf_src.values()) + sum(o["amount"] for o in open_or_won
                                         if o["opportunity_id"] in reg_opps and o["lead_source"] != "Partner")
true_total = sum(o["amount"] for o in open_or_won)
print(f"   pipeline + bookings if SF sources and the partner report are both summed: {money(naive_total)}")
print(f"   actual: {money(true_total)}  (double-counted {money(naive_total - true_total)})")
dc = truth["partner_double_claim"]
print(f"   partner deals also claimed by an AE: {len(dc)}; rule flags {sum(1 for x in conflicts if x in dc)}, "
      f"false flags {sum(1 for x in conflicts if x not in dc)}")
by_m = defaultdict(int)
for o in open_or_won:
    by_m[motion[o["opportunity_id"]]] += o["amount"]
for m in ("enterprise_direct", "midmarket_outbound", "channel_partner", "inbound_waitlist"):
    print(f"     {m:<20} {money(by_m[m])}")

# 4. activity ----------------------------------------------------------------------------------
print("\n4. WHAT ACTUALLY HAPPENED  (webhooks retry, and sends are not engagement)")
ev = systems["outreach_events"]
d1 = model.dedup_events(ev, ("event_id",))
d2 = model.dedup_events(ev, ("message_id", "type"))
print(f"   raw events {len(ev)} | dedup on event_id {len(d1)} | dedup on message_id {len(d2)}")
print(f"   retries with the same event_id: {len(truth['webhook_retry'])} (caught by both)")
print(f"   retries that regenerated event_id: {len(truth['regenerated_event_id'])} "
      f"(event_id dedup catches 0, message_id dedup catches {len(d1) - len(d2)})")
since = TODAY - timedelta(days=90)
dom2firm = {f["domain"]: f["firm_id"] for f in firms}
attempted = {dom2firm[e["contact_email"].split("@")[1]] for e in d2 if e["ts"] >= since}
engaged = {dom2firm[e["contact_email"].split("@")[1]] for e in d2 if e["ts"] >= since and e["type"] == "email_replied"}
engaged |= {dom2firm[g["account_domain"]] for g in systems["gong_calls"] if g["held_at"] >= since}
print(f"   'engaged' firms, last 90 days: {len(attempted | engaged)} counting any touch, {len(engaged)} by the declared rule")

# 5. sync drift --------------------------------------------------------------------------------
print("\n5. IS THE CUSTOMER GOING DARK  (SF last_activity_date vs the tools it syncs from)")
last = defaultdict(lambda: None)
for e in d2:
    if e["type"] == "email_replied":
        f = dom2firm[e["contact_email"].split("@")[1]]
        last[f] = max(filter(None, [last[f], e["ts"]]))
for g in systems["gong_calls"]:
    f = dom2firm[g["account_domain"]]
    last[f] = max(filter(None, [last[f], g["held_at"]]))
cust_firms = {a["_firm"] for a in accounts if a["account_id"] in prac_naive}
dark_cut = TODAY - timedelta(days=60)
dark_sf, drift = set(), []
for a in accounts:
    if a["_firm"] in cust_firms and (a["last_activity_date"] is None or a["last_activity_date"] < dark_cut):
        dark_sf.add(a["_firm"])
    if a["last_activity_date"] and last[a["_firm"]] and (last[a["_firm"]] - a["last_activity_date"]).days > 30:
        drift.append(a["account_id"])
dark_true = {f for f in cust_firms if last[f] is None or last[f] < dark_cut}
gap = truth["activity_sync_gap"]
print(f"   customers flagged dark by SF field: {len(dark_sf)} | by declared rule: {len(dark_true)}")
print(f"   accounts where the sync silently stopped: {len(gap)}; drift check flags "
      f"{sum(1 for x in drift if x in gap)}, false flags {sum(1 for x in drift if x not in gap)}")

# 6. agents ------------------------------------------------------------------------------------
print("\n6. CONTEXT FOR AGENTS  (traceable reads, controlled writes)")
dup = truth["duplicate_account"][0]
keep = firm_of[dup]
for role, action, payload in [
        ("sdr_agent", "task.create", {"firm": keep, "note": "follow up with Tax Lead Partner"}),
        ("revops_agent", "firm.merge", {"keep": keep, "merge": dup, "evidence": "same domain root, same name key"}),
        ("sdr_agent", "firm.last_engaged_at.update", {"firm": keep, "value": str(TODAY)})]:
    r = model.agent_write(role, action, payload)
    print(f"   {role:<13} {action:<28} -> {r['status']}" + (f"  ({r.get('reason')})" if r.get("reason") else ""))

print("\nOntology, field owners and metric rules are in model.ONTOLOGY (also printed by: python3 -c "
      "\"import model,json;print(json.dumps(model.ONTOLOGY,indent=1))\")")
