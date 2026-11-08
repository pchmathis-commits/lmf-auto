"""Rapport hebdomadaire (samedi matin) : concurrents via Apify + tes stats Instagram + prospection.

Produit data/veille/AAAA-MM-JJ.md (lisible) et .json (données brutes), puis prévient sur Telegram.
Le week-end, dis à Claude « on lance l'analyse » : il lit ce rapport et propose les modifs.

Lancé par l'horloge du Sheet le samedi vers 7 h : tâche « Veille hebdo ».
"""
import datetime as dt
import json
import os
import statistics
import time

import requests

from . import config as C
from . import prospection as P

RACINE = os.path.join(os.path.dirname(__file__), "..")
DOSSIER = os.path.join(RACINE, "data", "veille")
APIFY = "https://api.apify.com/v2"
ACTEUR = "apify~instagram-scraper"


# --------------------------------------------------------------------------
# Apify
# --------------------------------------------------------------------------
def _apify(entree, attente_max=1500):
    jeton = C.APIFY_TOKEN
    r = requests.post(f"{APIFY}/acts/{ACTEUR}/runs", params={"token": jeton}, json=entree, timeout=60)
    r.raise_for_status()
    run = r.json()["data"]
    debut = time.time()
    while run["status"] in ("READY", "RUNNING") and time.time() - debut < attente_max:
        time.sleep(20)
        run = requests.get(f"{APIFY}/actor-runs/{run['id']}", params={"token": jeton}, timeout=60).json()["data"]
    if run["status"] != "SUCCEEDED":
        raise RuntimeError(f"Apify : statut {run['status']}")
    items = requests.get(f"{APIFY}/datasets/{run['defaultDatasetId']}/items",
                         params={"token": jeton, "clean": "true", "format": "json"}, timeout=120)
    return items.json()


def concurrents():
    urls = [f"https://www.instagram.com/{c.lstrip('@')}/" for c in C.COMPTES_SURVEILLES]
    profils = _apify({"directUrls": urls, "resultsType": "details", "resultsLimit": 1})
    abonnes = {p.get("username", "").lower(): p.get("followersCount") or 0 for p in profils}
    posts = _apify({"directUrls": urls, "resultsType": "posts", "resultsLimit": C.POSTS_PAR_COMPTE,
                    "onlyPostsNewerThan": f"{C.JOURS_VEILLE} days"})
    propres = []
    for p in posts:
        if p.get("error"):
            continue
        compte = (p.get("ownerUsername") or "").lower()
        propres.append({
            "compte": compte, "abonnes": abonnes.get(compte, 0),
            "type": "Reel" if p.get("productType") == "clips" or p.get("type") == "Video" else
                    ("Carrousel" if p.get("type") == "Sidecar" else "Image"),
            "date": (p.get("timestamp") or "")[:10], "likes": p.get("likesCount") or 0,
            "commentaires": p.get("commentsCount") or 0,
            "vues": p.get("videoPlayCount") or p.get("videoViewCount") or None,
            "legende": (p.get("caption") or "")[:400], "lien": p.get("url"),
        })
    # performance relative : likes du post / médiane des likes du compte cette semaine
    par_compte = {}
    for p in propres:
        par_compte.setdefault(p["compte"], []).append(p["likes"])
    for p in propres:
        med = statistics.median(par_compte[p["compte"]]) or 1
        p["x_mediane"] = round(p["likes"] / med, 1)
        p["taux"] = round(100 * (p["likes"] + 3 * p["commentaires"]) / p["abonnes"], 2) if p["abonnes"] else None
    return propres, abonnes


# --------------------------------------------------------------------------
# Tes stats Instagram
# --------------------------------------------------------------------------
METRIQUES = ["views", "reach", "saved", "shares", "total_interactions", "follows", "profile_visits"]


def mes_stats():
    from . import instagram as IG
    jeton = IG._jeton()
    base = IG.BASE
    profil = requests.get(f"{base}/{C.IG_USER_ID}", params={"fields": "followers_count,media_count",
                                                            "access_token": jeton}, timeout=60).json()
    depuis = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=C.JOURS_VEILLE)
    r = requests.get(f"{base}/{C.IG_USER_ID}/media", params={
        "fields": "id,caption,media_type,media_product_type,timestamp,like_count,comments_count,permalink",
        "limit": 60, "access_token": jeton}, timeout=60).json()
    posts = []
    for m in r.get("data", []):
        quand = dt.datetime.fromisoformat(m["timestamp"].replace("+0000", "+00:00"))
        if quand < depuis:
            continue
        stats = {}
        rep = requests.get(f"{base}/{m['id']}/insights", params={"metric": ",".join(METRIQUES),
                                                                 "access_token": jeton}, timeout=60).json()
        if "error" in rep:  # une métrique non disponible : on les demande une par une
            for met in METRIQUES:
                x = requests.get(f"{base}/{m['id']}/insights", params={"metric": met, "access_token": jeton},
                                 timeout=60).json()
                for d in x.get("data", []):
                    stats[d["name"]] = d["values"][0]["value"] if d.get("values") else d.get("total_value", {}).get("value")
        else:
            for d in rep.get("data", []):
                stats[d["name"]] = d["values"][0]["value"] if d.get("values") else d.get("total_value", {}).get("value")
        posts.append({
            "date": quand.astimezone(P.PARIS).strftime("%Y-%m-%d %H:%M"),
            "type": "Carrousel" if m.get("media_type") == "CAROUSEL_ALBUM" else
                    ("Reel" if m.get("media_product_type") == "REELS" else "Image"),
            "legende": (m.get("caption") or "").split("\n")[0][:140],
            "likes": m.get("like_count"), "commentaires": m.get("comments_count"), "lien": m.get("permalink"),
            **stats,
        })
    return {"abonnes": profil.get("followers_count"), "posts": posts}


