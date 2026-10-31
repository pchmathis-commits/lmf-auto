"""Recherche d'images libres de droits : Wikimedia Commons, puis Pixabay et Pexels.

Seules les licences domaine public, CC0 et CC BY sont gardées (pas de CC BY-SA,
qui imposerait la même licence au post). Jamais de photos d'agences de presse.
"""
import io
import re

import requests
from PIL import Image

from . import config as C

UA = {"User-Agent": "LeMediaFrancaisBot/1.0 (contact: mathis@lemediafrancais.fr)"}
COMMONS = "https://commons.wikimedia.org/w/api.php"


def _licence_ok(lic):
    lic = (lic or "").lower()
    if "sa" in re.split(r"[\s\-/]+", lic):
        return False
    return any(k in lic for k in ("public domain", "pd", "cc0", "cc by"))


def _nettoyer(html):
    return re.sub(r"<[^>]+>", "", html or "").strip()


def wikimedia(requete, nombre=6):
    params = {
        "action": "query", "format": "json", "generator": "search",
        "gsrsearch": f"{requete} filetype:bitmap", "gsrnamespace": 6, "gsrlimit": 15,
        "prop": "imageinfo", "iiprop": "url|extmetadata|size", "iiurlwidth": 1600,
    }
    try:
        r = requests.get(COMMONS, params=params, headers=UA, timeout=60)
        pages = r.json().get("query", {}).get("pages", {})
    except Exception:
        return []
    resultats = []
    for p in sorted(pages.values(), key=lambda x: x.get("index", 99)):
        info = (p.get("imageinfo") or [{}])[0]
        meta = info.get("extmetadata", {})
        lic = meta.get("LicenseShortName", {}).get("value", "")
        if not _licence_ok(lic) or info.get("width", 0) < 800:
            continue
        resultats.append({
            "url": info.get("thumburl") or info.get("url"),
            "titre": p.get("title", "").replace("File:", ""),
            "description": _nettoyer(meta.get("ImageDescription", {}).get("value", ""))[:200],
            "credit": f"{_nettoyer(meta.get('Artist', {}).get('value', 'Wikimedia Commons'))[:60]} / {lic} (Wikimedia Commons)",
        })
        if len(resultats) >= nombre:
            break
    return resultats


def pexels(requete, nombre=4):
    if not C.PEXELS_API_KEY:
        return []
    try:
        r = requests.get("https://api.pexels.com/v1/search",
                         params={"query": requete, "per_page": nombre, "orientation": "portrait"},
                         headers={"Authorization": C.PEXELS_API_KEY}, timeout=60)
        photos = r.json().get("photos", [])
    except Exception:
        return []
    return [{
        "url": ph["src"]["large2x"], "titre": ph.get("alt", ""), "description": ph.get("alt", ""),
        "credit": f"{ph.get('photographer', '')} (Pexels)",
    } for ph in photos]


def pixabay(requete, nombre=4):
    if not C.PIXABAY_API_KEY:
        return []
    try:
        r = requests.get("https://pixabay.com/api/",
                         params={"key": C.PIXABAY_API_KEY, "q": requete[:100], "image_type": "photo",
                                 "per_page": max(3, nombre), "safesearch": "true", "min_width": 1000},
                         timeout=60)
        photos = r.json().get("hits", [])[:nombre]
    except Exception:
        return []
    return [{
        "url": ph["largeImageURL"], "titre": ph.get("tags", ""), "description": ph.get("tags", ""),
        "credit": f"{ph.get('user', '')} (Pixabay)",
    } for ph in photos]


def candidats(requete):
    return wikimedia(requete) + pixabay(requete) + pexels(requete)


def telecharger(url):
    r = requests.get(url, headers=UA, timeout=120)
    r.raise_for_status()
    return Image.open(io.BytesIO(r.content)).convert("RGB")


def fond_uni():
    """Secours si aucune image n'est trouvée : fond bleu marine dégradé."""
    img = Image.new("RGB", (C.LARGEUR, C.HAUTEUR), C.BLEU_MARINE)
    px = img.load()
    for y in range(C.HAUTEUR):
        k = 1 - 0.35 * y / C.HAUTEUR
        for x in range(C.LARGEUR):
            px[x, y] = tuple(int(c * k) for c in C.BLEU_MARINE)
    return img


def mettre_en_ligne(chemin):
    """Envoie un JPEG sur imgbb et renvoie son adresse publique."""
    import base64
    if not C.IMGBB_API_KEY:
        raise RuntimeError("IMGBB_API_KEY manquant dans les secrets GitHub")
    with open(chemin, "rb") as f:
        donnees = base64.b64encode(f.read()).decode()
    for essai in range(3):
        try:
            r = requests.post("https://api.imgbb.com/1/upload", params={"key": C.IMGBB_API_KEY},
                              data={"image": donnees}, timeout=120)
            return r.json()["data"]["url"]
        except Exception as e:
            erreur = e
    raise RuntimeError(f"Mise en ligne impossible sur imgbb : {erreur}")
