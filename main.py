# main.py
from flask import Flask, request, jsonify
import requests
from bs4 import BeautifulSoup
import concurrent.futures
import threading
import time
import re

app = Flask(__name__)

USER_AGENT = "Mozilla/5.0 (compatible; ClothesFinder/3.0; +https://example.com)"
REQUEST_TIMEOUT = 10
MAX_WORKERS = 6
CACHE_TTL = 300

_cache = {}
_cache_lock = threading.Lock()
def cache_get(key):
    with _cache_lock:
        item = _cache.get(key)
        if not item:
            return None
        ts, value = item
        if time.time() - ts > CACHE_TTL:
            del _cache[key]
            return None
        return value
def cache_set(key, value):
    with _cache_lock:
        _cache[key] = (time.time(), value)

# --- Synonyms (utökad massiv lista) ---
SYNONYMS = {
    # Items
    "byxor": ["byxa", "pants", "träningsbyxor", "joggers", "mjukisbyxor", "mysbyxor", "jeans", "denim", "slimfit", "skinny", "baggy", "loose"],
    "jeans": ["jeans", "denim", "jeansen"],
    "hoodie": ["hoodie", "luvtröja", "hoodtröja", "zip hoodie", "hoodie med dragkedja", "hood med dragkedja"],
    "tröja": ["tröja","tröjor","pullover","sweater","överdel","top","kortärmad tröja","t-shirt","tshirt","tee"],
    "t-shirt": ["t-shirt","tshirt","t shirt","tee","kortärmad tröja"],
    "jacka": ["jacka","jackor","coat","puffer","bomber","windbreaker","ytterjacka","anorak"],

    # Colors
    "svart": ["black","mörk","dark","svarta"],
    "vit": ["white","ljus","light","vita"],
    "blå": ["blue","navy","denim","blåa"],
    "grå": ["gray","grey","mellangrå","ljusgrå","gråa"],
    "beige": ["sand","cream","offwhite","beiga"],
    "röd": ["red","rose","rosa","röda"],
    "grön": ["green","oliv","khaki","gröna"],

    # Styles
    "baggy": ["loose","wide","oversized"],
    "skinny": ["tight","slim","fit"],
    "oversized": ["loose fit","stor","baggy"],
    "träning": ["sport","gym","tränings","athletic","track"],
    "mjukis": ["mys","soft","casual","relax","lounge"],
    "mys": ["mjukis","soft","cozy"],
    "utomhus": ["outdoor","ytter","friluft","vandrings"],
    "luvtröja": ["hoodie med dragkedja","zip hoodie"],

    # Gender
    "herr": ["man","male","men"],
    "dam": ["kvinna","female","women","tjej","tjejer"],
    "unisex": ["både","alla","universal"],

    # Kids
    "barn": ["kids","child","junior","pojke","flicka","youth","baby"]
}

# --- Size mapping ---
SIZE_MAP = {
    "XS": ["xs","extra small","28","30","34"],
    "S": ["s","small","36","38","44"],
    "M": ["m","medium","38","40","46","32 32","32 34"],
    "L": ["l","large","40","42","48","34 34","34 36"],
    "XL": ["xl","extra large","44","50","36 36","36 38"],
    "XXL": ["xxl","2xl","52","38 40"],
    "KIDS": ["92","98","104","110","116","122","128","134","140","146","152","158","164","170"]
}

# --- Utils ---
def clean_text(t):
    if not t: return ""
    return re.sub(r'\s+',' ',t).strip().lower()

def parse_price(text):
    if not text: return None
    m = re.search(r'(\d+[ \d\.]*\d)', text.replace('\u00A0',' '))
    if not m: return None
    num = m.group(1).replace(' ','').replace('.','').replace(',','')
    try: return int(num)
    except:
        try: return float(num)
        except: return None

