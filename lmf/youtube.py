"""Publication des Reels en YouTube Shorts (API YouTube Data v3, gratuite, sans facturation Google).

Secrets GitHub nécessaires : YT_CLIENT_ID, YT_CLIENT_SECRET, YT_REFRESH_TOKEN (voir le mode opératoire).
Une vidéo verticale de moins de 3 minutes est automatiquement classée en Short par YouTube.
"""
import json
import os
import re

import requests

CLIENT_ID = os.environ.get("YT_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("YT_CLIENT_SECRET", "")
REFRESH_TOKEN = os.environ.get("YT_REFRESH_TOKEN", "")
API = "https://www.googleapis.com/youtube/v3"


class ErreurYouTube(Exception):
    pass


def actif():
    return bool(CLIENT_ID and CLIENT_SECRET and REFRESH_TOKEN)


def _jeton():
    r = requests.post("https://oauth2.googleapis.com/token", timeout=60, data={
        "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET, "refresh_token": REFRESH_TOKEN,
        "grant_type": "refresh_token"})
    if r.status_code != 200:
        raise ErreurYouTube(f"Connexion YouTube refusée ({r.status_code}) : {r.text[:200]} — "
                            "le jeton a peut-être expiré : refais l'étape « jeton » du mode opératoire")
    return r.json()["access_token"]


def _h(jeton):
    return {"Authorization": f"Bearer {jeton}"}


def chaine(jeton=None):
    """Nom et playlist des mises en ligne de la chaîne connectée."""
    jeton = jeton or _jeton()
    r = requests.get(f"{API}/channels", params={"part": "snippet,contentDetails", "mine": "true"},
                     headers=_h(jeton), timeout=60)
    items = r.json().get("items", [])
    if not items:
        raise ErreurYouTube(f"Aucune chaîne YouTube sur ce compte : {r.text[:200]}")
    c = items[0]
    return {"nom": c["snippet"]["title"], "uploads": c["contentDetails"]["relatedPlaylists"]["uploads"]}


def _cle(t):
    return re.sub(r"\W+", " ", (t or "").lower()).strip()[:60]


def deja_publie(titre, jeton, uploads):
    """Garde-fou anti-doublon : le même titre parmi les 15 dernières vidéos de la chaîne ?"""
    try:
        r = requests.get(f"{API}/playlistItems", params={"part": "snippet", "playlistId": uploads, "maxResults": 15},
                         headers=_h(jeton), timeout=60)
        for it in r.json().get("items", []):
            if _cle(it["snippet"]["title"]) == _cle(titre):
                return f"https://youtube.com/shorts/{it['snippet']['resourceId']['videoId']}"
    except Exception as ex:
        print(f"Vérification anti-doublon YouTube impossible ({ex}) : on continue.")
    return None


def titre_short(titre, legende=""):
    t = re.sub(r"[🎬*]", "", titre or "").strip() or (legende or "").split(".")[0]
    t = re.sub(r"\s+", " ", t)[:88].rstrip(" ,.;:")
    return f"{t} #Shorts"


def publier_short(url_ou_chemin, titre, description, tags=None):
    """Envoie la vidéo sur YouTube en public. Renvoie le lien du Short."""
    jeton = _jeton()
    c = chaine(jeton)
    deja = deja_publie(titre, jeton, c["uploads"])
    if deja:
        print(f"Déjà sur YouTube : {deja}")
        return deja
    if os.path.exists(url_ou_chemin):
        with open(url_ou_chemin, "rb") as f:
            video = f.read()
    else:
        video = requests.get(url_ou_chemin, timeout=300).content
    meta = {
        "snippet": {"title": titre[:100], "description": description[:4900], "categoryId": "27",
                    "tags": (tags or [])[:15], "defaultLanguage": "fr", "defaultAudioLanguage": "fr"},
        "status": {"privacyStatus": "public", "selfDeclaredMadeForKids": False},
    }
    r = requests.post("https://www.googleapis.com/upload/youtube/v3/videos",
                      params={"uploadType": "resumable", "part": "snippet,status"},
                      headers={**_h(jeton), "Content-Type": "application/json; charset=UTF-8",
                               "X-Upload-Content-Type": "video/mp4", "X-Upload-Content-Length": str(len(video))},
                      data=json.dumps(meta), timeout=60)
    if r.status_code != 200 or "Location" not in r.headers:
        raise ErreurYouTube(f"YouTube a refusé l'envoi ({r.status_code}) : {r.text[:300]}")
    r = requests.put(r.headers["Location"], data=video, headers={"Content-Type": "video/mp4"}, timeout=900)
    if r.status_code not in (200, 201):
        raise ErreurYouTube(f"Envoi de la vidéo raté ({r.status_code}) : {r.text[:300]}")
    vid = r.json()["id"]
    print(f"Short publié sur « {c['nom']} » : https://youtube.com/shorts/{vid}")
    return f"https://youtube.com/shorts/{vid}"


def tags_depuis(legende):
    return [h.lstrip("#") for h in re.findall(r"#\w+", legende or "")][:10] + ["histoire de France", "Le Média Français"]
