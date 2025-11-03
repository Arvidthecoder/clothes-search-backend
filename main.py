# main.py
# ClothesFinder — robust single-file backend (revamped)
# - Full-page price extraction
# - Tolerant synonym matching and size handling (including jeans)
# - Multiple secondhand sites (Vinted, Sellpy, Tradera, Blocket, Plick, Facebook)
# - Returns best_match + top_results with breakdowns
# - Defensive: won't die on single-site failures; prints logs for debugging

from flask import Flask, request, jsonify
import requests, re, time, threading
from bs4 import BeautifulSoup
import concurrent.futures
from urllib.parse import urljoin, quote_plus

app = Flask(__name__)

# ---------------- CONFIG ----------------
USER_AGENT = "Mozilla/5.0 (compatible; ClothesFinder/5.0; +https://example.com)"
REQUEST_TIMEOUT = 10
MAX_SCRAPER_WORKERS = 6
MAX_PRODUCT_FETCH_WORKERS = 10
CACHE_TTL = 300
TOP_PER_SITE = 8          # how many listings to collect per site before fetching product pages
TOP_RETURN = 6            # how many top results to return to client
DEBUG_MODE = False        # set True to return more debug info (full_text) - careful with privacy/size

# ---------------- CACHE ----------------
_cache = {}
_cache_lock = threading.Lock()
def cache_get(k):
    with _cache_lock:
        v = _cache.get(k)
        if not v:
            return None
        ts, val = v
        if time.time() - ts > CACHE_TTL:
            del _cache[k]
            return None
        return val
def cache_set(k, val):
    with _cache_lock:
        _cache[k] = (time.time(), val)

# ---------------- UTIL ----------------
HEADERS = {"User-Agent": USER_AGENT}

