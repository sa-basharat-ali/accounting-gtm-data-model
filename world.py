"""A synthetic GTM world shaped like an AI-for-accounting company selling to accounting firms.

Everything here is generated. No real firm, person or deal is represented. The shape is taken
from public information: firms buy practice by practice (CAS, Tax, Audit), corporate finance
teams are a second buyer, deals arrive through four motions (enterprise direct, mid-market
outbound, channel partners, inbound waitlist), and activity lives in three tools that each
think they are the source of truth.

Failures are injected with ground truth so every check in model.py can be scored.
"""
import random
from datetime import date, timedelta

SEED = 1065  # the partnership return the industry keeps talking about
TODAY = date(2026, 10, 1)

PRACTICES = ["CAS", "Tax", "Audit"]
MOTIONS = ["enterprise_direct", "midmarket_outbound", "channel_partner", "inbound_waitlist"]
PARTNERS = ["Northgate Advisory", "Keystone Consulting Group", "Halyard Partners", "Brightline CAS Collective"]
ARR = {"CAS": 90_000, "Tax": 120_000, "Audit": 110_000, "Corporate Close": 60_000}

SURNAMES = ["Harlow", "Benton", "Pryce", "Okafor", "Lindqvist", "Marchetti", "Ashby", "Kaur", "Delacroix",
            "Whitfield", "Moreno", "Tanaka", "Osei", "Brennan", "Halvorsen", "Iyer", "Castellan", "Rourke",
            "Abernathy", "Sandoval", "Ferris", "Nakamura", "Quill", "Vance", "Ellery", "Duarte", "Kovacs",
            "Mbeki", "Sorensen", "Prewitt", "Galloway", "Achebe", "Lund", "Varga", "Strand", "Oyelaran"]
SUFFIXES = ["LLP", "PLLC", "PS", "CPAs", "& Co.", "LLC", "PC"]
CORP_WORDS = ["Freight", "Biosciences", "Hospitality", "Robotics", "Foods", "Energy", "Logistics", "Retail"]
FIRST = ["Ana", "Ben", "Chloe", "Dev", "Elena", "Femi", "Grace", "Hiro", "Iris", "Jon", "Kofi", "Lena",
         "Marco", "Nadia", "Omar", "Priya", "Quinn", "Rosa", "Sam", "Tara", "Uma", "Victor", "Wen", "Yusuf"]


def _d(rng, start, end):
    return start + timedelta(days=rng.randint(0, (end - start).days))


