from flask import Flask, request, jsonify
import requests, re, time, threading, concurrent.futures, difflib
from bs4 import BeautifulSoup

app = Flask(__name__)

USER_AGENT = "Mozilla/5.0 (ClothesFinder/3.0)"
TIMEOUT = 8
CACHE_TTL = 300
MAX_WORKERS = 8

_cache = {}
_lock = threading.Lock()

def cache_get(k):
    with _lock:
        i = _cache.get(k)
        if not i: return None
        t,v = i
        if time.time() - t > CACHE_TTL:
            del _cache[k]; return None
        return v

def cache_set(k,v):
    with _lock:
        _cache[k] = (time.time(), v)

def clean(t): return re.sub(r"\s+", " ", t or "").strip()

def parse_price(txt):
    if not txt: return None
    m = re.search(r"(\d+[ \d\.,]*)", txt.replace("\u00A0"," "))
    if not m: return None
    num = m.group(1).replace(" ","").replace(",",".")
    try: return float(num)
    except: return None

def http_get(url):
    try:
        r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
        r.raise_for_status()
        return r.text
    except: return None

# ---------------- SYNONYMER ----------------
SYNONYMS = {
    "jeans": ["denim", "byxor"],
    "byxor": ["pants", "trousers", "jeans"],
    "tröja": ["sweater", "hoodie", "pullover", "top"],
    "jacka": ["coat", "jacket", "parka"],
    "tshirt": ["t-shirt", "tee"],
    "hoodie": ["hood", "sweatshirt"],
    "svart": ["black", "mörk", "dark"],
    "vit": ["white", "ljus", "light"],
    "blå": ["blue", "navy"],
    "grön": ["green", "olive", "khaki"],
    "herr": ["man", "men", "male"],
    "dam": ["kvinna", "women", "lady", "female"],
    "barn": ["kids", "junior", "child", "boy", "girl"],
    "baggy": ["loose", "relaxed", "oversized"],
    "skinny": ["tight", "slim"],
    "cargo": ["utility", "pockets"]
}

def match_syn(word, text):
    if not word: return False
    t = text.lower(); w = word.lower()
    if w in t: return True
    for s in SYNONYMS.get(w, []):
        if s in t: return True
    words = re.findall(r"\w+", t)
    return bool(difflib.get_close_matches(w, words, cutoff=0.8))

def infer_gender(title,size):
    t,s = title.lower(), (size or "").lower()
    if any(k in t for k in ["herr","men","man","male"]): return "herr"
    if any(k in t for k in ["dam","women","lady","female"]): return "dam"
    if s in ["l","xl","xxl","m","s"]: return "herr/dam"
    if any(x in s for x in ["34","36","38","40"]): return "dam"
    return None

def infer_kids(title,size):
    t,s = title.lower(), (size or "").lower()
    if any(k in t for k in ["barn","kids","junior","pojk","flick","baby"]): return True
    if re.match(r"^1\d{2}$", s): return True
    if any(x in s for x in ["92","98","104","116","128","140","152","164"]): return True
    return False

# ---------------- SCORING ----------------
def score(prod,f):
    score=0
    title=clean(prod.get("title","")).lower()
    price=prod.get("price") or 0
    size=prod.get("size") or ""
    brand=(f.get("brand") or "").lower()
    item=(f.get("item") or "").lower()
    color=(f.get("color") or "").lower()
    style=(f.get("style") or "").lower()
    gender_f=(f.get("gender") or "").lower()
    kids_f=f.get("kids")
    price_max=f.get("price_max")

    g= infer_gender(title,size)
    k= infer_kids(title,size)

    if kids_f is True and not k: score -= 50
    if kids_f is False and k: score -= 50

    if match_syn(item,title): score += 30
    if match_syn(brand,title): score += 20
    if match_syn(color,title): score += 10
    if match_syn(style,title): score += 15

    if gender_f:
        if g==gender_f or g=="herr/dam": score += 15
        else: score -= 10

    if price and price_max:
        try:
            if price<=price_max: score += 10 + max(0,int((price_max-price)/20))
            else: score -= 15
        except: pass

    return max(0,min(100,score))

# ---------------- SCRAPERS ----------------
def search_vinted(q,f):
    res=[]; html=http_get("https://www.vinted.se/catalog?search_text="+requests.utils.quote(q))
    if not html: return res
    s=BeautifulSoup(html,"html.parser")
    for a in s.select("a.catalog-item__link")[:10]:
        href=a.get("href"); link="https://www.vinted.se"+href if href.startswith("/") else href
        res.append({"site":"Vinted","title":clean(a.text),"price":parse_price(a.text),"url":link,"used":True})
    return res

