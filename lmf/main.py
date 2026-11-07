"""Point d'entrée : python -m lmf.main <commande>

Commandes :
  carrousel        crée un carrousel, le met en ligne et envoie le mail de validation
  actu             crée un post d'actualité en une image (même chose)
  notifier         renvoie au Sheet le dernier post créé (si l'envoi du mail a échoué)
  publier          publie les posts arrivés à échéance (ou validés)
  prolonger-jeton  prolonge le jeton Instagram (chaque semaine)
  tester           vérifie Gemini, le Sheet et Instagram
  demo             génère un carrousel d'exemple sans rien publier
"""
import datetime as dt
import json
import os
import sys
import traceback
import zoneinfo

from . import config as C
from . import contenu, images, render

PARIS = zoneinfo.ZoneInfo("Europe/Paris")
RACINE = os.path.join(os.path.dirname(__file__), "..")
OUT = os.path.join(RACINE, "out", "post.json")


def _maintenant():
    return dt.datetime.now(PARIS)


def _id(type_):
    return _maintenant().strftime("%Y%m%d-%H%M") + ("-c" if type_ == "carrousel" else "-a")


def _dossier(post_id):
    return os.path.join(RACINE, "out", "posts", post_id)


def _url(post_id, fichier, sans_reseau=False):
    chemin = os.path.join(_dossier(post_id), fichier)
    return chemin if sans_reseau else images.mettre_en_ligne(chemin)


def _sauver(post):
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(post, f, ensure_ascii=False, indent=2)
    print(f"Post prêt : {post['id']} · {post['titre']} · vérification : {post['verification_ok']}")


def _legende(base, hashtags, credits):
    credits = sorted({c for c in credits if c})
    appel = f"👉 Abonne-toi à {C.COMPTE} : chaque jour, une histoire de France que tu ne connais pas."
    txt = base.strip() + "\n\n" + appel + "\n\n" + (hashtags or "").strip()
    if credits:
        txt += "\n\nCrédits photos : " + " · ".join(credits)
    return txt[:2200]


# ---------------------------------------------------------------------------
def creer_carrousel(fiche, ligne_sujet=None, sans_reseau=False, sans_mise_en_ligne=False, c=None):
    post_id = _id("carrousel")
    c = c or contenu.rediger_carrousel(fiche)
    c = contenu.controler_carrousel(c)
    if len(c["couverture"]["texte"].split()) > 14:  # garde-fou : couverture trop longue → on la fait raccourcir
        try:
            court = contenu.gemini.texte(
                "Raccourcis cette accroche Instagram à 12 mots maximum, même sens, garde le mystère, "
                "réponds uniquement avec la phrase : " + c["couverture"]["texte"], temperature=0.3).strip().strip('"«» ')
            if 3 <= len(court.split()) <= 14:
                c["couverture"]["texte"] = court
        except Exception:
            pass
    textes = [c["couverture"]["texte"]] + [s["texte"] for s in c["slides"]]

    contexte = fiche.get("sujet", "")
    if fiche.get("famille") == "Ton mois":
        contexte = ("Jeu « ton mois de naissance = ton … » : l'association mois → élément est un jeu, pas une affirmation. "
                    "Vérifie seulement que chaque élément cité existe réellement et appartient bien au thème.")
    ok, rapport = contenu.verifier(textes, contexte)

    requetes = [c["couverture"].get("image", fiche["sujet"])] + [s.get("image", fiche["sujet"]) for s in c["slides"]]
    cands = [[] for _ in requetes] if sans_reseau else [images.candidats(q) for q in requetes]
    choix = contenu.choisir_images(requetes, cands, fiche["sujet"]) if not sans_reseau else [None] * len(requetes)

    # Une image différente et nette par slide : jamais deux fois la même photo dans un carrousel.
    deja, photos, credits = set(), [], []
    reserve = [x for liste in cands for x in liste]  # toutes les images trouvées, en secours

    def _essayer(candidat):
        if not candidat or candidat["url"] in deja:
            return None
        deja.add(candidat["url"])
        try:
            im = images.telecharger(candidat["url"])
        except Exception:
            return None
        if min(im.size) < 700:  # trop petite : elle serait floue en 1080 × 1350
            return None
        credits.append(candidat["credit"])
        return im

    for i, ch in enumerate(choix):
        img = _essayer(ch)
        for autre in (cands[i] if not img else []):
            img = img or _essayer(autre)
        for autre in (reserve if not img else []):
            img = img or _essayer(autre)
        photos.append(img or images.fond_uni())

    dossier = _dossier(post_id)
    fichiers = []
    if c["couverture"].get("portrait") and choix and choix[0]:
        photos[0] = photos[0].convert("L").convert("RGB")  # portrait d'archive en noir et blanc
    slides = [render.slide_couverture(photos[0], c["couverture"]["texte"], c["couverture"].get("rouges"),
                                      etiquette=fiche.get("etiquette"))]
    for photo, s in zip(photos[1:], c["slides"]):
        slides.append(render.slide_texte(photo, s["texte"], s.get("rouges")))
    q = c.get("question_finale") or {"texte": "Et toi, tu le savais ?", "rouges": ["savais"]}
    slides.append(render.slide_finale(q["texte"], q.get("rouges")))
    for i, img in enumerate(slides, 1):
        nom = f"{i:02d}.jpg"
        render.enregistrer(img, os.path.join(dossier, nom))
        fichiers.append(nom)

    post = {
        "id": post_id, "type": "Carrousel", "titre": c["couverture"]["texte"],
        "images": [_url(post_id, f, sans_reseau or sans_mise_en_ligne) for f in fichiers],
        "legende": _legende((fiche.get("prefixe_legende", "") + c.get("legende", "")), c.get("hashtags", ""), credits),
        "verification_ok": ok, "verification": rapport,
        "lien_source": fiche.get("sources", ""), "ligne_sujet": ligne_sujet,
        "publication_prevue": (_maintenant() + dt.timedelta(minutes=C.DELAI_AVANT_PUBLICATION_MIN)).isoformat(),
    }
    _sauver(post)
    return post


