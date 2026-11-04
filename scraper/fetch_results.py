#!/usr/bin/env python3
"""
The People's Count: pull unofficial statewide results from the Missouri SOS
election night site and write results.json in the shape the WordPress page reads.

Parses the page TEXT, not its markup, so it survives most layout changes.
The SOS page renders each contest as:
    <Contest name> <N> of <M> Precincts Reported
    <Choice> <Party?> <votes> <pct>%
County contests (COMBAT, County Executive) are not on the state feed; they are
read from county.json, which you edit by hand.
"""
import json, re, sys, datetime, pathlib, html as htmllib, http.cookiejar, urllib.parse, urllib.request

SOS_URLS = [
    "https://enr.sos.mo.gov/default.aspx",
    "https://enr.sos.mo.gov/EnrNet/default.aspx",
    "https://enr.sos.mo.gov/",
]

# civicAPI's national roll-up races (seats per party, Senate includes seats not up in 2026)
CIVIC_RACES = {"house": 87524, "senate": 87528}
CIVIC_URL = "https://civicapi.org/api/v2/race/{}"

HERE = pathlib.Path(__file__).parent
OUT = HERE.parent / "results.json"
COUNTY = HERE / "county.json"
JUDGES = HERE / "judges.json"
CONGRESS = HERE / "congress.json"
CONTESTS = json.loads((HERE / "contests.json").read_text())

def to_text(html):
    text = re.sub(r"<script.*?</script>|<style.*?</style>", " ", html, flags=re.S|re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"&nbsp;|&#160;", " ", text)
    return re.sub(r"\s+", " ", htmllib.unescape(text))

def postback(opener, html, url):
    # The ENR page (ASP.NET) ships an empty results panel; the numbers only come
    # back after "Choose election type" -> Submit. Replay that form post.
    data = {}
    for tag in re.findall(r"<input[^>]*type=\"hidden\"[^>]*>", html, flags=re.I):
        name = re.search(r'name="([^"]+)"', tag)
        if name:
            value = re.search(r'value="([^"]*)"', tag)
            data[name.group(1)] = htmllib.unescape(value.group(1)) if value else ""
    sel = re.search(r'<select name="([^"]*cboElectionNames)".*?</select>', html, flags=re.S)
    if not sel:
        return html
    opts = re.findall(r'<option([^>]*)value="([^"]+)"', sel.group(0))
    if not opts:
        return html
    chosen = next((v for attrs, v in opts if "selected" in attrs), opts[0][1])
    data[sel.group(1)] = chosen
    btn = re.search(r'<input[^>]*type="submit"[^>]*name="([^"]*btnElectionType)"', html)
    data[btn.group(1) if btn else "ctl00$MainContent$btnElectionType"] = "Submit"
    data["__EVENTTARGET"] = data["__EVENTARGUMENT"] = ""
    req = urllib.request.Request(url, data=urllib.parse.urlencode(data).encode())
    return opener.open(req, timeout=60).read().decode("utf-8", "ignore")

def fetch_html():
    tried = []
    for url in SOS_URLS:
        try:
            opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
            opener.addheaders = [("User-Agent", "KCDefender-PeoplesCount/1.0 (+kansascitydefender.com)")]
            resp = opener.open(url, timeout=30)
            html = resp.read().decode("utf-8", "ignore")
            if "Precincts Reported" not in html and "cboElectionNames" in html:
                html = postback(opener, html, resp.geturl())
            if "Precincts Reported" in to_text(html) or "electtable" in html:
                return html
            tried.append(f"{url}: no results table (landed on {resp.geturl()})")
        except Exception as e:
            tried.append(f"{url}: {e}")
    raise SystemExit("Could not read SOS results:\n  " + "\n  ".join(tried))

HEAD = re.compile(r"(?P<name>[A-Za-z0-9\.\'\-\u2019 ,&/]+?)\s+(?P<in>[\d,]+) of (?P<of>[\d,]+) Precincts Reported")
ROW = re.compile(r"(?P<label>[A-Za-z][A-Za-z\.\'\-\u2019 ,\(\)]+?)\s+(?P<party>Republican|Democratic|Libertarian|Green|Constitution|Better|Write-in|Write-In)?\s*(?P<votes>[\d,]+)\s+(?P<pct>[\d\.]+)%")

def parse(text):
    blocks = {}
    heads = list(HEAD.finditer(text))
    for i, h in enumerate(heads):
        end = heads[i+1].start() if i+1 < len(heads) else min(len(text), h.end()+4000)
        body = text[h.end():end]
        rows = []
        for r in ROW.finditer(body):
            label = r.group("label").strip()
            if label.lower().startswith("total votes"): break
            rows.append({"name": label, "party": r.group("party") or "", "votes": int(r.group("votes").replace(",",""))})
        blocks[h.group("name").strip()] = {"in": int(h.group("in").replace(",","")), "of": int(h.group("of").replace(",","")), "rows": rows}
    return blocks

