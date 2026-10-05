"""Generate the BLIND second-annotation pack for RETRACT-ACT (ruling 22).

    python bench/agent/annotation/make_pack.py                 # writes bench/agent/annotation/out/
    python bench/agent/annotation/make_pack.py --check         # verifies the generated pack is blind
    open bench/agent/annotation/out/annotation-pack.html       # the form (macOS; any browser works offline)

Outputs (all under ``out/``, which is gitignored):
  annotation-pack.html          self-contained offline form (no network, no external assets)
  private/mapping.json          opaque item id -> (scenario id, decision point id). NOT part of the HTML. Do not open
                                it before annotating: it only names scenarios, but the ids reveal their class.

The HTML holds no gold action, no rationale, no system output, no scenario id, title, slug, category, tag, stakes tier,
cost weight or resolver hint. See ``narrative.py`` (whitelist) and ``--check``.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import narrative as nv

OUT = HERE / "out"
DEFAULT_SEED = 20261005
HTML_NAME = "annotation-pack.html"

INSTRUCTIONS = [
    (
        "You will see {n} decision points from small business scenarios. For each one, decide what the agent should do next, "
        "using ONLY the information shown (the timeline up to that moment). Work blind: do not look at any system output, "
        "any existing answer key, the benchmark documents or the mapping file before you finish."
    ),
    "Choose exactly one action per item:",
    "act: carry out the task now using the value the evidence justifies. Type that value.",
    "ask: do not act; ask the source or person who can supply the missing or deciding evidence.",
    "abstain: do not act and do not interrupt anyone.",
    "revalidate: you formed a plan earlier and the evidence has changed since; re-read it and act on the current value. Type that value.",
    (
        "A required task must be completed: if you cannot act safely, ask. An optional task can be dropped: if you cannot act "
        "safely, abstain. When asked to review an action that has already been taken, 'act' means let it stand and 'ask' means "
        "raise a problem with the people who must decide because it rests on something no longer justified."
    ),
    (
        "A mistaken action causes harm that is expensive to undo; asking costs a little; do not ask when the evidence clearly "
        "supports a value. Sources in the same origin group share an upstream origin (for example sister desks of one "
        "publisher), so they are not independent of each other."
    ),
    (
        "Items are independent: ignore similarities between items. Save often (the Save button downloads a JSON file; that "
        "file is the record). You can reload a partial session with Load. Add a one-line reason whenever you hesitate."
    ),
]


def build_pack(seed: int = DEFAULT_SEED) -> tuple[dict, dict]:
    raw = nv.load_raw_scenarios()
    chosen = nv.select_items(raw)
    rng = random.Random(seed)
    order = list(chosen)
    rng.shuffle(order)
    items = []
    mapping = {}
    for n, (sid, dp_id) in enumerate(order, 1):
        oid = nv.opaque_id(seed, sid, dp_id)
        if oid in mapping:  # 24-bit collision: extremely unlikely, but never silently merge items
            raise RuntimeError(f"opaque id collision {oid}")
        item = nv.build_item(raw[sid], dp_id)
        item["id"] = oid
        item["position"] = n
        items.append(item)
        mapping[oid] = {"scenario": sid, "decision_point": dp_id}
    instructions = [t.format(n=len(items)) if "{n}" in t else t for t in INSTRUCTIONS]
    pack = {"format": "retract-act-pack/1", "seed": seed, "n_items": len(items), "instructions": instructions, "items": items}
    pack_hash = hashlib.sha256(json.dumps(pack, sort_keys=True).encode()).hexdigest()
    pack["pack_hash"] = pack_hash
    private = {"format": "retract-act-mapping/1", "seed": seed, "pack_hash": pack_hash, "items": mapping}
    return pack, private


HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>RETRACT-ACT blind annotation</title>
<style>
:root{--bg:#fafafa;--fg:#1b1b1b;--card:#fff;--mut:#666;--line:#ddd;--acc:#1a56db;--ok:#0e7a3b}
@media (prefers-color-scheme:dark){:root{--bg:#161616;--fg:#eee;--card:#1f1f1f;--mut:#aaa;--line:#333;--acc:#7aa7ff;--ok:#5fd18a}}
body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.5 system-ui,sans-serif}
main{max-width:860px;margin:0 auto;padding:16px}
h1{font-size:1.3rem} h2{font-size:1.05rem;margin:.2rem 0}
.card{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:14px;margin:14px 0}
.mut{color:var(--mut);font-size:.9rem} table{border-collapse:collapse;width:100%;font-size:.9rem}
td,th{border-bottom:1px solid var(--line);padding:4px 6px;text-align:left;vertical-align:top}
.ev{display:grid;grid-template-columns:5.5rem 6.5rem 1fr;gap:6px;padding:3px 0;border-bottom:1px dashed var(--line)}
.ev.act{font-weight:600}
.decision{border-left:4px solid var(--acc);padding-left:10px;margin:10px 0}
label.opt{display:block;margin:4px 0} input[type=text]{width:100%;box-sizing:border-box;padding:6px;font:inherit;margin:4px 0}
.bar{position:sticky;top:0;background:var(--bg);padding:8px 0;border-bottom:1px solid var(--line);z-index:5;display:flex;gap:8px;flex-wrap:wrap;align-items:center}
button{font:inherit;padding:6px 12px;border:1px solid var(--line);border-radius:6px;background:var(--card);color:var(--fg);cursor:pointer}
.done{border-color:var(--ok)}
</style></head><body><main>
<h1>RETRACT-ACT: blind annotation</h1>
<div class="bar"><span id="progress" class="mut"></span><button id="save">Save (download JSON)</button>
<label class="mut">Load <input id="load" type="file" accept=".json,application/json"></label>
<label class="mut">Your name <input id="who" type="text" style="width:12rem"></label></div>
<div class="card" id="instr"></div>
<div id="items"></div>
<script type="application/json" id="pack-data">__PACK__</script>
<script>
(function(){
var pack=JSON.parse(document.getElementById('pack-data').textContent);
var ans={}; var KEY='retract-act-annotation-'+pack.pack_hash.slice(0,12);
try{var s=localStorage.getItem(KEY); if(s){ans=JSON.parse(s)||{};}}catch(e){}
function esc(t){var d=document.createElement('div');d.textContent=t;return d.innerHTML;}
function persist(){try{localStorage.setItem(KEY,JSON.stringify(ans));}catch(e){} progress();}
function progress(){var n=0;pack.items.forEach(function(it){if(ans[it.id]&&ans[it.id].action){n++;}});
 document.getElementById('progress').textContent=n+' of '+pack.n_items+' answered';
 pack.items.forEach(function(it){var c=document.getElementById('card-'+it.id);if(c){c.classList.toggle('done',!!(ans[it.id]&&ans[it.id].action));}});}
var instr=document.getElementById('instr');
instr.innerHTML=pack.instructions.map(function(t,i){return '<p'+(i==1?' class="mut"':'')+'>'+esc(t)+'</p>';}).join('');
var root=document.getElementById('items');
pack.items.forEach(function(it){
 var a=ans[it.id]||{};
 var h='<div class="card" id="card-'+it.id+'"><h2>Item '+it.position+' of '+pack.n_items+' <span class="mut">('+it.id+')</span></h2>';
 h+='<p class="mut"><b>What is known about the attributes</b></p><ul>'+it.attributes.map(function(x){return '<li>'+esc(x)+'</li>';}).join('')+'</ul>';
 h+='<p class="mut"><b>Sources</b></p><table><tr><th>source</th><th>reliability</th><th>origin group</th></tr>'+it.sources.map(function(s){return '<tr><td>'+esc(s.source)+'</td><td>'+esc(s.reliability)+'</td><td>'+esc(s.origin_group)+'</td></tr>';}).join('')+'</table>';
 h+='<p class="mut"><b>Timeline</b></p>'+it.events.map(function(e){return '<div class="ev'+(e.label==='Action taken'?' act':'')+'"><span class="mut">day '+e.day+'</span><span>'+esc(e.label)+'</span><span>'+esc(e.text)+'</span></div>';}).join('');
 h+='<div class="decision">'+it.decision.map(function(l){return '<p>'+esc(l)+'</p>';}).join('');
 it.actions.forEach(function(ac){h+='<label class="opt"><input type="radio" name="a-'+it.id+'" value="'+ac+'"'+(a.action===ac?' checked':'')+'> <b>'+ac+'</b></label>';});
 h+='<input type="text" id="v-'+it.id+'" placeholder="value you act on (for act or revalidate)" value="'+esc(a.value||'')+'">';
 h+='<input type="text" id="r-'+it.id+'" placeholder="one-line reason (optional)" value="'+esc(a.reason||'')+'"></div></div>';
 root.insertAdjacentHTML('beforeend',h);
});
function bind(){pack.items.forEach(function(it){
 var rs=document.getElementsByName('a-'+it.id);
 Array.prototype.forEach.call(rs,function(r){r.addEventListener('change',function(){ans[it.id]=ans[it.id]||{};ans[it.id].action=r.value;persist();});});
 ['v','r'].forEach(function(p){document.getElementById(p+'-'+it.id).addEventListener('input',function(ev){ans[it.id]=ans[it.id]||{};ans[it.id][p==='v'?'value':'reason']=ev.target.value;persist();});});
});}
bind(); progress();
document.getElementById('save').addEventListener('click',function(){
 var out={format:'retract-act-annotation/1',pack_hash:pack.pack_hash,annotator:document.getElementById('who').value,saved_at:new Date().toISOString(),items:{}};
 pack.items.forEach(function(it){var a=ans[it.id];if(a&&a.action){out.items[it.id]={action:a.action,value:(a.value||'').trim()||null,reason:(a.reason||'').trim()||null};}});
 var b=new Blob([JSON.stringify(out,null,1)],{type:'application/json'});var u=URL.createObjectURL(b);
 var l=document.createElement('a');l.href=u;l.download='annotations-'+pack.pack_hash.slice(0,8)+'.json';document.body.appendChild(l);l.click();l.remove();URL.revokeObjectURL(u);});
document.getElementById('load').addEventListener('change',function(ev){var f=ev.target.files[0];if(!f)return;var rd=new FileReader();
 rd.onload=function(){try{var d=JSON.parse(rd.result);if(d.pack_hash!==pack.pack_hash){alert('This file belongs to a different pack.');return;}
  ans={};Object.keys(d.items||{}).forEach(function(k){ans[k]=d.items[k];});document.getElementById('who').value=d.annotator||'';persist();location.reload();}catch(e){alert('Could not read the file.');}};rd.readAsText(f);});
})();
</script></main></body></html>
"""