def http_get(url, params=None):
    try:
        r = requests.get(url, params=params, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        return r.text
    except Exception as e:
        print(f"[http_get] FAILED {url} -> {e}")
        return None

def clean_text(t):
    if not t:
        return ""
    return re.sub(r'\s+', ' ', t).strip()

def parse_bool(v):
    if isinstance(v, bool):
        return v
    if v is None:
        return None
    s = str(v).strip().lower()
    if s in ("true","ja","yes","1"):
        return True
    if s in ("false","nej","no","0"):
        return False
    return None

# ---------------- SYNONYMS (expanded but editable) ----------------
# All comparisons are done case-insensitively on cleaned text.
SYNONYMS = {
    # items
    "byxor": ["byxa","byxor","pants","träningsbyxor","joggers","mjukisbyxor","mysbyxor","träningsbyxa"],
    "jeans": ["jeans","denim","jeansen","levis","501"],
    "hoodie": ["hoodie","luvtröja","hoodtröja","zip hoodie","hoodie med dragkedja","hood med dragkedja"],
    "tröja": ["tröja","tröjor","pullover","sweater","överdel","top"],
    "t-shirt": ["t-shirt","tshirt","tee","kortärmad tröja","t shirt"],
    "jacka": ["jacka","jackor","coat","puffer","bomber","anorak","windbreaker"],
    # styles
    "baggy": ["baggy","loose","oversized","wide"],
    "skinny": ["skinny","tight","slim","slimfit"],
    "training": ["träning","tränings","sport","gym","athletic","track","training"],
    "mjukis": ["mjukis","mys","lounge","casual","soft","relax"],
    "utomhus": ["utomhus","outdoor","friluft","vandrings","hiking"],
    "luvtröja": ["luvtröja","hoodie med dragkedja","zip hoodie"],
    # colors
    "svart": ["svart","svarta","black","dark","mörk"],
    "vit": ["vit","vita","white","offwhite","ljus"],
    "blå": ["blå","blåa","blue","navy","denim"],
    "grå": ["grå","gråa","gray","grey","ljusgrå"],
    # gender
    "herr": ["herr","man","men","male"],
    "dam": ["dam","kvinna","women","female"],
    "unisex": ["unisex","både","alla"],
    # kids
    "barn": ["barn","kids","junior","pojke","flicka","child","baby","youth"],
    # brands (examples, add more as needed)
    "adidas": ["adidas"],
    "nike": ["nike"],
    "levi": ["levi","levis","levi's"]
}

def term_in_text(text, term):
    """Return True if term or any synonyms appear in text."""
    if not term:
        return False
    t = clean_text(text).lower()
    term_key = term.lower()
    # direct
    if term_key in t:
        return True
    # synonyms
    alts = SYNONYMS.get(term_key, [])
    for a in alts:
        if a in t:
            return True
    # sometimes user input equals a synonym key; check reverse mapping
    # check all keys where given term appears in synonyms
    for k, vals in SYNONYMS.items():
        if term_key in vals and k in t:
            return True
    return False

# ---------------- SIZE / JEANS LOGIC ----------------
def parse_jeans(text):
    """Find waist/length patterns in text. Return dict or None."""
    if not text:
        return None
    t = text.lower()
    # W32 L30 or W 32 L 30
    m = re.search(r'w\s?(\d{2})\s*[^\d]{1,3}\s*l\s?(\d{2})', t)
    if m:
        return {"waist": int(m.group(1)), "length": int(m.group(2))}
    # 32/30 or 32x30 or 32 30 (common)
    m2 = re.search(r'\b(\d{2})\s*[\/x ]\s*(\d{2})\b', t)
    if m2:
        return {"waist": int(m2.group(1)), "length": int(m2.group(2))}
    # single waist number
    m3 = re.search(r'\b(2[6-9]|3[0-9]|4[0-4])\b', t)  # waist plausible range 26-44
    if m3:
        return {"waist": int(m3.group(1)), "length": None}
    return None

def waist_to_size(waist):
    try:
        w = int(waist)
    except:
        return None
    if w <= 28: return "XS"
    if w <= 30: return "S"
    if w <= 32: return "M"
    if w <= 34: return "L"
    if w <= 36: return "XL"
    return "XXL"

def detect_kids_by_size_or_text(text):
    """Return True if text contains kid numeric sizes or explicit kids words."""
    if not text:
        return False
    t = clean_text(text).lower()
    # numeric child sizes (e.g. 92,98,...170)
    if re.search(r'\b(9[0-9]|1[0-6][0-9]|170)\b', t):  # 90-170 roughly
        return True
    if any(k in t for k in SYNONYMS.get("barn", [])):
        return True
    return False

# ---------------- PRICE EXTRACTION ----------------
def parse_price_from_page(html_text):
    """Return smallest found price or None. Looks for 'kr', ':-', 'sek', or 3-5 digit numbers as fallback."""
    if not html_text:
        return None
    text = clean_text(html_text)
    # look for currency markers
    matches = re.findall(r'(\d[\d\s\.,]*\d)\s*(kr|:-|sek)\b', text, flags=re.IGNORECASE)
    nums = []
    for m in matches:
        raw = m[0]
        n = re.sub(r'[^\d]', '', raw)
        if n:
            try:
                nums.append(float(n))
            except:
                pass
    if nums:
        return min(nums)
    # fallback: any 3-5 digit numbers (be careful)
    fallback = re.findall(r'\b(\d{3,5})\b', text)
    nums2 = []
    for n in fallback:
        try:
            nums2.append(float(n))
        except:
            pass
    if nums2:
        return min(nums2)
    return None

# ---------------- SCRAPERS ----------------
# Each scraper returns a list of candidate dicts: {site,title,url, snippet(optional)}
# We intentionally keep list of candidates small (TOP_PER_SITE) to limit requests.

def scrape_vinted(query, filters):
    out = []
    q = quote_plus(query)
    url = f"https://www.vinted.se/catalog?search_text={q}"
    html = http_get(url)
    if not html:
        return out
    soup = BeautifulSoup(html, "html.parser")
    anchors = soup.select("a.catalog-item__link")
    if not anchors:
        anchors = [a for a in soup.find_all("a", href=True) if "/item/" in a.get("href","")]
    seen = set()
    for a in anchors[:TOP_PER_SITE]:
        href = a.get("href")
        if not href:
            continue
        url_full = urljoin("https://www.vinted.se", href) if href.startswith("/") else href
        if url_full in seen:
            continue
        seen.add(url_full)
        title = clean_text(a.get_text() or a.get("title") or "")
        out.append({"site":"Vinted","title":title,"url":url_full})
    return out

def scrape_tradera(query, filters):
    out = []
    q = quote_plus(query)
    url = f"https://www.tradera.com/search?q={q}"
    html = http_get(url)
    if not html:
        return out
    soup = BeautifulSoup(html, "html.parser")
    anchors = soup.select("a.listing-card__link")
    if not anchors:
        anchors = [a for a in soup.find_all("a", href=True) if "/item/" in a.get("href","")]
    seen=set()
    for a in anchors[:TOP_PER_SITE]:
        href = a.get("href")
        if not href: continue
        url_full = urljoin("https://www.tradera.com", href) if href.startswith("/") else href
        if url_full in seen: continue
        seen.add(url_full)
        title = clean_text(a.get_text() or a.get("title") or "")
        out.append({"site":"Tradera","title":title,"url":url_full})
    return out

def scrape_generic(query, filters, base, contains=None, site_name=None):
    out=[]
    q = quote_plus(query)
    # try both patterns: base?q= and base+q
    url1 = base if "?" in base else (base + "?q=" + q)
    html = http_get(url1)
    if not html:
        html = http_get(base + q)
        if not html:
            return out
    soup = BeautifulSoup(html, "html.parser")
    anchors = soup.find_all("a", href=True)[:TOP_PER_SITE * 3]
    seen=set()
    for a in anchors:
        href = a.get("href")
        if not href:
            continue
        if contains and contains not in href:
            # still allow if contains is None
            continue
        url_full = urljoin(base, href) if not href.startswith("http") else href
        if url_full in seen:
            continue
        seen.add(url_full)
        title = clean_text(a.get_text() or a.get("title") or "")
        if not title:
            # fallback: combine text of children
            title = clean_text(' '.join([c.strip() for c in a.find_all(text=True)]))
        if title:
            out.append({"site": site_name or base.split("//")[-1].split("/")[0], "title": title, "url": url_full})
    return out

# Define SCRAPERS mapping (name, function)
SCRAPERS = [
    ("Vinted", scrape_vinted),
    ("Sellpy", lambda q,f: scrape_generic(q,f,"https://www.sellpy.se/sok/","/product/","Sellpy")),
    ("Tradera", scrape_tradera),
    ("Blocket", lambda q,f: scrape_generic(q,f,"https://www.blocket.se/annonser/hela_sverige","/annons/","Blocket")),
    ("Plick", lambda q,f: scrape_generic(q,f,"https://www.plick.com/search/","/p/","Plick")),
    ("Facebook", lambda q,f: scrape_generic(q,f,"https://m.facebook.com/marketplace/search/","/marketplace/item/","Facebook Marketplace")),
]

# ---------------- PRODUCT PAGE ENRICH ----------------
def enrich_product(prod):
    """Fetch product url page, extract full text and page price and parse jeans sizes etc."""
    url = prod.get("url")
    title = prod.get("title","")
    prod["_full_text"] = title
    prod["_page_price"] = None
    prod["_jeans"] = None
    prod["_inferred_size"] = None
    try:
        if not url:
            return prod
        html = http_get(url)
        if not html:
            return prod
        soup = BeautifulSoup(html, "html.parser")
        text = title + " " + soup.get_text(separator=' ')
        text = clean_text(text)
        prod["_full_text"] = text
        price = parse_price_from_page(text)
        prod["_page_price"] = price
        jeans = parse_jeans(text)
        if jeans:
            prod["_jeans"] = jeans
            if jeans.get("waist"):
                prod["_inferred_size"] = waist_to_size(jeans["waist"])
        else:
            # try to detect token M/L/XS etc
            tokens = re.findall(r'\b(xs|s|m|l|xl|xxl)\b', text, flags=re.IGNORECASE)
            if tokens:
                prod["_inferred_size"] = tokens[0].upper()
            else:
                # detect numeric child size
                m = re.search(r'\b(9[0-9]|1[0-6][0-9])\b', text)
                if m:
                    prod["_inferred_child_size"] = int(m.group(1))
    except Exception as e:
        print(f"[enrich_product] error for {prod.get('url')} -> {e}")
    return prod

# ---------------- SCORING ----------------
WEIGHTS = {
    "item": 30,
    "brand": 20,
    "style": 15,
    "gender": 10,
    "kids": 15,
    "color": 5,
    "size": 10,
    "price": 10
}

def price_points(page_price, price_max):
    """Return 0..WEIGHTS['price']"""
    if price_max is None or page_price is None:
        return 0
    try:
        pm = float(price_max)
        p = float(page_price)
    except:
        return 0
    if p <= pm:
        return WEIGHTS["price"]
    # proportionally scale down: if p >= 2*pm -> 0
    ratio = (p - pm) / pm
    score = WEIGHTS["price"] * max(0.0, 1.0 - ratio)
    return round(score, 2)

def score_one(prod, filters, strict_kids=True):
    """
    Returns (score_float, breakdown dict).
    strict_kids: if True enforce veto for kids mismatch; if False allow partial matches (for debugging)
    """
    breakdown = {}
    full = (prod.get("_full_text") or prod.get("title","")).lower()
    score = 0.0

    # item (veto if not found)
    item = (filters.get("item") or "").strip()
    if item:
        item_found = term_in_text(full, item)
        breakdown["item_found"] = bool(item_found)
        if not item_found:
            # try fuzzy token presence: check tokens intersection between item and full text
            # split item into tokens and check if any synonym appears
            found_any = False
            for token in re.findall(r'\w+', item.lower()):
                if term_in_text(full, token):
                    found_any = True
                    break
            if not found_any:
                breakdown["veto"] = "item_missing"
                return (0.0, breakdown)
        score += WEIGHTS["item"]
    else:
        breakdown["item_found"] = False

    # brand
    brand = (filters.get("brand") or "").strip()
    if brand:
        if term_in_text(full, brand):
            score += WEIGHTS["brand"]
            breakdown["brand"] = True
        else:
            breakdown["brand"] = False

    # style
    style = (filters.get("style") or "").strip()
    if style:
        if term_in_text(full, style):
            score += WEIGHTS["style"]
            breakdown["style"] = True
        else:
            breakdown["style"] = False

    # gender
    gender = (filters.get("gender") or "").strip().lower()
    inferred_gender = None
    if any(k in full for k in SYNONYMS.get("herr", [])):
        inferred_gender = "herr"
    elif any(k in full for k in SYNONYMS.get("dam", [])):
        inferred_gender = "dam"
    else:
        inferred_gender = None
    breakdown["inferred_gender"] = inferred_gender
    if gender:
        if inferred_gender and inferred_gender != gender:
            # if clearly opposite -> veto
            breakdown["veto"] = "gender_mismatch"
            return (0.0, breakdown)
        # if explicit match in text -> full points, otherwise partial if inferred adult
        if term_in_text(full, gender):
            score += WEIGHTS["gender"]
            breakdown["gender"] = "explicit"
        else:
            if inferred_gender is None:
                # maybe inferred via size
                if prod.get("_inferred_size"):
                    score += WEIGHTS["gender"] * 0.5
                    breakdown["gender"] = "inferred_from_size_partial"
                else:
                    breakdown["gender"] = False
            else:
                score += WEIGHTS["gender"] * 0.5
                breakdown["gender"] = "inferred_partial"

    # kids detection + veto
    kids_filter = parse_bool(filters.get("kids"))
    is_kid = detect_kids_by_size_or_text(full)
    breakdown["is_kid_detected"] = bool(is_kid)
    if kids_filter is True:
        if not is_kid:
            if strict_kids:
                breakdown["veto"] = "expected_child_but_not_detected"
                return (0.0, breakdown)
            else:
                # allow but penalize
                score -= 10
    elif kids_filter is False:
        if is_kid:
            if strict_kids:
                breakdown["veto"] = "expected_adult_but_child_detected"
                return (0.0, breakdown)
            else:
                score -= 10
        else:
            # adult bonus (partial)
            score += WEIGHTS["kids"] * 0.5

    # color
    color = (filters.get("color") or "").strip()
    if color:
        if term_in_text(full, color):
            score += WEIGHTS["color"]
            breakdown["color"] = True
        else:
            breakdown["color"] = False

    # size matching (including jeans conversion)
    size_filter = (filters.get("size") or "").strip()
    matched_size = False
    if size_filter:
        size_filter_norm = size_filter.lower()
        # direct match with inferred size
        if prod.get("_inferred_size") and prod["_inferred_size"].lower() == size_filter_norm:
            matched_size = True
        # check jeans waist mapping
        if not matched_size and prod.get("_jeans") and prod["_jeans"].get("waist"):
            ws = prod["_jeans"]["waist"]
            if waist_to_size(ws).lower() == size_filter_norm:
                matched_size = True
        # direct token present (user may pass numeric 32 etc)
        if not matched_size and re.search(r'\b' + re.escape(size_filter) + r'\b', full):
            matched_size = True
        if matched_size:
            score += WEIGHTS["size"]
    breakdown["size_matched"] = matched_size

    # page price points
    page_price = prod.get("_page_price")
    pp = price_points(page_price, filters.get("price_max"))
    breakdown["price_points"] = pp
    score += pp

    # small metadata bonus
    if prod.get("url"): score += 1
    if prod.get("title"): score += 1

    final = max(0.0, min(100.0, round(score, 2)))
    breakdown["final"] = final
    return (final, breakdown)

# ---------------- ORCHESTRATOR ----------------
def run_scrapers(query, filters):
    """Run all site scrapers concurrently and return combined candidate list."""
    candidates = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(MAX_SCRAPER_WORKERS, len(SCRAPERS))) as ex:
        futures = {ex.submit(fn, query, filters): name for (name, fn) in SCRAPERS}
        for fut in concurrent.futures.as_completed(futures, timeout=30):
            site = futures[fut]
            try:
                res = fut.result()
            except Exception as e:
                print(f"[run_scrapers] {site} scraper failed: {e}")
                res = []
            if res:
                # each res element should be dict with title,url
                candidates.extend(res)
    return candidates