def parse_table(html):
    # Fallback for the ENR results table (class "electtable"): a row with <strong>
    # starts a contest and carries the precinct count; then name | party | votes | pct rows.
    blocks, cur = {}, None
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", html, flags=re.S|re.I):
        cells = [to_text(td).strip() for td in re.findall(r"<td[^>]*>(.*?)</td>", tr, flags=re.S|re.I)]
        if not cells:
            continue
        if "<strong" in tr.lower():
            prec = re.search(r"([\d,]+)\s*(?:of|/)\s*([\d,]+)", " ".join(cells[1:]))
            cur = {"in": int(prec.group(1).replace(",","")) if prec else 0,
                   "of": int(prec.group(2).replace(",","")) if prec else 0, "rows": []}
            blocks[cells[0]] = cur
        elif cur is not None and len(cells) >= 3 and re.fullmatch(r"[\d,]+", cells[2]) and cells[0]:
            cur["rows"].append({"name": cells[0], "party": cells[1], "votes": int(cells[2].replace(",",""))})
    return blocks

def norm(s):
    # 2024 headings read "Constitutional Amendment 3"; tolerate "No. 3" either way
    return re.sub(r"\s+", " ", re.sub(r"\bno\.\s*", "", s.lower())).strip()

def find_block(blocks, needle):
    n = norm(needle)
    for name, b in blocks.items():
        if n in norm(name):
            return b
    return None

def match_option(rows, option):
    key = option.get("match", option["name"]).lower()
    for r in rows:
        if key in r["name"].lower():
            return r["votes"]
    return None

def fetch_congress(congress):
    # Fill seat counts from civicAPI; on any failure keep the last good numbers
    # from the previous results.json, else the hand-entered congress.json.
    try:
        prev = json.loads(OUT.read_text()).get("congress") or {}
    except Exception:
        prev = {}
    for chamber, race_id in CIVIC_RACES.items():
        ch = congress.get(chamber)
        if ch is None:
            continue
        if prev.get(chamber):
            ch.update(prev[chamber])
        try:
            req = urllib.request.Request(CIVIC_URL.format(race_id), headers={"User-Agent": "KCDefender-PeoplesCount/1.0 (+kansascitydefender.com)"})
            race = json.loads(urllib.request.urlopen(req, timeout=45).read().decode("utf-8"))
            if race.get("test_data"):
                raise ValueError("race is marked as test data")
            seats = {"Democratic": 0, "Republican": 0}
            other = 0
            for c in race.get("candidates") or []:
                n = int(c.get("seats") or 0)
                if c.get("party") in seats:
                    seats[c["party"]] += n
                else:
                    other += n
            ch["dem"], ch["rep"] = seats["Democratic"], seats["Republican"]
            ch["ind" if "ind" in ch else "other"] = other
            maj = ch.get("majority") or 0
            if maj and ch["dem"] >= maj:
                ch["called"] = "D"
            elif maj and ch["rep"] >= maj:
                ch["called"] = "R"
            congress["source"] = "civicAPI"
        except Exception as e:
            print(f"civicAPI {chamber}: {e} (keeping last good numbers)")
    return congress

def build():
    html = fetch_html()
    blocks = parse(to_text(html)) or parse_table(html)
    contests = []
    for c in CONTESTS:
        c = dict(c)
        blk = find_block(blocks, c.pop("sosMatch")) if "sosMatch" in c else None
        if blk:
            c["in"], c["of"] = blk["in"], blk["of"]
            for o in c["options"]:
                v = match_option(blk["rows"], o)
                if v is not None: o["votes"] = v
                o.pop("match", None)
        else:
            for o in c["options"]: o.pop("match", None)
        contests.append(c)
    # county contests, edited by hand
    if COUNTY.exists():
        contests += json.loads(COUNTY.read_text())
    order = ["propa","amd3","amd6","amd7","amd8","combat","cd5","exec","auditor"]
    contests.sort(key=lambda c: order.index(c["id"]) if c["id"] in order else 99)
    now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=-6))).strftime("%-I:%M %p CT")
    found = sum(1 for c in contests if c.get("in"))
    judges = json.loads(JUDGES.read_text()) if JUDGES.exists() else []
    out = {"updated": now, "contests": contests, "judges": judges, "matched": found}
    # national balance of power from civicAPI, falling back to congress.json
    if CONGRESS.exists():
        out["congress"] = fetch_congress(json.loads(CONGRESS.read_text()))
    OUT.write_text(json.dumps(out, indent=1))
    print(f"wrote {OUT}: {found} contests with data, {len(blocks)} blocks parsed from SOS")
    if len(blocks) == 0:
        sys.exit("Parsed zero contests. Open the SOS page and adjust HEAD/ROW patterns.")

if __name__ == "__main__":
    build()