def render_html(pack: dict) -> str:
    data = json.dumps(pack, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    return HTML.replace("__PACK__", data)


def write_pack(seed: int = DEFAULT_SEED, out: Path = OUT) -> tuple[Path, Path]:
    pack, private = build_pack(seed)
    out.mkdir(parents=True, exist_ok=True)
    (out / "private").mkdir(exist_ok=True)
    html_path = out / HTML_NAME
    html_path.write_text(render_html(pack), encoding="utf-8")
    map_path = out / "private" / "mapping.json"
    map_path.write_text(json.dumps(private, indent=1, sort_keys=True), encoding="utf-8")
    return html_path, map_path


# ------------------------------------------------------------------------------------------------- blindness check

FORBIDDEN_KEYS = {"gold", "gold_by_profile", "rationale", "resolvers", "slug", "title", "category", "tags",
                  "depends_on_decision", "stakes", "costs", "split", "description", "harm", "miss"}


def leak_report(html: str, raw: dict[str, dict]) -> list[str]:
    """Every way a gold/system/identifying field could appear in the HTML; empty list = blind."""
    problems: list[str] = []
    m = re.search(r'<script type="application/json" id="pack-data">(.*?)</script>', html, re.DOTALL)
    if not m:
        return ["pack data block not found"]
    data = json.loads(m.group(1).replace("<\\/", "</"))

    def walk(o, path="pack"):
        if isinstance(o, dict):
            for k, v in o.items():
                if k in FORBIDDEN_KEYS:
                    problems.append(f"forbidden key {k!r} at {path}")
                walk(v, f"{path}.{k}")
        elif isinstance(o, list):
            for i, v in enumerate(o):
                walk(v, f"{path}[{i}]")

    walk(data)
    for sid, s in raw.items():
        if re.search(rf"\b{re.escape(sid)}\b", html):
            problems.append(f"scenario id {sid} appears")
        for field in ("title", "slug", "description"):
            v = s.get(field)
            if v and v in html:
                problems.append(f"{sid} {field} text appears")
        for d in s["decision_points"]:
            if re.search(rf"\b{re.escape(d['id'])}\b", html):
                problems.append(f"decision point id {d['id']} appears")
            for g in [d.get("gold", {}), *d.get("gold_by_profile", {}).values()]:
                r = g.get("rationale")
                if r and r in html:
                    problems.append(f"{d['id']} gold rationale appears")
            if d.get("resolvers") and json.dumps(d["resolvers"]) in html:
                problems.append(f"{d['id']} resolvers appear")
        for tag in s.get("tags", []):
            if f'"{tag}"' in html:
                problems.append(f"{sid} tag {tag!r} appears")
        if s.get("category") and f'"{s["category"]}"' in html:
            problems.append(f"{sid} category appears")
    return problems


def check(out: Path = OUT) -> int:
    html_path = out / HTML_NAME
    if not html_path.is_file():
        print(f"no pack at {html_path}; run make_pack.py first", file=sys.stderr)
        return 2
    html = html_path.read_text(encoding="utf-8")
    raw = nv.load_raw_scenarios()
    problems = leak_report(html, raw)
    if re.search(r"https?://|src=|@import|<link ", html):
        problems.append("external reference found (the pack must work offline)")
    if problems:
        print("NOT BLIND:")
        for p in problems:
            print("  -", p)
        return 1
    print(f"blind check passed: {html_path} ({len(html)} bytes, no gold, system output, ids, titles, tags or weights)")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seed", type=int, default=DEFAULT_SEED, help="shuffle/id seed (default %(default)s)")
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--check", action="store_true", help="verify the already generated pack is blind and offline")
    a = ap.parse_args(argv)
    if a.check:
        return check(a.out)
    html_path, map_path = write_pack(a.seed, a.out)
    rc = check(a.out)
    print(f"pack:    {html_path}\nmapping: {map_path}  (private; do not open before annotating)")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