def http_get(url, params=None):
    headers={"User-Agent":USER_AGENT}
    try:
        r=requests.get(url,params=params,headers=headers,timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        return r.text
    except: return None

def match_synonyms(word,title):
    word = clean_text(word)
    title = clean_text(title)
    if word in title: return True
    for syn in SYNONYMS.get(word,[]):
        if syn in title: return True
    return False

def size_matches(size_input,text):
    text = clean_text(text)
    for key,vals in SIZE_MAP.items():
        if size_input.upper()==key:
            for v in vals:
                if v in text: return True
    return False

# --- Scrapers ---
def search_vinted(query,filters):
    results=[]
    q=requests.utils.quote(query)
    url=f"https://www.vinted.se/catalog?search_text={q}"
    html=http_get(url)
    if not html: return results
    soup=BeautifulSoup(html,"html.parser")
    anchors=soup.select("a.catalog-item__link") or [a for a in soup.find_all("a",href=True) if "/item/" in a.get("href","")]
    for a in anchors[:12]:
        href=a.get("href")
        link="https://www.vinted.se"+href if href.startswith("/") else href
        title=clean_text(a.get_text() or a.get("title") or "")
        price_tag=a.select_one(".catalog-item__price")
        price=parse_price(price_tag.get_text() if price_tag else "")
        results.append({"site":"Vinted","title":title,"price":price,"url":link,"used":True})
    return results

def search_tradera(query,filters):
    results=[]
    q=requests.utils.quote(query)
    url=f"https://www.tradera.com/search?q={q}"
    html=http_get(url)
    if not html: return results
    soup=BeautifulSoup(html,"html.parser")
    anchors=soup.select("a.listing-card__link") or [a for a in soup.find_all("a",href=True) if "/item/" in a.get("href","")]
    for a in anchors[:12]:
        href=a.get("href")
        link=href if href.startswith("http") else "https://www.tradera.com"+href
        title=clean_text(a.get_text() or a.get("title") or "")
        price_tag=a.select_one(".listing-card__price")
        price=parse_price(price_tag.get_text() if price_tag else "")
        results.append({"site":"Tradera","title":title,"price":price,"url":link,"used":True})
    return results

# --- Generic scraper for Blocket/Sellpy/Plick/FB ---
def search_generic(query,filters,base_url,path_contains=None,site_name=None):
    results=[]
    q=requests.utils.quote(query)
    url=f"{base_url}?q={q}" if "?" in base_url else f"{base_url}{q}"
    html=http_get(url)
    if not html: return results
    soup=BeautifulSoup(html,"html.parser")
    anchors=soup.find_all("a",href=True)[:30]
    for a in anchors:
        href=a.get("href")
        if not href or (path_contains and path_contains not in href): continue
        link=href if href.startswith("http") else base_url.split("?")[0].rstrip("/")+href
        title=clean_text(a.get_text() or a.get("title") or "")
        price=None
        for p in (a.parent,getattr(a.parent,"parent",None)):
            if p:
                price=parse_price(p.get_text())
                if price: break
        results.append({"site":site_name or base_url.split("//")[1].split("/")[0],
                        "title":title,"price":price,"url":link,"used":True})
    return results

# --- Register sites ---
sites=[
    ("Vinted",search_vinted),
    ("Tradera",search_tradera),
    ("Blocket",lambda q,f: search_generic(q,f,"https://www.blocket.se/annonser/hela_sverige","/annons/","Blocket")),
    ("Sellpy",lambda q,f: search_generic(q,f,"https://www.sellpy.se/sok/","/product/","Sellpy")),
    ("Plick",lambda q,f: search_generic(q,f,"https://www.plick.com/search/","/item/","Plick")),
    ("FacebookMarketplace",lambda q,f: search_generic(q,f,"https://www.facebook.com/marketplace/search/","/item/","Facebook")),
]

# --- Scoring system ---
def score_product(prod,filters):
    score=0
    title=prod.get("title","").lower()
    brand=filters.get("brand","").lower()
    item=filters.get("item","").lower()
    style=filters.get("style","").lower()
    color=filters.get("color","").lower()
    gender=filters.get("gender","").lower()
    kids=filters.get("kids",False)
    size=filters.get("size","").lower()
    price_max=filters.get("price_max")

    if item and match_synonyms(item,title): score+=25
    if brand and match_synonyms(brand,title): score+=20
    if style and match_synonyms(style,title): score+=10
    if color and match_synonyms(color,title): score+=5
    if gender and match_synonyms(gender,title): score+=15

    is_kids=any(v in title for v in SIZE_MAP["KIDS"]) or match_synonyms("barn",title)
    if kids and is_kids: score+=15
    elif not kids and is_kids: return -1000  # veto

    if size and size_matches(size,title): score+=10

    price=prod.get("price")
    if price and price_max:
        try:
            price_val=float(price)
            if price_val<=float(price_max):
                score+=int(max(0,(float(price_max)-price_val)//10))
        except: pass
    if prod.get("url"): score+=1
    if prod.get("title"): score+=1
    return score

# --- Coordinator ---
def find_best_across_sites(query,filters):
    cache_key=f"{query}|{filters}"
    cached=cache_get(cache_key)
    if cached: return cached
    results=[]
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures=[]
        for name,fn in sites:
            futures.append(ex.submit(lambda n,f: _call_safe(n,fn,query,filters), name, filters))
        for fut in concurrent.futures.as_completed(futures,timeout=30):
            try:
                site_results=fut.result()
                if site_results: results.extend(site_results)
            except: continue
    best=None
    best_score=-10**9
    for prod in results:
        s=score_product(prod,filters)
        prod["_score"]=s
        if s>best_score or (s==best_score and prod.get("price",1e9)<best.get("price",1e9) if best else True):
            best_score=s
            best=prod
    cache_set(cache_key,best)
    return best

def _call_safe(site_name,fn,query,filters):
    try: return fn(query,filters)
    except: return []

# --- Flask endpoints ---
@app.route("/")
def home(): return jsonify({"message":"ClothesFinder API running","routes":{"/find_item":"POST"}})

@app.route("/find_item",methods=["POST"])
def find_item_route():
    data=request.get_json(silent=True)
    if not data: return jsonify({"error":"No JSON payload provided"}),400
    query=data.get("query","")
    filters={
        "brand":data.get("brand"),
        "item":data.get("item"),
        "size":data.get("size"),
        "color":data.get("color"),
        "style":data.get("style"),
        "gender":data.get("gender"),
        "kids":data.get("kids",False),
        "price_max":data.get("price_max")
    }
    if not query:
        parts=[]
        if filters.get("brand"): parts.append(filters["brand"])
        if filters.get("item"): parts.append(filters["item"])
        query=" ".join(parts)
    if not query: return jsonify({"error":"No query or filters provided"}),400
    best=find_best_across_sites(query,filters)
    if not best: return jsonify({"message":"Inget resultat hittades","link":None}),404
    resp={
        "site":best.get("site"),
        "title":best.get("title"),
        "price":best.get("price"),
        "url":best.get("url"),
        "score":best.get("_score",0)
    }
    return jsonify({"best_match":resp})

if __name__=="__main__":
    app.run(host="0.0.0.0",port=10000)
