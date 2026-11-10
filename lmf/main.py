"""Point d'entrée : python -m lmf.main <commande>

Commandes :
  carrousel        crée un carrousel, le met en ligne et envoie le mail de validation
  actu             crée un post d'actualité en une image (même chose)
  actu_carrousel   raconte une actu locale insolite en carrousel de 7 slides
  preparer         prépare tous les posts et les prospects du lendemain, aperçus sur Telegram
  modifier         refait un post selon une consigne (MODIF_ID, MODIF_CONSIGNE)
  notifier         renvoie au Sheet le dernier post créé (si l'envoi du mail a échoué)
  publier          publie les posts arrivés à échéance (ou validés)
  prolonger-jeton  prolonge le jeton Instagram (chaque semaine)
  tester           vérifie Gemini, le Sheet et Instagram
  demo             génère un carrousel d'exemple sans rien publier
"""
import datetime as dt
import json
import os
import random
import sys
import traceback
import zoneinfo

from . import config as C
from . import contenu, images, render

PARIS = zoneinfo.ZoneInfo("Europe/Paris")
RACINE = os.path.join(os.path.dirname(__file__), "..")
OUT = os.path.join(RACINE, "out", "post.json")


# Quand on prépare les posts la veille : date « simulée » de création, heure de publication, pas de mail.
CONTEXTE = {"quand": None, "prevu": None, "silencieux": False}


def _maintenant():
    return dt.datetime.now(PARIS)


def _quand():
    return CONTEXTE["quand"] or _maintenant()


def _prevu():
    return CONTEXTE["prevu"] or (_maintenant() + dt.timedelta(minutes=C.DELAI_AVANT_PUBLICATION_MIN))


def _id(type_):
    base = (CONTEXTE["prevu"] or _maintenant()).strftime("%Y%m%d-%H%M")
    return f"{base}-{'c' if type_ == 'carrousel' else 'a'}{random.randint(10, 99)}"


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
    donnees = {"genre": "carrousel", "c": c,
               "fiche": {k: fiche.get(k) for k in ("sujet", "famille", "etiquette", "prefixe_legende", "sources",
                                                    "couverture", "faits") if fiche.get(k)}}
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
        "publication_prevue": _prevu().isoformat(),
        "silencieux": CONTEXTE["silencieux"], "donnees": json.dumps(donnees, ensure_ascii=False)[:45000],
    }
    _sauver(post)
    return post


def _ton_mois_aujourdhui():
    """Une fois par semaine : le premier carrousel du dimanche."""
    m = _quand()
    return m.weekday() == 6 and m.hour < 11


def _departement_aujourdhui():
    """Une fois par semaine : le premier carrousel du mercredi."""
    m = _quand()
    return m.weekday() == 2 and m.hour < 11


def _serie_ce_soir():
    """Le carrousel du soir (après 16 h) est un épisode de la série « Les Français cachés »."""
    return _quand().hour >= 16


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
    sheet.ajouter_post(creer_actu(a, art))


def creer_actu(a, art):
    ok, rapport = contenu.verifier([a["titre"]], f"Actualité : {art['titre']} ({art['source']})")
    photo, credits = _image_actu(a)
    post_id = _id("actu")
    img = render.slide_actu(photo, a["titre"], a.get("rouges"), credit=credits[0] if credits else None)
    render.enregistrer(img, os.path.join(_dossier(post_id), "01.jpg"))
    post = {
        "id": post_id, "type": "Actu", "titre": a["titre"],
        "images": [_url(post_id, "01.jpg")],
        "legende": _legende(a.get("legende", ""), a.get("hashtags", ""), credits),
        "verification_ok": ok, "verification": rapport,
        "lien_source": f"{art['lien']} | {art['titre']} {a['titre']}", "ligne_sujet": None,
        "publication_prevue": _prevu().isoformat(),
        "silencieux": CONTEXTE["silencieux"],
        "donnees": json.dumps({"genre": "actu", "a": a, "art": art}, ensure_ascii=False)[:45000],
    }
    _sauver(post)
    return post