def build():
    rng = random.Random(SEED)
    truth = {k: [] for k in ["duplicate_account", "expansion_as_new_logo", "webhook_retry",
                             "regenerated_event_id", "activity_sync_gap", "partner_double_claim"]}

    # ---- firms (the real-world entities) ---------------------------------------------------
    firms, used = [], set()
    tiers = [("top_25", 25), ("top_100", 75), ("regional", 90), ("corporate", 40)]
    fid = 0
    for tier, n in tiers:
        for _ in range(n):
            fid += 1
            if tier == "corporate":
                name = f"{rng.choice(SURNAMES)} {rng.choice(CORP_WORDS)} Inc."
            else:
                while True:
                    a, b = rng.sample(SURNAMES, 2)
                    base = a if rng.random() < 0.4 else f"{a} {b}"
                    if base not in used:
                        used.add(base)
                        break
                name = f"{base} {rng.choice(SUFFIXES)}"
            stem = name.split()[0].lower()
            firms.append({"firm_id": fid, "name": name, "tier": tier, "domain": f"{stem}{fid}.com",
                          "offices": {"top_25": rng.randint(12, 40), "top_100": rng.randint(4, 14),
                                      "regional": rng.randint(1, 4), "corporate": 1}[tier]})

    # ---- salesforce accounts (one per firm, plus injected duplicates) -------------------------
    accounts, acc_of = [], {}
    aid = 1000
    for f in firms:
        aid += 1
        accounts.append({"account_id": f"001{aid}", "name": f["name"], "website": f["domain"],
                         "tier": f["tier"], "owner": rng.choice(["ae_1", "ae_2", "ae_3", "ae_4", "ae_5"]),
                         "last_activity_date": None, "_firm": f["firm_id"]})
        acc_of[f["firm_id"]] = [f"001{aid}"]
    for f in rng.sample([x for x in firms if x["tier"] != "corporate"], 22):
        aid += 1
        variant = f["name"].replace("&", "and").replace(" LLP", "").replace(" PLLC", "").replace(" PS", "")
        variant = variant if variant != f["name"] else f["name"] + " (Tax)"
        r = rng.random()  # same site, a 'cpa' variant, or a legacy domain from an acquired firm
        website = f["domain"] if r < 0.4 else (f["domain"].replace(".com", "cpa.com") if r < 0.7
                                               else f"{rng.choice(SURNAMES).lower()}legacy{aid}.com")
        dup = {"account_id": f"001{aid}", "name": variant, "website": website, "tier": f["tier"],
               "owner": rng.choice(["ae_1", "ae_2", "ae_3", "ae_4", "ae_5"]), "last_activity_date": None,
               "_firm": f["firm_id"]}
        accounts.append(dup)
        acc_of[f["firm_id"]].append(dup["account_id"])
        truth["duplicate_account"].append(dup["account_id"])

    # ---- contacts: buying groups are practice leaders, not "the account" -----------------------
    contacts, cid = [], 5000
    for f in firms:
        roles = ["CFO", "Controller"] if f["tier"] == "corporate" else \
            [f"{p} Lead Partner" for p in PRACTICES] + ["Managing Partner", "Innovation Director"]
        for r in roles:
            if rng.random() < (0.9 if f["tier"] != "regional" else 0.6):
                cid += 1
                contacts.append({"contact_id": f"003{cid}", "account_id": rng.choice(acc_of[f["firm_id"]]),
                                 "name": f"{rng.choice(FIRST)} {rng.choice(SURNAMES)}", "title": r,
                                 "email": f"c{cid}@{f['domain']}", "_firm": f["firm_id"]})

    # ---- opportunities: land one practice, expand to others ---------------------------------
    opps, oid = [], 7000
    partner_regs = []
    motion_by_tier = {"top_25": ["enterprise_direct"] * 3 + ["channel_partner"],
                      "top_100": ["enterprise_direct", "midmarket_outbound", "midmarket_outbound", "channel_partner"],
                      "regional": ["midmarket_outbound", "channel_partner", "inbound_waitlist", "inbound_waitlist"],
                      "corporate": ["inbound_waitlist", "enterprise_direct", "midmarket_outbound"]}
    win_rate = {"top_25": 0.32, "top_100": 0.22, "regional": 0.16, "corporate": 0.18}
    for f in firms:
        if rng.random() > {"top_25": 0.8, "top_100": 0.6, "regional": 0.45, "corporate": 0.5}[f["tier"]]:
            continue
        products = ["Corporate Close"] if f["tier"] == "corporate" else rng.sample(PRACTICES, 3)
        landed = None
        t = _d(rng, date(2025, 3, 1), date(2025, 12, 1))
        for k, prod in enumerate(products):
            if k > 0 and (landed is None or rng.random() > 0.55):
                break
            oid += 1
            motion = rng.choice(motion_by_tier[f["tier"]])
            created = t if k == 0 else landed + timedelta(days=rng.randint(30, 150))
            if created >= TODAY:
                break
            won = rng.random() < (win_rate[f["tier"]] * (2.2 if k > 0 else 1))
            closed = created + timedelta(days=rng.randint(40, 160))
            stage = ("Closed Won" if won else "Closed Lost") if closed < TODAY else \
                rng.choice(["Discovery", "Pilot", "Security Review", "Proposal"])
            size = {"top_25": 2.6, "top_100": 1.4, "regional": 0.6, "corporate": 1.0}[f["tier"]]
            amount = int(ARR[prod] * size * rng.uniform(0.7, 1.3) / 1000) * 1000
            truth_type = "expansion" if k > 0 else "new_logo"
            # land and expand often happen on different SF accounts when duplicates exist
            acct = acc_of[f["firm_id"]][-1 if (k > 0 and len(acc_of[f["firm_id"]]) > 1) else 0]
            sf_type = "Expansion" if truth_type == "expansion" else "New Business"
            if truth_type == "expansion" and (len(acc_of[f["firm_id"]]) > 1 or rng.random() < 0.25):
                sf_type = "New Business"
                truth["expansion_as_new_logo"].append(f"006{oid}")
            o = {"opportunity_id": f"006{oid}", "account_id": acct, "product": prod, "type": sf_type,
                 "lead_source": {"channel_partner": "Partner", "inbound_waitlist": "Waitlist",
                                 "midmarket_outbound": "Outbound", "enterprise_direct": "AE Sourced"}[motion],
                 "stage": stage, "amount": amount, "created_date": created,
                 "close_date": closed if stage.startswith("Closed") else None,
                 "_firm": f["firm_id"], "_motion": motion, "_truth_type": truth_type}
            if motion == "channel_partner":
                partner_regs.append({"registration_id": f"PR{oid}", "partner": rng.choice(PARTNERS),
                                     "firm_name": f["name"], "domain": f["domain"],
                                     "registered_at": created - timedelta(days=rng.randint(3, 40)),
                                     "_opp": o["opportunity_id"]})
                if rng.random() < 0.3:  # AE also claims it
                    o["lead_source"] = "AE Sourced"
                    truth["partner_double_claim"].append(o["opportunity_id"])
            opps.append(o)
            if won and stage == "Closed Won":
                landed = closed

    # ---- activity: Outreach webhooks and Gong calls -----------------------------------------
    outreach, gong, eid = [], [], 900000
    for c in contacts:
        f = firms[c["_firm"] - 1]
        if rng.random() > 0.7:
            continue
        for _ in range(rng.randint(1, 8)):
            eid += 1
            ts = _d(rng, date(2026, 4, 1), date(2026, 9, 30))
            ev = {"event_id": f"ev_{eid}", "message_id": f"msg_{eid}", "contact_email": c["email"],
                  "type": rng.choices(["email_sent", "email_opened", "email_replied"], [0.7, 0.22, 0.08])[0],
                  "ts": ts}
            outreach.append(ev)
            if rng.random() < 0.06:  # webhook retry: same event id delivered again
                outreach.append(dict(ev))
                truth["webhook_retry"].append(ev["event_id"])
            elif rng.random() < 0.03:  # retry that regenerated its event id, same message
                eid += 1
                outreach.append(dict(ev, event_id=f"ev_{eid}"))
                truth["regenerated_event_id"].append(f"ev_{eid}")
    for o in opps:
        if o["stage"] in ("Discovery", "Pilot", "Security Review", "Proposal") or o["stage"] == "Closed Won":
            for _ in range(rng.randint(1, 5)):
                gong.append({"call_id": f"g{len(gong) + 1}", "account_domain": firms[o["_firm"] - 1]["domain"],
                             "attendee_emails": [], "held_at": _d(rng, date(2026, 5, 1), date(2026, 9, 29)),
                             "duration_min": rng.randint(15, 60)})

    # salesforce last_activity_date: synced from tools, except where the sync silently stopped
    last_true = {}
    for ev in outreach:
        if ev["type"] == "email_replied":
            fdm = ev["contact_email"].split("@")[1]
            last_true[fdm] = max(last_true.get(fdm, date.min), ev["ts"])
    for g in gong:
        last_true[g["account_domain"]] = max(last_true.get(g["account_domain"], date.min), g["held_at"])
    for a in accounts:
        f = firms[a["_firm"] - 1]
        lt = last_true.get(f["domain"])
        if lt is None:
            continue
        if a["account_id"] == acc_of[f["firm_id"]][0] and rng.random() < 0.18 and lt > date(2026, 8, 1):
            a["last_activity_date"] = lt - timedelta(days=rng.randint(70, 120))  # sync stopped weeks ago
            truth["activity_sync_gap"].append(a["account_id"])
        else:
            a["last_activity_date"] = lt

    # ---- inbound waitlist (the form on the website) ------------------------------------------
    waitlist = []
    for i in range(160):
        f = rng.choice(firms)
        waitlist.append({"submission_id": f"wl{i}", "company": f["name"], "email": f"lead{i}@{f['domain']}",
                         "size": {"top_25": "100+ employees", "top_100": "100+ employees",
                                  "regional": rng.choice(["11-50 employees", "51-100 employees"]),
                                  "corporate": rng.choice(["51-100 employees", "100+ employees"])}[f["tier"]],
                         "use_case": "In house accounting" if f["tier"] == "corporate" else
                         rng.choice(["Client services accounting", "Tax preparation & planning", "Audit & assurance"]),
                         "heard_from": rng.choice(["LinkedIn", "Word of mouth / referral", "Industry event / conference",
                                                   "Search (e.g., Google, ChatGPT)", "Podcast"]),
                         "submitted_at": _d(rng, date(2026, 6, 1), date(2026, 9, 30)), "_firm": f["firm_id"]})

    systems = {"salesforce": {"accounts": accounts, "contacts": contacts, "opportunities": opps},
               "outreach_events": outreach, "gong_calls": gong, "partner_portal": partner_regs,
               "waitlist": waitlist}
    return systems, truth, firms
