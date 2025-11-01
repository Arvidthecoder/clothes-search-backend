# main.py
# ClothesFinder — All-in-one Flask API
# Ready for Render / Adalo. Uses html.parser (no lxml).

from flask import Flask, request, jsonify
import requests, re, time, threading
from bs4 import BeautifulSoup
from difflib import SequenceMatcher
import concurrent.futures

app = Flask(__name__)

# ----------------------------
# CONFIG
# ----------------------------
USER_AGENT = "Mozilla/5.0 (compatible; ClothesFinder/3.0; +https://example.com)"
REQUEST_TIMEOUT = 8
MAX_WORKERS = 6
CACHE_TTL = 300  # seconds

# ----------------------------
# SIMPLE IN-MEMORY CACHE
# ----------------------------
_cache = {}
_cache_lock = threading.Lock()

def cache_get(key):
    with _cache_lock:
        v = _cache.get(key)
        if not v:
            return None
        ts, val = v
        if time.time() - ts > CACHE_TTL:
            del _cache[key]
            return None
        return val

def cache_set(key, val):
    with _cache_lock:
        _cache[key] = (time.time(), val)

# ----------------------------
# SYNONYMS & SPELL FIXES
# ----------------------------
# Expand this gradually. Keep words lowercased.
SYNONYMS = {
    # items
    "hoodie": ["huvtröja", "sweatshirt", "hood"],
    "tshirt": ["t-shirt", "tee", "t shirt", "tshirt", "tee-shirt"],
    "jacka": ["coat", "jacket", "ytterplagg", "kappa"],
    "jeans": ["denim", "byxa", "jeansbyxa", "jeans"],
    "byxa": ["pants", "trousers", "chinos", "byxor"],
    "klänning": ["dress"],
    "skor": ["sneakers", "trainers", "gympaskor"],
    # styles
    "baggy": ["oversized", "loose", "relaxed"],
    "skinny": ["tight", "slim", "slimfit"],
    "vintage": ["retro", "oldschool"],
    # colors (basic)
    "svart": ["black", "dark"],
    "vit": ["white", "offwhite"],
    "blå": ["blue", "navy"],
    "grå": ["gray", "grey"],
    "röd": ["red"],
    # gender
    "herr": ["man", "men", "male"],
    "dam": ["kvinna", "woman", "women", "female"],
    "barn": ["kids", "junior", "child", "baby"],
}

# Simple common misspellings mapping to canonical
SPELL_FIX = {
    "nikke": "nike",
    "adiddas": "adidas",
    "addidas": "adidas",
    "pumma": "puma",
    "reebock": "reebok"
}

# Words that indicate child in title
CHILD_WORDS = {"barn", "kids", "junior", "baby", "child"}

# ----------------------------
# UTILITIES
# ----------------------------
HEADERS = {"User-Agent": USER_AGENT}

