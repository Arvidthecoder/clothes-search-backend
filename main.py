# main.py
# ClothesFinder — robust single-file backend
# - Full product page scraping for price extraction
# - Synonym and size handling (including jeans sizes)
# - Scoring 0-100 with price rules: <=price_max => +10; >price_max => proportionally less
# - Sites: Vinted, Sellpy, Tradera, Blocket, Plick, Facebook Marketplace
# - Return best_match + top_results

from flask import Flask, request, jsonify
import requests, re, time, threading
from bs4 import BeautifulSoup
import concurrent.futures
from urllib.parse import urljoin, quote_plus

app = Flask(__name__)

# ---------------- CONFIG ----------------
USER_AGENT = "Mozilla/5.0 (compatible; ClothesFinder/4.1; +https://example.com)"
REQUEST_TIMEOUT = 10
MAX_WORKERS = 8
PRODUCT_PAGE_WORKERS = 12
CACHE_TTL = 300  # seconds
TOP_PER_SITE = 8  # how many listings per site to fetch product pages for

# ---------------- SIMPLE CACHE ----------------
_cache = {}
_cache_lock = threading.Lock()

def cache_get(key):
    with _cache_lock:
        item = _cache.get(key)
        if not item:
            return None
        ts, val = item
        if time.time() - ts > CACHE_TTL:
            del _cache[key]
            return None
        return val

def cache_set(key, val):
    with _cache_lock:
        _cache[key] = (time.time(), val)

# ---------------- UTILITIES ----------------
HEADERS = {"User-Agent": USER_AGENT}