def enrich_candidates(candidates, max_workers=MAX_PRODUCT_FETCH_WORKERS):
    """Fetch product pages concurrently to enrich candidates with _full_text and _page_price."""
    enriched = []
    # limit total product fetches to avoid huge loads
    limit = min(len(candidates), TOP_PER_SITE * len(SCRAPERS))
    candidates = candidates[:limit]
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = [ex.submit(enrich_product, c) for c in candidates]
        for fut in concurrent.futures.as_completed(futures, timeout=60):
            try:
                enriched.append(fut.result())
            except Exception as e:
                print(f"[enrich_candidates] product fetch failed: {e}")
    return enriched

def find_best(query, filters, top_n=TOP_RETURN, strict_kids=True):
    """Full pipeline: scrapers -> enrich -> score -> sort -> return top_n"""
    cache_key = f"find|{query}|{str(filters)}"
    cached = cache_get(cache_key)
    if cached:
        return cached

    candidates = run_scrapers(query, filters)
    if not candidates:
        cache_set(cache_key, [])
        return []

    enriched = enrich_candidates(candidates)
    scored = []
    for p in enriched:
        try:
            sc, breakdown = score_one(p, filters, strict_kids=strict_kids)
        except Exception as e:
            print(f"[find_best] scoring failed for {p.get('url')} -> {e}")
            sc, breakdown = 0.0, {"final": 0}
        p["_rating"] = sc
        p["_breakdown"] = breakdown
        # normalize price
        try:
            p["_price_norm"] = float(p.get("_page_price")) if p.get("_page_price") is not None else None
        except:
            p["_price_norm"] = None
        scored.append(p)

    # ensure we return useful alternatives: if everything vetoed to 0, still return top by fuzzy item match
    filtered = [p for p in scored if p["_rating"] > 0]
    if not filtered:
        # fallback: select top 10 by fuzzy item token presence (not 0-rated veto)
        scored.sort(key=lambda x: x["_rating"], reverse=True)
        top = scored[:top_n]
        cache_set(cache_key, top)
        return top

    # sort by rating desc then price asc (None price -> INF)
    def sortkey(x):
        price = x.get("_price_norm")
        return (-x.get("_rating",0), float('inf') if price is None else price)
    filtered.sort(key=sortkey)
    top = filtered[:top_n]
    cache_set(cache_key, top)
    return top