def _image_actu(a):
    """Image de fond d'une actu : de la plus précise à la plus large, jamais de fond vide si on peut l'éviter."""
    requetes = [q for q in (a.get("images") or []) + [a.get("image", "")] if q]
    region = (a.get("etiquette") or "").split("·")[-1].strip()
    if region:
        requetes.append(f"{region} paysage")
    secours = None
    for q in requetes:
        try:
            cands = images.candidats(q)
        except Exception:
            continue
        if not cands:
            continue
        ch = contenu.choisir_images([q], [cands], a["titre"])[0]
        for c in ([ch] if ch else []):
            try:
                return images.telecharger(c["url"]), [c["credit"]]
            except Exception:
                pass
        if secours is None:
            secours = next((c for c in cands if "Wikimedia" in c.get("credit", "")), None)
    if secours:  # aucune image « parfaite » : on prend la meilleure photo Wikimedia du lieu plutôt qu'un fond vide
        try:
            return images.telecharger(secours["url"]), [secours["credit"]]
        except Exception:
            pass
    print("Aucune image trouvée pour l'actu : fond uni.")
    return images.fond_uni(), []


def commande_actu_carrousel():
    from . import sheet
    articles = contenu.actualites(sheet.liens_utilises())
    if not articles:
        print("Aucune actualité trouvée.")
        return
    art, c = contenu.rediger_actu_carrousel(articles)
    if not c:
        print("Aucune actu assez insolite : actu classique à la place.")
        return commande_actu()
    region = (c.get("region") or "").strip()
    fiche = {"sujet": art["titre"], "famille": "Actu carrousel",
             "etiquette": f"ACTU · {region.upper()}" if region else "ACTU",
             "sources": f"{art['lien']} | {art['titre']} {c['couverture']['texte']}"}
    post = creer_carrousel(fiche, c=c)
    sheet.ajouter_post(post)


# ---------------------------------------------------------------------------
# Préparation la veille + aperçus Telegram
# ---------------------------------------------------------------------------
TYPES = {"carrousel": "commande_carrousel", "actu": "commande_actu", "actu_carrousel": "commande_actu_carrousel"}


def apercu_telegram(e, post):
    """Envoie les slides puis un message avec les boutons Valider / Modifier / Supprimer."""
    from . import prospection as P
    if not e.get("telegram_chat"):
        return
    imgs = [u for u in post.get("images", []) if str(u).startswith("http")]
    if len(imgs) >= 2:
        P.tg("sendMediaGroup", chat_id=e["telegram_chat"], media=[{"type": "photo", "media": u} for u in imgs[:10]])
    elif imgs:
        P.tg("sendPhoto", chat_id=e["telegram_chat"], photo=imgs[0])
    heure = dt.datetime.fromisoformat(post["publication_prevue"]).astimezone(PARIS).strftime("%H:%M")
    doute = "" if post.get("verification_ok", True) else "\n⚠️ La relecture a un doute : il ne partira qu'avec « Valider »."
    texte = f"🕘 {heure} · {post['type']}\n{post['titre']}{doute}\n\n{post.get('legende', '')[:900]}"
    P.tg("sendMessage", chat_id=e["telegram_chat"], text=texte[:4000], disable_web_page_preview=True,
         reply_markup={"inline_keyboard": [[
             {"text": "✅ Valider", "callback_data": f"v:{post['id']}"},
             {"text": "✏️ Modifier", "callback_data": f"m:{post['id']}"},
             {"text": "🗑 Supprimer", "callback_data": f"s:{post['id']}"}]]})


def _creer(type_, prevu):
    """Crée un post du type demandé pour l'heure `prevu`, sans mail. Renvoie le post ou None."""
    CONTEXTE.update(quand=prevu - dt.timedelta(minutes=C.DELAI_AVANT_PUBLICATION_MIN), prevu=prevu, silencieux=True)
    if os.path.exists(OUT):
        os.remove(OUT)
    globals()[TYPES[type_]]()
    if not os.path.exists(OUT):
        return None
    with open(OUT, encoding="utf-8") as f:
        return json.load(f)