def _ton_mois_aujourdhui():
    """Une fois par semaine : le premier carrousel du dimanche."""
    m = _maintenant()
    return m.weekday() == 6 and m.hour < 11


def _departement_aujourdhui():
    """Une fois par semaine : le premier carrousel du mercredi."""
    m = _maintenant()
    return m.weekday() == 2 and m.hour < 11


def _serie_ce_soir():
    """Le carrousel du soir (après 16 h) est un épisode de la série « Les Français cachés »."""
    return _maintenant().hour >= 16


def commande_carrousel():
    from . import sheet
    if _departement_aujourdhui():
        titres = sheet.titres_sujets()
        episode = sum(1 for t in titres if str(t).startswith("Ton département"))
        c = contenu.rediger_departement(episode)
        fiche = {"statut": "Utilisé", "famille": "Ton département", "sujet": c["sujet"],
                 "couverture": c["couverture"]["texte"], "faits": "", "sources": ""}
        fiche["row"] = sheet.ajouter_sujet(fiche)
        post = creer_carrousel(fiche, fiche["row"], c=c)
        sheet.ajouter_post(post)
        return
    if _ton_mois_aujourdhui():
        titres = sheet.titres_sujets()
        c = contenu.rediger_ton_mois(titres)
        fiche = {"statut": "Utilisé", "famille": "Ton mois", "sujet": f"Ton mois = {c.get('theme', '')}",
                 "couverture": c["couverture"]["texte"], "faits": "", "sources": ""}
        fiche["row"] = sheet.ajouter_sujet(fiche)
        post = creer_carrousel(fiche, fiche["row"], c=c)
        sheet.ajouter_post(post)
        return
    if _serie_ce_soir():
        try:
            return _episode_serie(sheet)
        except Exception as e:  # si la série échoue, on publie quand même un carrousel de la banque
            print(f"Série « Les Français cachés » impossible ({e}) : carrousel de la banque à la place.")
    _carrousel_banque(sheet)


def _episode_serie(sheet):
    titres = sheet.titres_sujets()
    n = sum(1 for t in titres if str(t).startswith("[Les Français cachés #")) + 1
    sujet = None
    # 1) d'abord les épisodes préparés et vérifiés (data/francais_caches.json)
    try:
        with open(os.path.join(RACINE, "data", "francais_caches.json"), encoding="utf-8") as f:
            prets = json.load(f)
        deja = " ".join(str(t) for t in titres)
        sujet = next((dict(x) for x in prets if x["sujet"] not in deja), None)
    except Exception as e:
        print(f"Banque de la série illisible : {e}")
    # 2) sinon, Gemini en trouve un nouveau
    for essai in range(3 if not sujet else 0):
        try:
            sujet = contenu.proposer_sujet(titres, famille=contenu.FAMILLES[0][0])
            if sujet.get("sujet") and sujet.get("faits"):
                break
        except Exception as e:
            print(f"Proposition de sujet ratée (essai {essai + 1}) : {e}")
            sujet = None
    if not sujet:
        raise RuntimeError("aucun sujet trouvé")
    if True:
        sujet["sujet"] = f"[Les Français cachés #{n}] " + sujet.get("sujet", "")
        sujet["statut"] = "Utilisé"
        sujet["row"] = sheet.ajouter_sujet(sujet)
        sujet["etiquette"] = f"LES FRANÇAIS CACHÉS #{n}"
        sujet["prefixe_legende"] = f"Les Français cachés · épisode {n}\n\n"
        post = creer_carrousel(sujet, sujet["row"])
        post["legende"] = post["legende"].replace("👉 Abonne-toi", f"👉 Épisode {n + 1} demain soir. Abonne-toi", 1)
        sheet.ajouter_post(post)