# ---------------- ROUTES ----------------
@app.route("/")
def home():
    return jsonify({"status":"ok","message":"ClothesFinder running"})

@app.route("/find_item", methods=["POST"])
def find_item_route():
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error":"No JSON payload provided"}), 400

    # read inputs (Adalo usually sends strings)
    brand = data.get("brand")
    item = data.get("item")
    color = data.get("color")
    style = data.get("style")
    gender = data.get("gender")
    kids = parse_bool(data.get("kids"))
    size = data.get("size")
    price_max = data.get("price_max")
    # try to convert price_max to float if possible
    try:
        if price_max is not None and price_max != "":
            price_max = float(price_max)
        else:
            price_max = None
    except:
        price_max = None

    # build query (used by scrapers)
    if data.get("query"):
        query = data.get("query")
    else:
        parts = [p for p in [brand, item, color, style] if p]
        query = " ".join(parts)

    filters = {
        "brand": brand,
        "item": item,
        "color": color,
        "style": style,
        "gender": gender,
        "kids": kids,
        "size": size,
        "price_max": price_max
    }

    if not query:
        return jsonify({"error":"No query or filters provided"}), 400

    # run pipeline; if DEBUG_MODE True, relax kids veto for easier debugging
    strict_kids = not DEBUG_MODE
    top = find_best(query, filters, top_n=TOP_RETURN, strict_kids=strict_kids)

    # build response
    def fmt(p):
        out = {
            "site": p.get("site"),
            "title": p.get("title"),
            "url": p.get("url"),
            "price": p.get("_page_price"),
            "rating": p.get("_rating"),
            "breakdown": p.get("_breakdown")
        }
        if DEBUG_MODE:
            out["_full_text"] = p.get("_full_text")
            out["_jeans"] = p.get("_jeans")
            out["_inferred_size"] = p.get("_inferred_size")
        return out

    if not top:
        # no matches - return helpful message and empty list (404 previously)
        return jsonify({
            "best_match": None,
            "top_results": [],
            "message": "Inget resultat hittades. Testa att bredda sökningen eller ta bort vissa filter."
        }), 404

    return jsonify({
        "best_match": fmt(top[0]),
        "top_results": [fmt(x) for x in top],
        "count": len(top)
    })

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
