# main.py
# ClothesFinder — All-in-One Flask API
# Inkluderar Vinted, Tradera, Blocket, Zalando, Amazon, Plick, Sellpy, Facebook Marketplace

from flask import Flask, request, jsonify
import requests, re, time
from bs4 import BeautifulSoup
import concurrent.futures, threading

app = Flask(__name__)

# --- CONFIG ---
USER_AGENT = "Mozilla/5.0 (compatible; ClothesFinder/2.0; +https://example.com)"
REQUEST_TIMEOUT = 8
CACHE_TTL = 300
MAX_WORKERS = 8

# --- CACHE ---
_cache = {}
_cache_lock = threading.Lock()

def cached_get(url):
    with _cache_lock:
        item = _cache.get(url)
        if item and (time.time() - item[0] < CACHE_TTL):
            return item[1]
    headers = {"User-Agent": USER_AGENT}
    try:
        r = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
        if r.status_code == 200:
            with _cache_lock:
                _cache[url] = (time.time(), r.text)
            return r.text
    except:
        return None
    return None

# --- HELPERS ---
def clean_text(t):
    if not t: return ""
    return re.sub(r"\s+", " ", t).strip()

def extract_price(text):
    if not text: return None
    text = text.replace("\u00A0", " ")
    m = re.search(r"(\d+[ \d\.]*\d)", text)
    if not m: return None
    try:
        return int(m.group(1).replace(" ", "").replace(".", "").replace(",", ""))
    except:
        return None

# --- SCORING ---
def score_product(prod, filters):
    title = (prod.get("title") or "").lower()
    score = 0

    brand = (filters.get("brand") or "").lower()
    item = (filters.get("item") or "").lower()
    color = (filters.get("color") or "").lower()
    size = (filters.get("size") or "").lower()
    gender = (filters.get("gender") or "").lower()
    kids = filters.get("kids")
    used_pref = filters.get("used")
    price_max = filters.get("price_max")

    if brand and brand in title: score += 40
    if item and item in title: score += 25
    if color and color in title: score += 10
    if size and size in title: score += 10
    if gender:
        if gender in ["herr","dam"]:
            if gender in title: score += 8
    if kids is not None:
        if kids and ("barn" in title or "kid" in title): score += 8
        if not kids and ("barn" in title or "kid" in title): score -= 10

    p = prod.get("price")
    if p and price_max:
        try:
            pv = float(p)
            if pv <= float(price_max):
                score += 20 + max(0, int((float(price_max) - pv)/10))
        except:
            pass

    if used_pref is not None:
        if used_pref == prod.get("used"):
            score += 10
        else:
            score -= 5

    if prod.get("url"): score += 2
    if prod.get("title"): score += 1
    return score

# --- SCRAPERS ---
def scrape_vinted(query):
    results = []
    url = f"https://www.vinted.se/catalog?search_text={query.replace(' ','+')}"
    html = cached_get(url)
    if not html: return results
    soup = BeautifulSoup(html, "html.parser")
    anchors = soup.select("a.catalog-item__link")
    if not anchors:
        anchors = [a for a in soup.find_all("a", href=True) if "/item/" in a.get("href","")]
    for a in anchors[:12]:
        href = a.get("href")
        link = "https://www.vinted.se" + href if href.startswith("/") else href
        title = clean_text(a.get_text() or "")
        price_tag = a.select_one(".catalog-item__price") if a else None
        price = extract_price(price_tag.get_text()) if price_tag else None
        results.append({"site":"Vinted","title":title,"price":price,"url":link,"used":True})
    return results

def scrape_tradera(query):
    results = []
    url = f"https://www.tradera.com/search?q={query.replace(' ','+')}"
    html = cached_get(url)
    if not html: return results
    soup = BeautifulSoup(html, "html.parser")
    anchors = soup.select("a.listing-card__link")
    if not anchors:
        anchors = [a for a in soup.find_all("a", href=True) if "/item/" in a.get("href","")]
    for a in anchors[:12]:
        href = a.get("href")
        link = href if href.startswith("http") else "https://www.tradera.com" + href
        title = clean_text(a.get_text() or "")
        price_tag = a.select_one(".listing-card__price")
        price = extract_price(price_tag.get_text()) if price_tag else None
        results.append({"site":"Tradera","title":title,"price":price,"url":link,"used":True})
    return results