def http_get(url, params=None):
    try:
        r = requests.get(url, params=params, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        return r.text
    except Exception:
        return None

def clean_text(text):
    if not text:
        return ""
    return re.sub(r'\s+', ' ', text).strip()

def parse_price_from_text(text):
    if not text:
        return None
    t = text.replace('\u00A0', ' ')
    m = re.search(r'(\d+[ \d\.,]*\d)', t)
    if not m:
        return None
    num = m.group(1).replace(' ', '').replace('.', '').replace(',', '')
    try:
        return int(num)
    except:
        try:
            return float(num)
        except:
            return None

def similar(a, b):
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()

def parse_boolean_input(v):
    # Accept booleans or text 'ja'/'nej' etc.
    if v is None:
        return None
    if isinstance(v, bool):
        return v
    s = str(v).strip().lower()
    if s in ("ja", "true", "yes", "1"):
        return True
    if s in ("nej", "false", "no", "0"):
        return False
    return None

def expand_terms(text):
    # Return a set of canonical words based on synonyms and spellfix
    out = set()
    if not text:
        return out
    words = re.findall(r"[a-zA-ZåäöÅÄÖ0-9\-]+", text.lower())
    for w in words:
        if w in SPELL_FIX:
            out.add(SPELL_FIX[w])
        out.add(w)
        # add synonyms where w is a key or in values
        if w in SYNONYMS:
            out.update(SYNONYMS[w])
        else:
            for k, vals in SYNONYMS.items():
                if w in vals:
                    out.add(k)
                    out.update(vals)
    return out

# ----------------------------
# SIZE MATCHING / NORMALIZATION
# ----------------------------
def normalize_size_input(size_input):
    """
    Convert input into a normalized form.
    Returns dict with possible types: {'kind':'jeans','waist':32,'length':30} or {'kind':'numeric_child','value':164} or {'kind':'text','value':'m'}
    """
    if not size_input:
        return None
    s = str(size_input).strip().lower()
    # Remove stray spaces
    s = re.sub(r'\s+', ' ', s)
    # Jeans patterns: W32 L30, 32/30, 32x30, 32 30
    m = re.search(r'w?(\d{2,3})\D+(\d{2,3})', s)
    if m:
        waist = int(m.group(1))
        length = int(m.group(2))
        return {"kind":"jeans", "waist":waist, "length":length}
    # single numeric value might be child size or waist (e.g. 164 or 32)
    m2 = re.match(r'^(\d{2,3})$', s)
    if m2:
        val = int(m2.group(1))
        # if plausible child size range 50-176 -> child
        if 50 <= val <= 176:
            return {"kind":"child_numeric","value":val}
        # else treat as waist (jeans)
        return {"kind":"jeans_single","waist":val}
    # text sizes like XS, S, M, L, XL
    if re.match(r'^(xs|s|m|l|xl|xxl|xxxl)$', s):
        return {"kind":"text","value":s.upper()}
    # other patterns like '30x32' handled earlier; fallback to raw
    return {"kind":"text","value":s}

def jeans_waist_to_textsize(waist):
    # heuristic mapping waist->text sizes (very rough, can be tuned)
    # waist in inches
    if waist <= 28:
        return "S"
    if waist == 30:
        return "M"
    if waist == 32:
        return "L"
    if waist == 34:
        return "XL"
    if waist >= 36:
        return "XXL"
    return None

def match_size_filter(size_filter_norm, title, item, kids_flag):
    """
    Return True if title seems to match size filter.
    Logic:
     - If jeans kind, check presence of waist or corresponding text-size in title.
     - If child numeric, check numeric present in title.
     - If text size, check S/M/L exists.
    """
    if not size_filter_norm:
        return False
    t = title.lower()
    kind = size_filter_norm.get("kind")
    if kind == "jeans":
        waist = size_filter_norm.get("waist")
        length = size_filter_norm.get("length")
        # match patterns like "w32 l30", "32/30", "32x30", "32 30"
        if re.search(r'\b{}[^\d]{}|\b{}[^\d]'.format(waist, length, "{}".format(waist)), t.replace(' ',' ')):
            return True
        # also accept just waist present
        if str(waist) in t:
            return True
        # map waist to text size
        txt = jeans_waist_to_textsize(waist)
        if txt and re.search(r'\b{}\b'.format(txt.lower()), t):
            return True
        return False
    if kind == "jeans_single":
        waist = size_filter_norm.get("waist")
        if str(waist) in t:
            return True
        txt = jeans_waist_to_textsize(waist)
        if txt and re.search(r'\b{}\b'.format(txt.lower()), t):
            return True
        return False
    if kind == "child_numeric":
        val = size_filter_norm.get("value")
        # check plain number present
        if str(val) in t:
            return True
        return False
    if kind == "text":
        v = size_filter_norm.get("value").lower()
        # check direct or common mappings (e.g. 34 -> XL mapping)
        if re.search(r'\b{}\b'.format(re.escape(v)), t):
            return True
        # map common synonyms: 'm' => 'medium'
        return False
    return False

# ----------------------------
# SCRAPERS (defensive)
# Each returns list of dicts: {site, title, price, url, description (opt), used}
# ----------------------------
def scrape_vinted(query):
    results=[]
    url = f"https://www.vinted.se/catalog?search_text={requests.utils.quote(query)}"
    html = cache_or_fetch(url)
    if not html:
        return results
    soup = BeautifulSoup(html, "html.parser")
    anchors = soup.select("a.catalog-item__link")
    if not anchors:
        anchors = [a for a in soup.find_all("a", href=True) if "/item/" in a.get("href","")]
    seen=set()
    for a in anchors[:20]:
        href=a.get("href")
        link = ("https://www.vinted.se"+href) if href and href.startswith("/") else href
        if not link or link in seen: continue
        seen.add(link)
        title = clean_text(a.get_text() or a.get("title") or "")
        price_tag = a.select_one(".catalog-item__price")
        price = parse_price_from_text(price_tag.get_text()) if price_tag else None
        results.append({"site":"Vinted","title":title,"price":price,"url":link,"description":"","used":True})
    return results

def scrape_tradera(query):
    results=[]
    url = f"https://www.tradera.com/search?q={requests.utils.quote(query)}"
    html = cache_or_fetch(url)
    if not html:
        return results
    soup = BeautifulSoup(html, "html.parser")
    anchors = soup.select("a.listing-card__link")
    if not anchors:
        anchors = [a for a in soup.find_all("a", href=True) if "/item/" in a.get("href","")]
    seen=set()
    for a in anchors[:20]:
        href=a.get("href")
        link = href if href and href.startswith("http") else "https://www.tradera.com"+href
        if not link or link in seen: continue
        seen.add(link)
        title = clean_text(a.get_text() or a.get("title") or "")
        price_tag = a.select_one(".listing-card__price")
        price = parse_price_from_text(price_tag.get_text()) if price_tag else None
        results.append({"site":"Tradera","title":title,"price":price,"url":link,"description":"","used":True})
    return results

def scrape_blocket(query):
    results=[]
    url = f"https://www.blocket.se/annonser/hela_sverige?q={requests.utils.quote(query)}"
    html = cache_or_fetch(url)
    if not html:
        return results
    soup = BeautifulSoup(html, "html.parser")
    anchors = [a for a in soup.find_all("a", href=True) if "/annons/" in a.get("href","")]
    seen=set()
    for a in anchors[:20]:
        href=a.get("href")
        link = href if href and href.startswith("http") else "https://www.blocket.se"+href
        if not link or link in seen: continue
        seen.add(link)
        title = clean_text(a.get_text() or a.get("title") or "")
        parent_text = a.parent.get_text() if a.parent else ""
        price = parse_price_from_text(parent_text)
        results.append({"site":"Blocket","title":title,"price":price,"url":link,"description":"","used":True})
    return results

def scrape_plick(query):
    results=[]
    url = f"https://plick.se/search?q={requests.utils.quote(query)}"
    html = cache_or_fetch(url)
    if not html:
        return results
    soup = BeautifulSoup(html, "html.parser")
    seen=set()
    for a in soup.find_all("a", href=True)[:30]:
        if "/p/" not in a.get("href",""): continue
        href=a.get("href")
        link = ("https://plick.se"+href) if href.startswith("/") else href
        if link in seen: continue
        seen.add(link)
        title = clean_text(a.get_text() or "")
        price_tag = a.find_next(string=lambda x: "kr" in str(x))
        price = parse_price_from_text(price_tag) if price_tag else None
        results.append({"site":"Plick","title":title,"price":price,"url":link,"description":"","used":True})
    return results

def scrape_sellpy(query):
    results=[]
    url = f"https://www.sellpy.se/search?q={requests.utils.quote(query)}"
    html = cache_or_fetch(url)
    if not html:
        return results
    soup = BeautifulSoup(html, "html.parser")
    seen=set()
    for a in soup.find_all("a", href=True)[:30]:
        if "/product/" not in a.get("href",""): continue
        href=a.get("href")
        link = ("https://www.sellpy.se"+href) if href.startswith("/") else href
        if link in seen: continue
        seen.add(link)
        title = clean_text(a.get_text() or "")
        price_tag = a.find_next(string=lambda x: "kr" in str(x))
        price = parse_price_from_text(price_tag) if price_tag else None
        results.append({"site":"Sellpy","title":title,"price":price,"url":link,"description":"","used":True})
    return results

def scrape_facebook(query):
    results=[]
    url = f"https://m.facebook.com/marketplace/search/?query={requests.utils.quote(query)}"
    html = cache_or_fetch(url)
    if not html:
        return results
    soup = BeautifulSoup(html, "html.parser")
    seen=set()
    for a in soup.find_all("a", href=True)[:30]:
        if "/marketplace/item/" not in a.get("href",""): continue
        href=a.get("href")
        link = ("https://m.facebook.com"+href) if href.startswith("/") else href
        if link in seen: continue
        seen.add(link)
        title = clean_text(a.get_text() or "")
        results.append({"site":"Facebook Marketplace","title":title,"price":None,"url":link,"description":"","used":True})
    return results

def scrape_generic_store(query, base_search_url, path_contains, site_name, used=False):
    results=[]
    url = base_search_url + requests.utils.quote(query)
    html = cache_or_fetch(url)
    if not html:
        return results
    soup = BeautifulSoup(html, "html.parser")
    seen=set()
    for a in soup.find_all("a", href=True)[:60]:
        href=a.get("href")
        if path_contains and path_contains not in href:
            continue
        link = href if href.startswith("http") else base_search_url.split('?')[0].rstrip('/')+href
        if link in seen: continue
        seen.add(link)
        title = clean_text(a.get_text() or a.get("title") or "")
        price = None
        for p in (a.parent, a.parent.parent if a.parent else None):
            if p:
                price = parse_price_from_text(p.get_text())
                if price:
                    break
        results.append({"site":site_name,"title":title,"price":price,"url":link,"description":"","used":used})
    return results

def scrape_zalando(query):
    return scrape_generic_store(query, "https://www.zalando.se/catalog/?q=", "/p/", "Zalando", False)

def scrape_amazon(query):
    return scrape_generic_store(query, "https://www.amazon.se/s?k=", "/dp/", "Amazon", False)

# ----------------------------
# CACHE HELPER for scrapers
# ----------------------------
def cache_or_fetch(url):
    cached = cache_get(url)
    if cached:
        return cached
    html = http_get(url)
    if html:
        cache_set(url, html)
    return html

# ----------------------------
# SCORING / RANKING
# ----------------------------
# Veto rules: item must match; kids if True must be explicit; if kids False then explicit child items are excluded.
# Gender is conditional veto (if user asks herr/dam and title clearly indicates opposite -> veto)
# Weights (sum to ~100 but we apply carefully)
WEIGHTS = {
    "item": 30,
    "brand": 15,
    "color": 10,
    "size": 10,
    "gender": 10,
    "kids": 15,
    "style": 5,
    "price": 5
}

def title_contains_any(title, terms):
    if not terms:
        return False
    t = title.lower()
    for term in terms:
        if term and term.lower() in t:
            return True
    return False

def build_search_term_set(filter_value):
    # Accept a single value or list
    if not filter_value:
        return set()
    if isinstance(filter_value, (list, tuple, set)):
        vals = filter_value
    else:
        vals = [str(filter_value)]
    out = set()
    for v in vals:
        out.update(expand_terms(str(v)))
    return out

def is_item_match(title, item_filter):
    # strict match: check synonyms and fuzzy
    if not item_filter:
        return False
    item_terms = build_search_term_set(item_filter)
    # check exact tokens
    for it in item_terms:
        if it and it in title.lower():
            return True
    # fuzzy fallback
    for it in item_terms:
        if similar(it, title) > 0.9:
            return True
    return False

def score_product(prod, filters):
    title = (prod.get("title") or "").lower()
    # VETO: item must match
    if not is_item_match(title, filters.get("item")):
        return 0

    # parse kids flag
    kids_flag = filters.get("kids")
    # VETO: if searching kids then title must contain child words
    if kids_flag:
        if not any(w in title for w in CHILD_WORDS):
            return 0
    else:
        # searching adults, exclude explicit child items
        if any(w in title for w in CHILD_WORDS):
            return 0

    # VETO: gender if provided - only veto if title explicitly indicates opposite
    gender = (filters.get("gender") or "").lower()
    if gender:
        if gender == "herr":
            if any(x in title for x in ["dam", "kvinna", "women", "female", "lady"]):
                return 0
        elif gender == "dam":
            if any(x in title for x in ["herr", "man", "men", "male"]):
                return 0

    score = 0.0

    # item passed -> base score
    score += WEIGHTS["item"]

    # brand
    brand_terms = build_search_term_set(filters.get("brand"))
    if brand_terms and title_contains_any(title, brand_terms):
        score += WEIGHTS["brand"]

    # color
    color_terms = build_search_term_set(filters.get("color"))
    if color_terms and title_contains_any(title, color_terms):
        score += WEIGHTS["color"]

    # style
    style_terms = build_search_term_set(filters.get("style"))
    if style_terms and title_contains_any(title, style_terms):
        score += WEIGHTS["style"]

    # size
    size_norm = normalize_size_input(filters.get("size"))
    if filters.get("size") and match_size_filter(size_norm, title, filters.get("item"), kids_flag):
        score += WEIGHTS["size"]

    # gender bonus (if matched)
    if gender and any(x in title for x in [gender, ("herr" if gender=="herr" else ""), ("dam" if gender=="dam" else "")]):
        score += WEIGHTS["gender"]

    # kids bonus (if searching kids and matched, already ensured)
    if kids_flag:
        score += WEIGHTS["kids"]

    # price: small bonus
    try:
        price = prod.get("price")
        if price is not None and filters.get("price_max"):
            price_val = float(price)
            pm = float(filters.get("price_max"))
            if price_val <= pm:
                # reward proportionally up to weight
                score += WEIGHTS["price"]
            else:
                # small penalty if over budget
                diff = price_val - pm
                score -= min(WEIGHTS["price"], diff / max(1.0, pm) * WEIGHTS["price"])
    except:
        pass

    # ensure between 0 and 100
    final = max(0.0, min(100.0, round(score, 2)))
    return final

# ----------------------------
# FIND BEST ACROSS SITES
# ----------------------------
SCRAPERS = [
    scrape_vinted,
    scrape_tradera,
    scrape_blocket,
    scrape_plick,
    scrape_sellpy,
    scrape_facebook,
    scrape_zalando,
    scrape_amazon
]

def _call_scraper_safe(fn, query, filters):
    try:
        return fn(query)
    except Exception:
        return []

def find_best_across_sites(query, filters, top_n=5):
    cache_key = f"{query}|{filters}"
    cached = cache_get(cache_key)
    if cached:
        return cached

    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = [ex.submit(_call_scraper_safe, fn, query, filters) for fn in SCRAPERS]
        for fut in concurrent.futures.as_completed(futures, timeout=40):
            try:
                res = fut.result()
                if res:
                    results.extend(res)
            except Exception:
                continue

    # score each
    scored = []
    for prod in results:
        try:
            r = score_product(prod, filters)
        except Exception:
            r = 0
        prod['_rating'] = r
        # ensure price numeric if possible
        try:
            prod['_price_norm'] = float(prod.get("price")) if prod.get("price") is not None else None
        except:
            prod['_price_norm'] = None
        if prod['_rating'] > 0:
            scored.append(prod)

    if not scored:
        cache_set(cache_key, [])
        return []

    # sort by rating desc, then by price asc (None prices go last)
    scored.sort(key=lambda x: (-x['_rating'], float('inf') if x['_price_norm'] is None else x['_price_norm']))
    top = scored[:top_n]
    cache_set(cache_key, top)
    return top

# ----------------------------
# FLASK ENDPOINTS
# ----------------------------
@app.route("/")
def home():
    return jsonify({"status":"ok","message":"ClothesFinder running"})

@app.route("/find_item", methods=["POST"])
def find_item_route():
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error":"No JSON payload provided"}), 400

    # Build query if not provided; primarily for scrapers
    brand = data.get("brand")
    item = data.get("item")
    color = data.get("color")
    style = data.get("style")
    size = data.get("size")
    gender = data.get("gender")
    kids = parse_boolean_input(data.get("kids"))
    price_max = data.get("price_max")

    # At the Adalo side you will supply dropdowns/text for these variables
    query = data.get("query")
    if not query:
        # build simple query from main filters
        parts = []
        for v in (brand, item, color, style):
            if v:
                parts.append(str(v))
        query = " ".join(parts)

    filters = {
        "brand": brand,
        "item": item,
        "color": color,
        "size": size,
        "gender": gender,
        "kids": kids,
        "price_max": price_max,
        "style": style
    }

    if not item:
        return jsonify({"error":"`item` (plaggtyp) is required"}), 400

    top_results = find_best_across_sites(query, filters, top_n=10)
    if not top_results:
        return jsonify({"best_match": None, "top_results": [], "message":"No matching products found"}), 404

    best = top_results[0]
    # Format output
    def outprod(p):
        return {
            "site": p.get("site"),
            "title": p.get("title"),
            "price": p.get("price"),
            "url": p.get("url"),
            "rating": p.get("_rating")
        }
    return jsonify({
        "best_match": outprod(best),
        "top_results": [outprod(p) for p in top_results],
        "count": len(top_results)
    })

# ----------------------------
# RUN
# ----------------------------
if __name__ == "__main__":
    # Run on port 10000 so Render detects it
    app.run(host="0.0.0.0", port=10000)