# --------------------------------------------------------------------------
# Rapport
# --------------------------------------------------------------------------
def _md_tableau(lignes, colonnes):
    out = ["| " + " | ".join(c for c, _ in colonnes) + " |", "|" + "---|" * len(colonnes)]
    for l in lignes:
        cellules = []
        for _, f in colonnes:
            v = f(l) if callable(f) else l.get(f)
            cellules.append("–" if v is None or v == "" else str(v).replace("|", "/").replace("\n", " "))
        out.append("| " + " | ".join(cellules) + " |")
    return "\n".join(out)


def rapport():
    jour = P.maintenant().date().isoformat()
    os.makedirs(DOSSIER, exist_ok=True)
    donnees, md = {"date": jour}, [f"# Rapport hebdo – {jour}\n"]

    # 1. Tes stats
    try:
        moi = mes_stats()
        donnees["moi"] = moi
        posts = sorted(moi["posts"], key=lambda p: p.get("views") or 0, reverse=True)
        tot_follows = sum(p.get("follows") or 0 for p in posts)
        md.append(f"## Le Média Français\n\nAbonnés : **{moi['abonnes']}** · {len(posts)} posts sur 7 jours · "
                  f"abonnés gagnés via les posts : **{tot_follows}**\n")
        md.append(_md_tableau(posts, [("Date", "date"), ("Type", "type"), ("Post", "legende"),
                                      ("Vues", "views"), ("Portée", "reach"), ("Likes", "likes"),
                                      ("Enreg.", "saved"), ("Partages", "shares"), ("Abonnés +", "follows")]))
    except Exception as ex:
        md.append(f"## Le Média Français\n\n⚠️ Stats indisponibles : {ex}")

    # 2. Concurrents
    if C.APIFY_TOKEN:
        try:
            posts, abonnes = concurrents()
            donnees["concurrents"] = posts
            top = sorted(posts, key=lambda p: p["x_mediane"], reverse=True)[:25]
            md.append("\n## Ce qui a surperformé chez les autres (×médiane du compte)\n")
            md.append(_md_tableau(top, [("Compte", "compte"), ("Type", "type"), ("×méd.", "x_mediane"),
                                        ("Likes", "likes"), ("Com.", "commentaires"), ("Vues", "vues"),
                                        ("Sujet", lambda p: p["legende"][:160]), ("Lien", "lien")]))
            md.append("\n## Comptes suivis\n")
            lignes = []
            for compte in sorted({p["compte"] for p in posts}):
                ps = [p for p in posts if p["compte"] == compte]
                lignes.append({"compte": compte, "abonnes": abonnes.get(compte), "posts": len(ps),
                               "likes_med": int(statistics.median(p["likes"] for p in ps)),
                               "types": ", ".join(sorted({p["type"] for p in ps}))})
            md.append(_md_tableau(lignes, [("Compte", "compte"), ("Abonnés", "abonnes"), ("Posts 7 j", "posts"),
                                           ("Likes médians", "likes_med"), ("Formats", "types")]))
        except Exception as ex:
            md.append(f"\n## Concurrents\n\n⚠️ Apify indisponible : {ex}")
    else:
        md.append("\n## Concurrents\n\n(APIFY_TOKEN absent des secrets GitHub)")

    # 3. Prospection
    e = P.charger()
    debut = P.maintenant() - dt.timedelta(days=7)
    recents = [c for c in e["contacts"] if P.lire_date(c["cree_le"]) >= debut]
    donnees["prospection"] = [{k: c.get(k) for k in ("organisation", "type", "email", "statut", "objet")} for c in recents]
    md.append(f"\n## Prospection (7 jours)\n\n{len(recents)} contacts · "
              f"{sum(c['statut'] in ('répondu', 'en discussion') for c in recents)} réponses\n")
    md.append(_md_tableau(recents, [("Organisation", "organisation"), ("Type", "type"), ("Statut", "statut")]))

    with open(os.path.join(DOSSIER, f"{jour}.json"), "w", encoding="utf-8") as f:
        json.dump(donnees, f, ensure_ascii=False, indent=1)
    with open(os.path.join(DOSSIER, f"{jour}.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")
    P.dire(e, f"📊 Rapport hebdo prêt (data/veille/{jour}.md).\nQuand tu veux, écris à Claude : « on lance l'analyse ».")
    P.sauver(e)
    print("\n".join(md))


if __name__ == "__main__":
    rapport()