def http_get(url, params=None):
    try:
        r = requests.get(url, params=params, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        return r.text
    except Exception:
        return None

def clean_text(t):
    if not t:
        return ""
    return re.sub(r'\s+', ' ', t).strip()

def parse_boolean_input(v):
    if isinstance(v, bool):
        return v
    if v is None:
        return None
    s = str(v).strip().lower()
    if s in ("true", "ja", "yes", "1"):
        return True
    if s in ("false", "nej", "no", "0"):
        return False
    return None

# ---------------- SYNONYMS (expanded) ----------------
# Add more terms over time. All lowercased comparisons are used.
SYNONYMS = {
    # items
    "byxor": ["byxa","byxor","pants","träningsbyxor","joggers","mjukisbyxor","mysbyxor"],
    "jeans": ["jeans","denim","jeansen","levis","501"],
    "hoodie": ["hoodie","luvtröja","hoodtröja","zip hoodie","hoodie med dragkedja","hood med dragkedja"],
    "tröja": ["tröja","tröjor","pullover","sweater","överdel","top"],
    "t-shirt": ["t-shirt","tshirt","tee","kortärmad tröja","t shirt"],
    "jacka": ["jacka","jackor","coat","puffer","bomber","anorak","windbreaker"],

    # styles
    "baggy": ["baggy","loose","oversized","wide"],
    "skinny": ["skinny","tight","slim","slimfit"],
    "träning": ["träning","tränings","sport","gym","athletic","track","training"],
    "mjukis": ["mjukis","mys","lounge","casual","soft","relax"],
    "utomhus": ["utomhus","outdoor","friluft","vandrings","hiking"],
    "luvtröja": ["luvtröja","hoodie med dragkedja","zip hoodie"],

    # colors (add variations)
    "svart": ["svart","svarta","black","dark","mörk"],
    "vit": ["vit","vita","white","offwhite","ljus"],
    "blå": ["blå","blåa","blue","navy","denim"],
    "grå": ["grå","gråa","gray","grey","ljusgrå"],
    "beige": ["beige","sand","cream","offwhite"],
    "röd": ["röd","röda","red","rosa"],
    "grön": ["grön","gröna","green","oliv","khaki"],

    # brand common mistakes / short forms can be added dynamically
    "adidas": ["adidas","adidass","addiadas"],
    "nike": ["nike","nikE","nikke"],
    "levi": ["levi","levis","levi's"],

    # gender
    "herr": ["herr","man","men","male"],
    "dam": ["dam","kvinna","women","female"],
    "unisex": ["unisex","både","alla"],

    # kids
    "barn": ["barn","kids","junior","pojke","flicka","child","baby","youth"]
}

def text_contains_term(title, term):
    """Check if title contains term or any synonyms (case-insensitive)."""
    if not term:
        return False
    t = clean_text(title).lower()
    term = term.lower()
    if term in t:
        return True
    alts = SYNONYMS.get(term, [])
    for a in alts:
        if a in t:
            return True
    # also check single-word fuzzy-ish: exact token matching for small misspellings
    # (simple approach: word boundaries)
    return False

# ---------------- SIZE HANDLING ----------------
# Jeans parsing and mapping waist->text size (heuristic)
def parse_jeans_size_from_text(text):
    """
    Find patterns like 'W32 L30', '32/30', '32x30', '32 30' and return (waist,int) or None.
    """
    if not text:
        return None
    t = text.lower()
    # common patterns: w32 l30
    m = re.search(r'w\s?(\d{2,3})\s*[l|:x\/\s]\s*(\d{2,3})', t)
    if m:
        try:
            waist = int(m.group(1))
            length = int(m.group(2))
            return {"waist": waist, "length": length}
        except:
            pass
    # patterns 32/30 or 32x30 or 32 30
    m2 = re.search(r'\b(\d{2})\s*[/x\s]\s*(\d{2})\b', t)
    if m2:
        try:
            waist = int(m2.group(1))
            length = int(m2.group(2))
            return {"waist": waist, "length": length}
        except:
            pass
    # single waist number maybe
    m3 = re.search(r'\b(\d{2})\b', t)
    if m3:
        try:
            v = int(m3.group(1))
            if 26 <= v <= 44:
                return {"waist": v, "length": None}
        except:
            pass
    return None

def waist_to_text_size(waist):
    # heuristic mapping
    try:
        w = int(waist)
    except:
        return None
    if w <= 28:
        return "XS"
    if w <= 30:
        return "S"
    if w <= 32:
        return "M"
    if w <= 34:
        return "L"
    if w <= 36:
        return "XL"
    return "XXL"

def is_kids_size_in_text(text):
    if not text:
        return False
    t = clean_text(text)
    # look for numeric child sizes like 92,98,...170
    if re.search(r'\b(9[0-9]|1[0-7][0-9])\b', t):
        # e.g. 92-179 — treat 92..170 as kids (approx)
        return True
    if any(k in t for k in ["barn","kids","junior","pojke","flicka","baby"]):
        return True
    return False

# ---------------- PRICE EXTRACTION ----------------
def parse_price_from_html(html_text):
    """
    Extract price(s) from a full HTML page/text.
    Returns smallest detected price (float) or None.
    Matches patterns like '1 299 kr', '1299:-', '1299 kr', '1299'
    but prefers those followed by 'kr' or ':-' when possible.
    """
    if not html_text:
        return None
    text = clean_text(html_text)
    # first find explicit patterns with currency markers
    pattern_currency = re.findall(r'(\d[\d\s\.,]*\d)\s*(kr|:-|sek)\b', text, flags=re.IGNORECASE)
    nums = []
    for m in pattern_currency:
        raw = m[0]
        n = re.sub(r'[^\d]', '', raw)
        if n:
            try:
                nums.append(float(n))
            except:
                pass
    if nums:
        return min(nums)
    # fallback: any standalone 3-5 digit number (but riskier)
    pattern_nums = re.findall(r'\b(\d{3,5})\b', text)
    nums2 = []
    for n in pattern_nums:
        try:
            nums2.append(float(n))
        except:
            pass
    if nums2:
        return min(nums2)
    return None

# ---------------- SCRAPERS (site-level: return list of candidate dicts) ----------------
# Each candidate dict: {site, title, url, price (may be None), snippet (opt)}
# For each listing we will later fetch the product page to parse price and full text.

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
        out.append({"site":"Vinted", "title": title, "url": url_full, "price": None})
    return out

def scrape_tradera(query, filters):
    out=[]
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
        out.append({"site":"Tradera","title":title,"url":url_full,"price":None})
    return out

def scrape_generic(query, filters, base, contains=None, site_name=None):
    out=[]
    q = quote_plus(query)
    # some bases already include ?q=..., others need different format; we try both patterns
    url1 = base if "?" in base else (base + q)
    html = http_get(url1)
    if not html:
        # try adding ?q=
        url2 = base + "?q=" + q if "?" not in base else base
        html = http_get(url2)
        if not html:
            return out
    soup = BeautifulSoup(html, "html.parser")
    anchors = soup.find_all("a", href=True)[:TOP_PER_SITE*2]
    seen=set()
    for a in anchors:
        href=a.get("href")
        if not href: continue
        if contains and contains not in href: 
            # still allow if contains is None
            continue
        url_full = urljoin(base, href) if not href.startswith("http") else href
        if url_full in seen: continue
        seen.add(url_full)
        title = clean_text(a.get_text() or a.get("title") or "")
        if not title:
            # sometimes the link has nested text in children
            title = clean_text(' '.join([c.get_text() for c in a.find_all(text=True)]))
        out.append({"site": site_name or base.split("//")[-1].split("/")[0], "title": title, "url": url_full, "price": None})
    return out

# wrapper list of scrapers to call
SCRAPERS = [
    ("Vinted", scrape_vinted),
    ("Sellpy", lambda q,f: scrape_generic(q,f,"https://www.sellpy.se/sok/","/product/","Sellpy")),
    ("Tradera", scrape_tradera),
    ("Blocket", lambda q,f: scrape_generic(q,f,"https://www.blocket.se/annonser/hela_sverige","/annons/","Blocket")),
    ("Plick", lambda q,f: scrape_generic(q,f,"https://www.plick.com/search/","/p/","Plick")),
    ("Facebook", lambda q,f: scrape_generic(q,f,"https://m.facebook.com/marketplace/search/","/marketplace/item/","Facebook Marketplace")),
]

# ---------------- PRODUCT PAGE FETCH (get full text + price + maybe size) ----------------
def enrich_product_with_page_info(prod):
    """
    Given a product dict with 'url' and 'title', fetch its page and attempt to:
    - extract price (smallest detected in page)
    - extract full text (title + description)
    - detect jeans size if present
    - return updated dict
    """
    url = prod.get("url")
    if not url:
        prod["_full_text"] = prod.get("title","")
        prod["_page_price"] = prod.get("price")
        return prod
    html = http_get(url)
    if not html:
        prod["_full_text"] = prod.get("title","")
        prod["_page_price"] = prod.get("price")
        return prod
    full_text = clean_text(' '.join([prod.get("title",""), BeautifulSoup(html, "html.parser").get_text(separator=' ')]))
    prod["_full_text"] = full_text
    price = parse_price_from_html(full_text)
    prod["_page_price"] = price if price is not None else prod.get("price")
    # detect sizes / jeans
    jeans = parse_jeans_size_from_text(full_text)
    if jeans:
        prod["_jeans"] = jeans
        if jeans.get("waist"):
            prod["_inferred_text_size"] = waist_to_text_size(jeans["waist"])
    else:
        # try to find single size tokens like "M", "L", "164"
        # find tokens
        toks = re.findall(r'\b[a-zA-Z]{1,3}\b|\b\d{2,3}\b', full_text)
        toks = [t.strip() for t in toks if t.strip()]
        # detect M/L/XS style tokens
        for t in toks:
            if t.lower() in ("xs","s","m","l","xl","xxl"):
                prod["_inferred_text_size"] = t.upper()
                break
        # detect child numeric
        if not prod.get("_inferred_text_size"):
            for t in toks:
                if re.match(r'^[89]\d$|^1[0-7]\d$', t):  # 80-179 range
                    prod["_inferred_child_size"] = int(t)
                    break
    return prod

# ---------------- SCORING ----------------
# weights
WEIGHTS = {
    "item": 30,
    "brand": 20,
    "style": 15,
    "gender": 10,
    "kids": 15,
    "color": 5,
    "size": 10,
    "price": 10  # max price bonus
}

def compute_price_score(page_price, price_max):
    """Return 0..WEIGHTS['price'] """
    if price_max is None:
        return 0
    try:
        pm = float(price_max)
    except:
        return 0
    if page_price is None:
        return 0
    try:
        p = float(page_price)
    except:
        return 0
    if p <= pm:
        return WEIGHTS["price"]  # full points when under or equal
    # over price: linear decrease; if double the price -> zero
    diff = p - pm
    ratio = diff / pm  # e.g. 0.5 = 50% over
    score = WEIGHTS["price"] * max(0.0, 1.0 - ratio)
    return max(0, round(score, 2))

def score_product(prod, filters):
    """
    Return tuple (score_float, breakdown_dict).
    If veto (e.g. kids mismatch) -> return (0, breakdown) or negative large sentinel -> filtered out.
    """
    breakdown = {}
    full_text = prod.get("_full_text") or prod.get("title","")
    t = full_text.lower()
    score = 0.0

    # item (veto if missing)
    item = (filters.get("item") or "").strip()
    if item:
        item_match = text_contains_term(t, item)
        breakdown["item_match"] = bool(item_match)
        if not item_match:
            # item is veto (must match)
            breakdown["veto"] = "item_mismatch"
            return (0.0, breakdown)
        score += WEIGHTS["item"]
    else:
        breakdown["item_match"] = False

    # brand
    brand = (filters.get("brand") or "").strip()
    if brand:
        bmatch = text_contains_term(t, brand)
        breakdown["brand_match"] = bool(bmatch)
        if bmatch:
            score += WEIGHTS["brand"]
    else:
        breakdown["brand_match"] = False

    # style
    style = (filters.get("style") or "").strip()
    if style:
        smatch = text_contains_term(t, style)
        breakdown["style_match"] = bool(smatch)
        if smatch:
            score += WEIGHTS["style"]
    else:
        breakdown["style_match"] = False

    # gender (veto logic: if user asks male/female and text explicitly opposite -> veto)
    gender = (filters.get("gender") or "").strip().lower()
    inferred_gender = None
    if any(k in t for k in SYNONYMS.get("herr",[])):
        inferred_gender = "herr"
    elif any(k in t for k in SYNONYMS.get("dam",[])):
        inferred_gender = "dam"
    # size-based inference: if inferred text size numeric suggests adult sizes -> consider adult
    if not inferred_gender and prod.get("_inferred_text_size"):
        inferred_gender = "herr/dam"
    breakdown["inferred_gender"] = inferred_gender
    if gender:
        if inferred_gender and inferred_gender not in (gender, "herr/dam"):
            breakdown["veto"] = "gender_mismatch"
            return (0.0, breakdown)
        # if no explicit, give gender points if word appears
        if text_contains_term(t, gender):
            score += WEIGHTS["gender"]
            breakdown["gender_match"] = True
        else:
            # if not explicit but inferred as adult ("herr/dam") we still give partial points
            if inferred_gender == "herr/dam":
                score += WEIGHTS["gender"] * 0.5
                breakdown["gender_match"] = "inferred_partial"
            else:
                breakdown["gender_match"] = False

    # kids (veto logic)
    kids_filter = parse_boolean_input(filters.get("kids"))
    is_kid = is_kids_size_in_text(t) or ("barn" in t or "kids" in t or "junior" in t)
    breakdown["is_kid_detected"] = bool(is_kid)
    if kids_filter is True:
        if not is_kid:
            breakdown["veto"] = "expected_kids_but_not_kid"
            return (0.0, breakdown)
        else:
            score += WEIGHTS["kids"]
    elif kids_filter is False:
        if is_kid:
            breakdown["veto"] = "expected_adult_but_is_kid"
            return (0.0, breakdown)
        else:
            score += WEIGHTS["kids"] * 0.5  # adult bonus

    # color
    color = (filters.get("color") or "").strip()
    if color:
        if text_contains_term(t, color):
            score += WEIGHTS["color"]
            breakdown["color_match"] = True
        else:
            breakdown["color_match"] = False

    # size matching
    size_filter = (filters.get("size") or "").strip()
    size_matched = False
    # check direct text size matches and inferred sizes from jeans
    if size_filter:
        # normalize accepted forms: M, L, XS, or numeric jeans like 32 30
        # check product inferred text size
        inferred_text_size = prod.get("_inferred_text_size")
        if inferred_text_size and inferred_text_size.lower() == size_filter.lower():
            size_matched = True
        else:
            # check if jeans waist maps to text
            jeans = prod.get("_jeans")
            if jeans and jeans.get("waist"):
                txt = waist_to_text_size(jeans["waist"])
                if txt and txt.lower() == size_filter.lower():
                    size_matched = True
            # check presence of exact token (user might have given numeric)
            if size_filter.isdigit() and size_filter in t:
                size_matched = True
            # check our size map tokens
            if not size_matched:
                for key, vals in SIZE_MAP.items():
                    if size_filter.upper() == key:
                        # look in text for any of vals
                        for v in vals:
                            if v in t:
                                size_matched = True
                                break
                    if size_matched:
                        break
        if size_matched:
            score += WEIGHTS["size"]
    breakdown["size_matched"] = bool(size_matched)

    # URL/title metadata bonus
    if prod.get("url"):
        score += 1
    if prod.get("title"):
        score += 1

    # Price scoring (page price prioritized)
    page_price = prod.get("_page_price")
    price_pts = compute_price_score(page_price, filters.get("price_max"))
    breakdown["price_points"] = price_pts
    score += price_pts

    # clamp
    final = max(0.0, min(100.0, round(score, 2)))
    breakdown["final"] = final
    return (final, breakdown)

# ---------------- COORDINATOR: run scrapers, enrich pages, score ----------------
def _safe_call(fn, query, filters):
    try:
        return fn(query, filters)
    except Exception:
        return []

def find_best_across_sites(query, filters, top_n=6):
    """
    1) Run site scrapers concurrently -> candidate lists
    2) Limit to TOP_PER_SITE per site (already in scrapers)
    3) Fetch product pages concurrently (enrich) for those candidates
    4) Score each candidate
    5) Return top_n sorted by (score desc, price asc)
    """
    cache_key = f"{query}|{str(filters)}"
    cached = cache_get(cache_key)
    if cached:
        return cached

    candidates = []
    # 1. run scrapers in parallel
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(SCRAPERS))) as ex:
        futures = {ex.submit(_safe_call, fn, query, filters): name for (name, fn) in SCRAPERS}
        for fut in concurrent.futures.as_completed(futures, timeout=30):
            try:
                res = fut.result()
                if res:
                    candidates.extend(res)
            except Exception:
                continue

    if not candidates:
        cache_set(cache_key, [])
        return []

    # 2. enrich product pages (fetch product page to get price + full text)
    # limit concurrency and number of product pages to fetch overall to avoid timeouts
    # We'll fetch up to TOP_PER_SITE per site already provided; cap total to a reasonable limit
    # Use a thread pool to fetch product pages
    with concurrent.futures.ThreadPoolExecutor(max_workers=PRODUCT_PAGE_WORKERS) as ex:
        futures = [ex.submit(enrich_product_with_page_info, c) for c in candidates]
        enriched = []
        for fut in concurrent.futures.as_completed(futures, timeout=60):
            try:
                enriched.append(fut.result())
            except Exception:
                continue

    # 3. score each
    scored = []
    for p in enriched:
        try:
            s, breakdown = score_product(p, filters)
        except Exception:
            s, breakdown = 0.0, {"final": 0}
        p["_rating"] = s
        p["_breakdown"] = breakdown
        # normalized price for tie-breaker
        pp = p.get("_page_price")
        try:
            p["_price_norm"] = float(pp) if pp is not None else None
        except:
            p["_price_norm"] = None
        if s > 0:
            scored.append(p)

    if not scored:
        cache_set(cache_key, [])
        return []

    # sort by rating desc, then price asc (None treated as very large)
    def sort_key(x):
        price = x.get("_price_norm")
        price_sort = float('inf') if price is None else price
        return (-x.get("_rating",0), price_sort)

    scored.sort(key=sort_key)
    top = scored[:top_n]
    cache_set(cache_key, top)
    return top

