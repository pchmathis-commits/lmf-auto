"""Publication Instagram via l'API officielle (connexion « Instagram Login »)."""
import time

import requests

from . import config as C
from . import sheet

BASE = f"{C.GRAPH_URL}/{C.GRAPH_VERSION}"


class ErreurInstagram(Exception):
    pass


def _jeton():
    jeton = sheet.lire_jeton()
    if not jeton:
        raise ErreurInstagram("Jeton Instagram absent : exécute « Définir le jeton Instagram » dans le Sheet")
    return jeton


def _verifier(r):
    try:
        data = r.json()
    except ValueError:
        raise ErreurInstagram(f"Réponse illisible ({r.status_code}) : {r.text[:300]}")
    if r.status_code != 200 or "error" in data:
        msg = data.get("error", {}).get("message", r.text[:300])
        raise ErreurInstagram(f"Instagram : {msg}")
    return data


def _attendre(conteneur, jeton, maximum=60):
    for _ in range(maximum):
        r = requests.get(f"{BASE}/{conteneur}", params={"fields": "status_code", "access_token": jeton}, timeout=60)
        statut = _verifier(r).get("status_code")
        if statut == "FINISHED":
            return
        if statut in ("ERROR", "EXPIRED"):
            raise ErreurInstagram(f"Instagram a refusé une image (statut {statut})")
        time.sleep(5)
    raise ErreurInstagram("Instagram n'a pas fini de traiter les images à temps")


def _publier_conteneur(conteneur, jeton):
    _attendre(conteneur, jeton)
    r = requests.post(f"{BASE}/{C.IG_USER_ID}/media_publish",
                      data={"creation_id": conteneur, "access_token": jeton}, timeout=120)
    media_id = _verifier(r)["id"]
    r = requests.get(f"{BASE}/{media_id}", params={"fields": "permalink", "access_token": jeton}, timeout=60)
    return _verifier(r).get("permalink", "")


def publier_carrousel(urls, legende):
    if not 2 <= len(urls) <= 10:
        raise ErreurInstagram(f"Un carrousel doit avoir entre 2 et 10 images ({len(urls)} reçues)")
    jeton = _jeton()
    enfants = []
    for url in urls:
        r = requests.post(f"{BASE}/{C.IG_USER_ID}/media",
                          data={"image_url": url, "is_carousel_item": "true", "access_token": jeton}, timeout=120)
        enfants.append(_verifier(r)["id"])
    for e in enfants:
        _attendre(e, jeton)
    r = requests.post(f"{BASE}/{C.IG_USER_ID}/media",
                      data={"media_type": "CAROUSEL", "children": ",".join(enfants),
                            "caption": legende, "access_token": jeton}, timeout=120)
    return _publier_conteneur(_verifier(r)["id"], jeton)


def publier_image(url, legende):
    jeton = _jeton()
    r = requests.post(f"{BASE}/{C.IG_USER_ID}/media",
                      data={"image_url": url, "caption": legende, "access_token": jeton}, timeout=120)
    return _publier_conteneur(_verifier(r)["id"], jeton)


def tester():
    jeton = _jeton()
    r = requests.get(f"{BASE}/me", params={"fields": "user_id,username", "access_token": jeton}, timeout=60)
    return _verifier(r)


def prolonger_jeton():
    """Le jeton dure 60 jours ; on le prolonge chaque semaine."""
    jeton = _jeton()
    r = requests.get(f"{C.GRAPH_URL}/refresh_access_token",
                     params={"grant_type": "ig_refresh_token", "access_token": jeton}, timeout=60)
    nouveau = _verifier(r)["access_token"]
    sheet.ecrire_jeton(nouveau)
    return True
