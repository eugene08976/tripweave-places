"""English brand names from Wikidata for Taiwan (spec section 1.3 step 5).

Taiwan rows whose name has no Latin letter and that carry brand.wikidata get the brand's English label in `alt`, so
"din tai fung" also finds the branches Overture names only 鼎泰豐. Wikidata labels are CC0.

Labels are fetched with wbgetentities, 50 ids per request, about one request a second, and saved to
labels/brands.json, which the workflow commits. If Wikidata is down, last month's file is used as is.
"""
import json
import os
import time
import urllib.parse
import urllib.request

API = "https://www.wikidata.org/w/api.php"
USER_AGENT = "tripweave-places/1 (https://github.com/eugene08976/tripweave-places; monthly brand label refresh)"
BATCH = 50


class MaxLag(Exception):
    pass


def load(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return dict(data.get("labels", {}))
    except FileNotFoundError:
        return {}


def save(path: str, labels: dict) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"source": "Wikidata (CC0), English labels via wbgetentities",
                   "labels": dict(sorted(labels.items(), key=lambda kv: int(kv[0][1:]) if kv[0][1:].isdigit() else 0))},
                  f, ensure_ascii=False, indent=1)
        f.write("\n")
    os.replace(tmp, path)


def _fetch_batch(ids: list, timeout: float, maxlag: bool = True) -> dict:
    params = {"action": "wbgetentities", "ids": "|".join(ids), "props": "labels", "languages": "en", "format": "json"}
    if maxlag:
        params["maxlag"] = "5"   # Wikidata asks automated clients to back off while its replicas lag
    qs = urllib.parse.urlencode(params)
    req = urllib.request.Request(f"{API}?{qs}", headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.load(r)
    if "error" in data:
        if data["error"].get("code") == "maxlag":
            raise MaxLag()
        raise RuntimeError(f"wikidata error: {data['error'].get('code')}")
    out = {}
    for qid, ent in (data.get("entities") or {}).items():
        lab = ((ent.get("labels") or {}).get("en") or {}).get("value")
        out[qid] = lab or ""
    return out


def refresh(qids, path: str, log=print, timeout: float = 30, pause: float = 1.0) -> tuple:
    """Fetch English labels for every id; on any failure keep the committed labels for the rest.

    Returns (labels, stats). An empty string records "Wikidata has no English label", so it isn't re-fetched as new.
    """
    cached = load(path)
    want = sorted({q for q in qids if q and q.startswith("Q") and q[1:].isdigit()}, key=lambda q: int(q[1:]))
    labels = dict(cached)
    stats = {"ids": len(want), "requests": 0, "fetched": 0, "failed_batches": 0, "from_cache": 0}
    for i in range(0, len(want), BATCH):
        batch = want[i:i + BATCH]
        try:
            got = None
            for attempt in range(4):
                try:
                    stats["requests"] += 1
                    got = _fetch_batch(batch, timeout, maxlag=attempt < 3)
                    break
                except MaxLag:
                    time.sleep(5 * (attempt + 1))
            if got is None:
                raise RuntimeError("no answer")
            labels.update(got)
            stats["fetched"] += len(got)
        except Exception as e:  # network, HTTP, maxlag: keep last month's labels for this batch
            stats["failed_batches"] += 1
            stats["from_cache"] += sum(1 for q in batch if q in cached)
            log(f"labels: batch {i // BATCH + 1} failed ({type(e).__name__}); using committed labels")
        time.sleep(pause)
    labels = {q: labels[q] for q in want if q in labels}  # only brands still in the data
    save(path, labels)
    return labels, stats