# ---------------- FLASK ROUTES ----------------
@app.route("/")
def home():
    return jsonify({"status":"ok","message":"ClothesFinder running"})

@app.route("/find_item", methods=["POST"])
def find_item_route():
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error":"No JSON payload provided"}), 400

    # parse filters from Adalo; Adalo likely sends strings for booleans so convert
    brand = data.get("brand")
    item = data.get("item")
    color = data.get("color")
    style = data.get("style")
    size = data.get("size")
    gender = data.get("gender")
    kids = parse_boolean_input(data.get("kids"))
    price_max = data.get("price_max")
    query = data.get("query") or " ".join([str(x) for x in (brand, item, color, style) if x])

    filters = {
        "brand": brand,
        "item": item,
        "color": color,
        "style": style,
        "size": size,
        "gender": gender,
        "kids": kids,
        "price_max": price_max
    }

    if not query:
        return jsonify({"error":"No query or filters provided"}), 400

    top = find_best_across_sites(query, filters, top_n=6)
    if not top:
        return jsonify({"best_match": None, "top_results": [], "message":"Inget resultat hittades"}), 404

    # build response with breakdowns for debugging
    def fmt(p):
        return {
            "site": p.get("site"),
            "title": p.get("title"),
            "url": p.get("url"),
            "price": p.get("_page_price"),
            "rating": p.get("_rating"),
            "breakdown": p.get("_breakdown")
        }

    return jsonify({
        "best_match": fmt(top[0]),
        "top_results": [fmt(x) for x in top],
        "count": len(top)
    })

if __name__ == "__main__":
    # Render typically expects port from env, but default to 10000 here
    app.run(host="0.0.0.0", port=10000)
