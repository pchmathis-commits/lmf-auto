"""Images exactes via Wikipédia / Wikidata / Wikimedia Commons.

Au lieu de chercher des photos par mots-clés (ce qui donnait un enfant pour « Charles L'Enfant » ou le Pont du Gard
pour « Dupont »), on part de l'ENTITÉ exacte : son article Wikipédia, son identifiant Wikidata, et l'image que
Wikidata lui attribue (P18 : le portrait de la personne, la photo du monument…). Gemini regarde ensuite l'image
pour confirmer. Au moindre doute : pas d'image (la scène devient du texte), jamais une image fausse.
"""
import html
import re
import urllib.parse

import requests

from . import gemini

UA = {"User-Agent": "LeMediaFrancais-bot/1.0 (https://lemediafrancais.fr)"}
WP = "https://fr.wikipedia.org/w/api.php"
COMMONS = "https://commons.wikimedia.org/w/api.php"
HUMAIN = "Q5"


def _get(url, **params):
    r = requests.get(url, params={**params, "format": "json"}, headers=UA, timeout=40)
    r.raise_for_status()
    return r.json()


def entite(titre):
    """Article Wikipédia FR exact → {titre, description, qid} (None si absent ou page d'homonymie)."""
    d = _get(WP, action="query", titles=titre, redirects=1, prop="pageprops|description", ppprop="wikibase_item|disambiguation")
    for p in d.get("query", {}).get("pages", {}).values():
        if "missing" in p or "disambiguation" in p.get("pageprops", {}):
            return None
        qid = p.get("pageprops", {}).get("wikibase_item")
        if qid:
            return {"titre": p["title"], "description": p.get("description", ""), "qid": qid}
    return None


def _claims(qid):
    d = requests.get(f"https://www.wikidata.org/wiki/Special:EntityData/{qid}.json", headers=UA, timeout=40).json()
    return d["entities"][qid].get("claims", {})


def _valeurs(claims, prop):
    out = []
    for c in claims.get(prop, []):
        v = c.get("mainsnak", {}).get("datavalue", {}).get("value")
        if v is not None:
            out.append(v)
    return out


def coordonnees(titre):
    """(lon, lat) d'un lieu d'après Wikidata (P625), ou None."""
    e = entite(titre)
    if not e:
        return None
    for v in _valeurs(_claims(e["qid"]), "P625"):
        return float(v["longitude"]), float(v["latitude"])
    return None


def _fichier_commons(nom, largeur=1600):
    d = _get(COMMONS, action="query", titles="File:" + nom, prop="imageinfo", iiprop="url|extmetadata", iiurlwidth=largeur)
    for p in d.get("query", {}).get("pages", {}).values():
        ii = (p.get("imageinfo") or [{}])[0]
        if not ii.get("url"):
            return None
        meta = ii.get("extmetadata", {})
        auteur = re.sub(r"<[^>]+>", "", html.unescape(meta.get("Artist", {}).get("value", ""))).strip()
        licence = meta.get("LicenseShortName", {}).get("value", "")
        credit = " / ".join(x for x in (auteur[:60], licence, "Wikimedia Commons") if x)
        return {"url": ii.get("thumburl") or ii["url"], "fichier": nom, "credit": credit}
    return None


def image_exacte(titre, nature="lieu", verifier=True):
    """Image officielle de l'entité `titre` (article Wikipédia FR exact).

    nature : « personne », « lieu », « objet », « oeuvre » ou « evenement ».
    Renvoie {url, credit, fichier, titre, description} ou None si on n'est pas sûr.
    """
    try:
        e = entite(titre)
        if not e:
            print(f"Image : pas d'article Wikipédia exact pour « {titre} »")
            return None
        claims = _claims(e["qid"])
        est_humain = HUMAIN in [v.get("id") for v in _valeurs(claims, "P31")]
        if nature == "personne" and not est_humain:
            print(f"Image : « {titre} » n'est pas une personne sur Wikidata, on n'utilise pas son image")
            return None
        fichiers = [v for v in _valeurs(claims, "P18") if isinstance(v, str)]
        if not fichiers:   # pas d'image principale : l'image de l'article Wikipédia
            d = _get(WP, action="query", titles=e["titre"], prop="pageimages", piprop="name")
            for p in d.get("query", {}).get("pages", {}).values():
                if p.get("pageimage"):
                    fichiers = [urllib.parse.unquote(p["pageimage"]).replace("_", " ")]
        for nom in fichiers[:2]:
            img = _fichier_commons(nom)
            if not img:
                continue
            img.update(titre=e["titre"], description=e["description"])
            if not verifier or controle_visuel(img, nature):
                return img
        print(f"Image : aucune image sûre pour « {titre} »")
    except Exception as ex:
        print(f"Image : recherche impossible pour « {titre} » ({ex})")
    return None


def controle_visuel(img, nature):
    """Gemini regarde l'image : montre-t-elle bien l'entité (et pas une signature, une tombe, un enfant…) ?"""
    try:
        octets = requests.get(img["url"].replace("/1600px-", "/640px-"), headers=UA, timeout=60).content
        attendu = {"personne": "un portrait (peinture, gravure, dessin ou photo) de cette personne, à l'âge adulte si elle "
                               "est connue adulte",
                   "lieu": "ce lieu (bâtiment, monument, ville, paysage)",
                   "objet": "cet objet", "oeuvre": "cette œuvre", "evenement": "une image de cet événement"}.get(nature, "ce sujet")
        mime = "image/png" if re.search(r"\.png$", img["url"], re.I) else "image/jpeg"
        r = gemini.image_json(octets, mime=mime, prompt=f"""Cette image doit illustrer « {img['titre']} » ({img['description']}).
Elle doit montrer {attendu}. Elle NE doit PAS être : une signature, une tombe, un blason, une carte, un logo, un
document texte, un timbre, une statue moderne sans rapport, ou une autre personne / un autre lieu.
Réponds en JSON : {{"correspond": true/false, "ce_que_montre_l_image": "..."}}""")
        ok = bool(r.get("correspond"))
        print(f"Contrôle visuel « {img['titre']} » : {'OK' if ok else 'REFUSÉ'} ({r.get('ce_que_montre_l_image', '')[:80]})")
        return ok
    except Exception as ex:
        print(f"Contrôle visuel impossible ({ex}) : image refusée par prudence")
        return False
