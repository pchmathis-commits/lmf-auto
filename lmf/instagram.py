"""Publication Instagram via l'API officielle (connexion « Instagram Login »)."""
import datetime as dt
import json
import re
import time

import requests

from . import config as C
from . import sheet

BASE = f"{C.GRAPH_URL}/{C.GRAPH_VERSION}"


class ErreurInstagram(Exception):
    pass


# Compte utilisé : None = Le Média Français (par défaut), "paris" = Le Média Paris.
_COMPTE = {"nom": None, "uid": None}


def utiliser_compte(nom):
    """Bascule la publication vers un autre compte du groupe (None = Le Média Français)."""
    _COMPTE["nom"], _COMPTE["uid"] = nom, None


def _jeton(compte="actuel"):
    nom = _COMPTE["nom"] if compte == "actuel" else compte
    jeton = sheet.lire_jeton(nom)
    if not jeton:
        menu = "Définir le jeton Le Média Paris" if nom == "paris" else "Définir le jeton Instagram"
        raise ErreurInstagram(f"Jeton Instagram absent : exécute « {menu} » dans le Sheet")
    return jeton


def _uid():
    """Identifiant Instagram du compte utilisé (lu une fois via /me pour les comptes autres que le principal)."""
    if _COMPTE["nom"] is None:
        return C.IG_USER_ID
    if not _COMPTE["uid"]:
        r = requests.get(f"{BASE}/me", params={"fields": "user_id,username", "access_token": _jeton()}, timeout=60)
        _COMPTE["uid"] = str(_verifier(r)["user_id"])
    return _COMPTE["uid"]


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
            r = requests.post(f"{BASE}/{_uid()}/media", data=donnees, timeout=120)
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
    r = requests.post(f"{BASE}/{_uid()}/media_publish",
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


def publier_reel(url_video, legende, essai=None):
    """Publie un Reel. La vidéo est envoyée directement à Instagram (envoi « resumable »), sans dépendre d'un
    hébergeur ; si cet envoi échoue, Instagram va la chercher lui-même à son adresse publique.

    essai (Trial Reel) : le Reel est d'abord montré uniquement aux non-abonnés, puis diffusé automatiquement
    à tes abonnés s'il marche bien (SS_PERFORMANCE). Si Instagram refuse l'essai (limite atteinte, compte non
    éligible), on republie en Reel normal."""
    if essai is None:
        essai = getattr(C, "TRIAL_REELS", False)
    try:
        return _publier_reel(url_video, legende, essai)
    except ErreurInstagram as ex:
        if essai and "trial" in str(ex).lower():
            print(f"Trial Reel refusé ({ex}) : publication en Reel normal.")
            return _publier_reel(url_video, legende, False)
        raise


def _publier_reel(url_video, legende, essai):
    jeton = _jeton()
    video = requests.get(url_video, timeout=300).content
    base = {"media_type": "REELS", "caption": legende, "share_to_feed": "true", "thumb_offset": "800",
            "access_token": jeton}
    if essai:
        base["trial_params"] = json.dumps({"graduation_strategy": "SS_PERFORMANCE"})
    try:
        conteneur = _creer({**base, "upload_type": "resumable"})
        r = requests.post(f"https://rupload.facebook.com/ig-api-upload/{C.GRAPH_VERSION}/{conteneur}", data=video,
                          headers={"Authorization": f"OAuth {jeton}", "offset": "0", "file_size": str(len(video))},
                          timeout=600)
        if r.status_code != 200 or not r.json().get("success", True):
            raise ErreurInstagram(f"envoi de la vidéo refusé : {r.text[:200]}")
    except Exception as ex:
        if essai and "trial" in str(ex).lower():
            raise ErreurInstagram(str(ex))
        print(f"Envoi direct impossible ({ex}) : Instagram va chercher la vidéo à son adresse.")
        conteneur = _creer({**base, "video_url": url_video})
    _attendre(conteneur, jeton, maximum=120)   # le traitement d'une vidéo prend 1 à 5 minutes
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
        r = requests.get(f"{BASE}/{_uid()}/media", timeout=60, params={
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
    """Le jeton dure 60 jours ; on le prolonge chaque semaine, pour chaque compte du groupe."""
    for compte in (None, "paris"):
        try:
            jeton = sheet.lire_jeton(compte)
        except Exception as ex:
            print(f"Jeton {compte or 'principal'} illisible : {ex}")
            continue
        if not jeton:
            if compte is None:
                raise ErreurInstagram("Jeton Instagram principal absent")
            continue
        r = requests.get(f"{C.GRAPH_URL}/refresh_access_token",
                         params={"grant_type": "ig_refresh_token", "access_token": jeton}, timeout=60)
        sheet.ecrire_jeton(_verifier(r)["access_token"], compte)
        print(f"Jeton {compte or 'Le Média Français'} prolongé")
    return True