def commande_preparer():
    from . import prospection as P
    demain = (_maintenant() + dt.timedelta(days=1)).date()
    e = P.charger()
    bilan, rates = [], 0
    for heure, type_ in C.PLANNING:
        hh, mm = map(int, heure.split(":"))
        prevu = dt.datetime.combine(demain, dt.time(hh, mm), PARIS)
        try:
            post = None
            for essai in ([type_, type_] if type_ == "carrousel" else [type_, "actu"]):  # un 2e essai en secours
                try:
                    post = _creer(essai, prevu)
                except Exception as ex:
                    print(f"{essai} raté ({ex}), nouvel essai…")
                if post:
                    break
            if not post:
                raise RuntimeError("aucun sujet adapté trouvé")
            apercu_telegram(e, post)
            bilan.append(f"✅ {heure} · {post['type']}")
        except Exception as ex:
            traceback.print_exc()
            rates += 1
            bilan.append(f"❌ {heure} · {type_} : {str(ex)[:150]}")
    CONTEXTE.update(quand=None, prevu=None, silencieux=False)
    nb_prospects = 0
    if demain.weekday() < 5 and C.PROSPECTION_ACTIVE and e["active"] and demain.isoformat() not in e["jours_generes"]:
        try:
            C.CLE_2_EN_PREMIER = True
            avant = len(e["contacts"])
            P.generer_du_jour(e, jour=demain)
            nb_prospects = len(e["contacts"]) - avant
        except Exception as ex:
            traceback.print_exc()
            P.dire(e, f"⚠️ Prospects de demain ratés : {ex}")
    P.dire(e, f"📋 Demain ({demain.strftime('%d/%m')}) :\n" + "\n".join(bilan)
              + (f"\n📤 {nb_prospects} prospects" if nb_prospects else "")
              + "\n\nSans action de ta part, tout part aux heures prévues."
              + ("\nPour un post raté, lance « Carrousel » ou « Actualité » à la main dans GitHub si tu veux le remplacer."
                 if rates else ""))
    P.sauver(e)


def commande_modifier():
    from . import prospection as P
    from . import sheet
    pid = os.environ.get("MODIF_ID", "").strip()
    consigne = os.environ.get("MODIF_CONSIGNE", "").strip()
    e = P.charger()
    row = sheet.appel("getPost", id=pid)
    if not row:
        P.dire(e, f"Post {pid} introuvable.")
        return
    if row.get("statut") in ("Publié", "Publication en cours", "Supprimé", "Remplacé"):
        P.dire(e, f"Ce post est déjà « {row['statut']} », je ne le modifie pas.")
        return
    prevu = dt.datetime.fromisoformat(str(row["prevue"]).replace("Z", "+00:00")).astimezone(PARIS)
    CONTEXTE.update(quand=prevu - dt.timedelta(minutes=C.DELAI_AVANT_PUBLICATION_MIN), prevu=prevu, silencieux=True)
    try:
        d = json.loads(row.get("donnees") or "{}")
    except ValueError:
        d = {}
    post = None
    if d:
        r = contenu.modifier_contenu(d, consigne)
        if not r.get("nouveau_sujet") and r.get("contenu"):
            if d["genre"] == "carrousel":
                fiche = d.get("fiche", {})
                post = creer_carrousel(fiche, c=r["contenu"])
                if str(fiche.get("etiquette", "")).startswith("LES FRANÇAIS CACHÉS #"):
                    n = int(fiche["etiquette"].split("#")[-1])
                    post["legende"] = post["legende"].replace("👉 Abonne-toi", f"👉 Épisode {n + 1} demain soir. Abonne-toi", 1)
            else:
                post = creer_actu(r["contenu"], d["art"])
            sheet.ajouter_post(post)
    if not post:  # nouveau sujet demandé (ou pas de données) : on refait un post du même type
        type_ = "actu" if row.get("type") == "Actu" else (
            "actu_carrousel" if d.get("fiche", {}).get("famille") == "Actu carrousel" else "carrousel")
        post = _creer(type_, prevu)
    if not post:
        P.dire(e, "Je n'ai pas réussi à refaire ce post. L'ancien reste prévu : supprime-le si tu n'en veux pas.")
        return
    sheet.maj_post(pid, statut="Remplacé")
    P.dire(e, "✏️ Voici la nouvelle version (l'ancienne est annulée) :")
    apercu_telegram(e, post)
    P.sauver(e)


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
    "actu_carrousel": commande_actu_carrousel, "preparer": commande_preparer, "modifier": commande_modifier,
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