def scrape_blocket(query):
    results = []
    url = f"https://www.blocket.se/annonser/hela_sverige?q={query.replace(' ','+')}"
    html = cached_get(url)
    if not html: return results
    soup = BeautifulSoup(html, "html.parser")
    anchors = [a for a in soup.find_all("a", href=True) if "/annons/" in a.get("href","")]
    for a in anchors[:12]:
        href = a.get("href")
        link = href if href.startswith("http") else "https://www.blocket.se" + href
        title = clean_text(a.get_text() or "")
        parent_text = a.parent.get_text() if a.parent else ""
        price = extract_price(parent_text)
        results.append({"site":"Blocket","title":title,"price":price,"url":link,"used":True})
    return results

def scrape_generic(query, base_url, path_contains, site_name, used=False):
    results=[]
    url = base_url + query.replace(" ","+")
    html = cached_get(url)
    if not html: return results
    soup = BeautifulSoup(html, "html.parser")
    for a in soup.find_all("a", href=True)[:30]:
        href = a.get("href")
        if path_contains and path_contains not in href: continue
        link = href if href.startswith("http") else base_url.rstrip('/')+href
        title = clean_text(a.get_text() or "")
        price = None
        for p in (a.parent, a.parent.parent if a.parent else None):
            if p:
                price = extract_price(p.get_text())
                if price: break
        results.append({"site":site_name,"title":title,"price":price,"url":link,"used":used})
    return results[:10]

def scrape_plick(query):
    return scrape_generic(query,"https://plick.se/search?q=","/p/","Plick",True)

def scrape_sellpy(query):
    return scrape_generic(query,"https://www.sellpy.se/search?q=","/product/","Sellpy",True)

def scrape_facebook(query):
    results=[]
    url=f"https://m.facebook.com/marketplace/search/?query={query.replace(' ','+')}"
    html = cached_get(url)
    if not html: return results
    soup = BeautifulSoup(html, "html.parser")
    for a in soup.find_all("a", href=True):
        if "/marketplace/item/" not in a["href"]: continue
        title = clean_text(a.get_text() or "")
        results.append({"site":"Facebook Marketplace","title":title,"price":None,"url":"https://m.facebook.com"+a["href"],"used":True})
    return results[:10]

# --- ALL SCRAPERS LIST ---
SCRAPERS = [
    scrape_vinted,
    scrape_tradera,
    scrape_blocket,
    lambda q: scrape_generic(q,"https://www.zalando.se/catalog/?q=","/p/","Zalando",False),
    lambda q: scrape_generic(q,"https://www.amazon.se/s?k=","/dp/","Amazon",False),
    scrape_plick,
    scrape_sellpy,
    scrape_facebook
]

# --- HELPER: RUN ALL SCRAPERS SAFELY ---
def run_all_scrapers(query):
    results=[]
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures=[ex.submit(scraper,query) for scraper in SCRAPERS]
        for fut in concurrent.futures.as_completed(futures):
            try:
                res=fut.result()
                if res: results+=res
            except: continue
    return results

# --- FLASK ENDPOINTS ---
@app.route("/")
def home(): return jsonify({"status":"ok","message":"ClothesFinder AI running"})

@app.route("/find_item", methods=["POST"])
def find_item():
    data = request.get_json(silent=True)
    if not data: return jsonify({"error":"Missing JSON"}),400

    query = data.get("query")
    if not query:
        query=" ".join(filter(None,[data.get("brand"),data.get("item"),data.get("color"),data.get("gender")]))

    filters={
        "brand":data.get("brand"),
        "item":data.get("item"),
        "size":data.get("size"),
        "color":data.get("color"),
        "gender":data.get("gender"),
        "kids":data.get("kids"),
        "price_max":data.get("price_max"),
        "used":data.get("used")
    }

    results=run_all_scrapers(query)
    if not results: return jsonify({"message":"Inga resultat hittades"}),404

    for r in results: r["_score"]=score_product(r,filters)
    results.sort(key=lambda x:x["_score"],reverse=True)
    best=results[0]

    return jsonify({"best_match":best,"top_results":results[:10],"count":len(results)})

if __name__=="__main__":
    app.run(host="0.0.0.0", port=10000)