def _carrousel_banque(sheet):
    sujet = sheet.prendre_sujet()
    if not sujet:
        print("Banque de sujets vide : proposition automatique d'un sujet.")
        sujet = contenu.proposer_sujet(sheet.titres_sujets())
        sujet["statut"] = "Auto"
        sujet["row"] = sheet.ajouter_sujet(sujet)
    try:
        post = creer_carrousel(sujet, sujet.get("row"))
        sheet.ajouter_post(post)
        sheet.marquer_sujet(sujet.get("row"), "Utilisé")
    except Exception:
        sheet.marquer_sujet(sujet.get("row"), "Disponible")  # on le remet dans la banque
        raise


def commande_actu():
    from . import sheet
    articles = contenu.actualites(sheet.liens_utilises())
    if not articles:
        print("Aucune actualité trouvée.")
        return
    a = contenu.rediger_actu(articles)
    idx = a.get("index", -1)
    if not isinstance(idx, int) or not 0 <= idx < len(articles):
        print("Aucune actualité adaptée aujourd'hui (garde-fou).")
        return
    art = articles[idx]
    ok, rapport = contenu.verifier([a["titre"]], f"Actualité : {art['titre']} ({art['source']})")

    photo = None
    cands = images.candidats(a.get("image", ""))
    ch = contenu.choisir_images([a.get("image", "")], [cands], a["titre"])[0]
    credits = []
    if ch:
        try:
            photo = images.telecharger(ch["url"])
            credits.append(ch["credit"])
        except Exception:
            photo = None
    photo = photo or images.fond_uni()

    post_id = _id("actu")
    img = render.slide_actu(photo, a["titre"], a.get("rouges"), credit=credits[0] if credits else None)
    render.enregistrer(img, os.path.join(_dossier(post_id), "01.jpg"))
    post = {
        "id": post_id, "type": "Actu", "titre": a["titre"],
        "images": [_url(post_id, "01.jpg")],
        "legende": _legende(a.get("legende", ""), a.get("hashtags", ""), credits),
        "verification_ok": ok, "verification": rapport,
        "lien_source": f"{art['lien']} | {art['titre']} {a['titre']}", "ligne_sujet": None,
        "publication_prevue": (_maintenant() + dt.timedelta(minutes=C.DELAI_AVANT_PUBLICATION_MIN)).isoformat(),
    }
    _sauver(post)
    sheet.ajouter_post(post)


def commande_notifier():
    from . import sheet
    if not os.path.exists(OUT):
        print("Rien à notifier.")
        return
    with open(OUT, encoding="utf-8") as f:
        post = json.load(f)
    sheet.ajouter_post(post)
    os.remove(OUT)
    print(f"Mail envoyé pour {post['id']}")


def commande_publier():
    from . import instagram, sheet
    for p in sheet.posts_a_publier():
        sheet.maj_post(p["id"], statut="Publication en cours")
        try:
            if p["type"] == "Carrousel":
                lien = instagram.publier_carrousel(p["images"], p["legende"])
            else:
                lien = instagram.publier_image(p["images"][0], p["legende"])
            sheet.maj_post(p["id"], statut="Publié", lien_instagram=lien, erreur="")
            print(f"Publié : {p['id']} → {lien}")
        except Exception as e:
            sheet.maj_post(p["id"], statut="Erreur", erreur=str(e)[:400])
            print(f"Erreur sur {p['id']} : {e}")


def commande_tester():
    from . import gemini, instagram, sheet
    print("Gemini :", gemini.texte("Réponds juste : OK")[:20])
    print("Sheet :", "OK" if sheet.titres_sujets() is not None else "?")
    print("Instagram :", instagram.tester())


def commande_demo():
    """Carrousel d'exemple, sans Sheet ni publication (utile pour régler la DA)."""
    fiche = {
        "sujet": "La France est le pays qui a le plus de fuseaux horaires au monde",
        "couverture": "Le pays qui a le plus de fuseaux horaires n'est ni la Russie ni les USA… c'est la France",
        "faits": "- La France compte 12 fuseaux horaires, 13 avec la Terre Adélie.\n"
                 "- La Russie en compte 11, les États-Unis 11 avec leurs territoires.\n"
                 "- La métropole n'a qu'un seul fuseau horaire.\n"
                 "- Territoires : Polynésie française, Nouvelle-Calédonie, Guyane, La Réunion, Saint-Pierre-et-Miquelon…\n"
                 "- En hiver, à midi à Paris, il est 22 h à Nouméa et 1 h à Tahiti.\n"
                 "- La France possède le 2e espace maritime du monde, après les États-Unis.",
        "sources": "https://www.timeanddate.com/time/country-with-the-most-time-zones.html",
    }
    creer_carrousel(fiche, sans_mise_en_ligne=True)


COMMANDES = {
    "carrousel": commande_carrousel, "actu": commande_actu, "notifier": commande_notifier,
    "publier": commande_publier, "tester": commande_tester, "demo": commande_demo,
    "prolonger-jeton": lambda: __import__("lmf.instagram", fromlist=["x"]).prolonger_jeton(),
}

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd not in COMMANDES:
        print(__doc__)
        sys.exit(1)
    try:
        COMMANDES[cmd]()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
