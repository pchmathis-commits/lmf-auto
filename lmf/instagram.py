"""Publication Instagram via l'API officielle (connexion « Instagram Login »)."""
import datetime as dt
import re
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


TRANSITOIRE = ("timeout", "timed out", "temporar", "transient", "please retry", "try again", "unexpected error",
               "réponse illisible (5")


def _creer(donnees, essais=4):
    """Crée un conteneur Instagram. Si Instagram met trop de temps à récupérer l'image (« Timeout ») ou a un
    souci passager, on réessaie (jusqu'à 4 fois, 30 s, 60 s, 90 s d'écart) au lieu d'échouer tout de suite."""
    for essai in range(essais):
        try:
            r = requests.post(f"{BASE}/{C.IG_USER_ID}/media", data=donnees, timeout=120)
            return _verifier(r)["id"]
        except (ErreurInstagram, requests.RequestException) as ex:
            if essai + 1 >= essais or not any(m in str(ex).lower() for m in TRANSITOIRE):
                raise
            print(f"Instagram : souci passager ({ex}), nouvel essai dans {30 * (essai + 1)} s…")
            time.sleep(30 * (essai + 1))


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
        enfants.append(_creer({"image_url": url, "is_carousel_item": "true", "access_token": jeton}))
    for e in enfants:
        _attendre(e, jeton)
    conteneur = _creer({"media_type": "CAROUSEL", "children": ",".join(enfants), "caption": legende,
                        "access_token": jeton})
    return _publier_conteneur(conteneur, jeton)


def publier_image(url, legende):
    jeton = _jeton()
    conteneur = _creer({"image_url": url, "caption": legende, "access_token": jeton})
    return _publier_conteneur(conteneur, jeton)


def cle_legende(legende):
    return re.sub(r"\W+", " ", (legende or "").lower()).strip()[:160]


def deja_publie(legende, heures=48):
    """Renvoie le lien du post si une légende identique est déjà en ligne depuis moins de 48 h, sinon None."""
    cle = cle_legende(legende)
    if not cle:
        return None
    try:
        jeton = _jeton()
        r = requests.get(f"{BASE}/{C.IG_USER_ID}/media", timeout=60, params={
            "fields": "caption,timestamp,permalink", "limit": 25, "access_token": jeton})
        limite = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=heures)
        for m in _verifier(r).get("data", []):
            quand = dt.datetime.strptime(m["timestamp"], "%Y-%m-%dT%H:%M:%S%z")
            if quand >= limite and cle_legende(m.get("caption")) == cle:
                return m.get("permalink") or "déjà publié"
    except Exception as ex:
        print(f"Vérification anti-doublon impossible ({ex}) : on continue.")
    return None


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