def search_sellpy(q,f):
    res=[]; html=http_get("https://www.sellpy.se/search?q="+requests.utils.quote(q))
    if not html: return res
    s=BeautifulSoup(html,"html.parser")
    for a in s.find_all("a",href=True)[:10]:
        if not "/produkt/" in a["href"]: continue
        link="https://www.sellpy.se"+a["href"]
        res.append({"site":"Sellpy","title":clean(a.text),"price":parse_price(a.text),"url":link,"used":True})
    return res

def search_tradera(q,f):
    res=[]; html=http_get("https://www.tradera.com/search?q="+requests.utils.quote(q))
    if not html: return res
    s=BeautifulSoup(html,"html.parser")
    for a in s.select("a.listing-card__link")[:10]:
        href=a.get("href"); link="https://www.tradera.com"+href if href.startswith("/") else href
        res.append({"site":"Tradera","title":clean(a.text),"price":parse_price(a.text),"url":link,"used":True})
    return res

def search_blocket(q,f):
    res=[]; html=http_get("https://www.blocket.se/annonser/hela_sverige?q="+requests.utils.quote(q))
    if not html: return res
    s=BeautifulSoup(html,"html.parser")
    for a in s.find_all("a",href=True)[:10]:
        if "/annons/" not in a["href"]: continue
        link="https://www.blocket.se"+a["href"]
        res.append({"site":"Blocket","title":clean(a.text),"price":parse_price(a.text),"url":link,"used":True})
    return res

def search_plick(q,f):
    res=[]; html=http_get("https://plick.se/sok?q="+requests.utils.quote(q))
    if not html: return res
    s=BeautifulSoup(html,"html.parser")
    for a in s.find_all("a",href=True)[:10]:
        if not "/p/" in a["href"]: continue
        link="https://plick.se"+a["href"]
        res.append({"site":"Plick","title":clean(a.text),"price":parse_price(a.text),"url":link,"used":True})
    return res

def search_fbmarket(q,f):
    res=[]; html=http_get("https://www.facebook.com/marketplace/search/?query="+requests.utils.quote(q))
    if not html: return res
    s=BeautifulSoup(html,"html.parser")
    for a in s.find_all("a",href=True)[:10]:
        if "/marketplace/item/" not in a["href"]: continue
        link="https://www.facebook.com"+a["href"]
        res.append({"site":"Facebook","title":clean(a.text),"price":parse_price(a.text),"url":link,"used":True})
    return res

SITES=[
    ("Vinted", search_vinted),
    ("Sellpy", search_sellpy),
    ("Tradera", search_tradera),
    ("Blocket", search_blocket),
    ("Plick", search_plick),
    ("Facebook", search_fbmarket)
]

# ---------------- COORDINATOR ----------------
def find_best(q,f):
    key=f"{q}|{f}"
    c=cache_get(key)
    if c: return c

    site_results=[]
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futs=[ex.submit(fn,q,f) for _,fn in SITES]
        for site,(sn,fn) in zip(futs,SITES):
            try:
                res=site.result()
                if not res: continue
                scored=[(p,score(p,f)) for p in res]
                best=max(scored,key=lambda x:x[1])[0]
                best["_score"]=max(scored,key=lambda x:x[1])[1]
                site_results.append(best)
            except: pass

    if not site_results: return None
    best=max(site_results,key=lambda x:(x["_score"],-x.get("price",99999)))
    cache_set(key,best)
    return best

# ---------------- ROUTES ----------------
@app.route("/")
def home():
    return jsonify({"message":"ClothesFinder 3.0 API running"})

@app.route("/find_item",methods=["POST"])
def find_item():
    data=request.get_json(silent=True)
    if not data: return jsonify({"error":"No JSON"}),400
    query=data.get("query") or " ".join(filter(None,[data.get("brand"),data.get("item")]))
    filters={
        "brand":data.get("brand"),
        "item":data.get("item"),
        "color":data.get("color"),
        "style":data.get("style"),
        "gender":data.get("gender"),
        "kids":data.get("kids"),
        "price_max":data.get("price_max"),
        "size":data.get("size")
    }
    if not query: return jsonify({"error":"Missing query"}),400
    best=find_best(query,filters)
    if not best: return jsonify({"message":"Inget resultat hittades"}),404
    return jsonify({
        "best_match":{
            "site":best["site"],
            "title":best["title"],
            "price":best.get("price"),
            "url":best["url"],
            "score":best["_score"]
        }
    })

if __name__=="__main__":
    app.run(host="0.0.0.0",port=10000)
