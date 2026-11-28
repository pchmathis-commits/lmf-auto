"""Le Média Paris : un carrousel par jour, écrit à l'avance par Claude (data/paris_semaine.json).

Zéro appel Gemini : le texte est déjà vérifié. Le robot cherche les images exactes (Wikipédia), dessine les
slides dans la DA du groupe, les met en ligne et publie sur le compte Instagram Le Média Paris, puis prévient
sur Telegram avec le lien du post.
"""
import datetime as dt
import json
import os

from PIL import Image, ImageDraw

from . import config as C
from . import images, render, wiki
from . import instagram as IG

ICI = os.path.dirname(__file__)
FICHIER = os.path.join(ICI, "..", "data", "paris_semaine.json")
LOGO = os.path.join(ICI, "..", "assets", "logo_paris.png")
PARIS_TZ = dt.timezone(dt.timedelta(hours=1))   # heure d'hiver ; l'heure exacte vient du planning GitHub


def post_du_jour(jour=None):
    jour = jour or dt.datetime.now(PARIS_TZ).date().isoformat()
    try:
        with open(FICHIER, encoding="utf-8") as f:
            posts = json.load(f)["posts"]
    except Exception as ex:
        print(f"paris_semaine.json illisible ({ex})")
        return None
    return next((p for p in posts if p.get("date") == jour), None)


def _empreinte(im):
    g = im.convert("L").resize((16, 16))
    px = list(g.getdata())
    m = sum(px) / len(px)
    return [p > m for p in px]


def _photos(requetes):
    """Une photo nette et différente par slide : l'image officielle de l'article Wikipédia exact,
    sinon la 1re image Wikimedia Commons pour cette requête précise (jamais de banque d'images générique)."""
    photos, credits, empreintes, urls = [], [], [], set()

    def accepter(url, credit):
        if not url or url in urls:
            return None
        urls.add(url)
        try:
            im = images.telecharger(url)
        except Exception:
            return None
        if min(im.size) < 700:
            return None
        h = _empreinte(im)
        if any(sum(a != b for a, b in zip(h, x)) <= 25 for x in empreintes):
            return None
        empreintes.append(h)
        credits.append(credit)
        return im

    for i, req in enumerate(requetes):
        im = None
        exacte = wiki.image_exacte(req, "autre", verifier=False)
        if exacte:
            im = accepter(exacte["url"], exacte["credit"])
        for cand in ([] if im else images.wikimedia(req)):
            im = im or accepter(cand["url"], cand["credit"])
        if im is None and i == 0:
            raise RuntimeError(f"Pas de photo sûre pour la couverture « {req} » : post annulé")
        photos.append(im)
    return photos, credits


def slide_finale(question, rouges):
    img = Image.new("RGB", (C.LARGEUR, C.HAUTEUR), (250, 250, 248))
    d = ImageDraw.Draw(img)
    for x in range(30, C.LARGEUR, 45):
        for y in range(30, C.HAUTEUR, 45):
            d.ellipse([x, y, x + 3, y + 3], fill=(225, 225, 228))
    render.dessiner_bandeaux(img, question, rouges, C.BLEU_MARINE, C.BLANC, 50, 90, 560, 900)
    appel = "ABONNE-TOI POUR DÉCOUVRIR PARIS COMME TU NE L'AS JAMAIS VU"
    render.dessiner_bandeaux(img, appel, ["PARIS"], C.BLEU_MARINE, C.BLANC, 46, 90, 880, 900)
    if os.path.exists(LOGO):
        logo = Image.open(LOGO).convert("RGB").resize((220, 220))
        masque = Image.new("L", (220, 220), 0)
        ImageDraw.Draw(masque).ellipse((0, 0, 219, 219), fill=255)
        img.paste(logo, (C.LARGEUR // 2 - 110, 930), masque)
    f = render.police(40)
    larg = f.getlength(C.PARIS_COMPTE)
    d.text(((C.LARGEUR - larg) / 2, 1170), C.PARIS_COMPTE, font=f, fill=C.BLEU_MARINE)
    return img


def fabriquer(post, dossier):
    """Dessine les slides du post dans `dossier`. Renvoie (chemins des JPEG, crédits photo)."""
    os.makedirs(dossier, exist_ok=True)
    c = post["couverture"]
    requetes = [c["image"]] + [s["image"] for s in post["slides"]]
    photos, credits = _photos(requetes)
    secours = [p for p in photos if p is not None]
    slides = [render.slide_couverture(photos[0], c["texte"], c.get("rouges"), etiquette="PARIS")]
    for i, (photo, s) in enumerate(zip(photos[1:], post["slides"])):
        photo = photo or secours[(i + 1) % len(secours)]   # rare : une slide sans photo reprend une photo du post
        slides.append(render.slide_texte(photo, s["texte"], s.get("rouges")))
    q = post.get("question_finale") or {"texte": "Tu le savais ?", "rouges": ["savais"]}
    slides.append(slide_finale(q["texte"], q.get("rouges")))
    chemins = []
    for i, img in enumerate(slides, 1):
        chemin = os.path.join(dossier, f"{i:02d}.jpg")
        render.enregistrer(img, chemin)
        chemins.append(chemin)
    return chemins, credits


def legende(post, credits):
    texte = post.get("legende", "").strip()
    if post.get("hashtags"):
        texte += "\n\n" + post["hashtags"].strip()
    uniques = list(dict.fromkeys(c for c in credits if c))
    if uniques:
        texte += "\n\n📷 " + " · ".join(uniques)[:600]
    return texte[:2150]


def _telegram(texte):
    try:
        from . import prospection as P
        e = P.charger()
        if e.get("telegram_chat"):
            P.tg("sendMessage", chat_id=e["telegram_chat"], text=texte[:4000], disable_web_page_preview=False)
    except Exception as ex:
        print(f"Telegram : {ex}")


def publier_du_jour(jour=None, test=False):
    post = post_du_jour(jour)
    if not post and test:   # en test, on prend le premier post du fichier si rien n'est prévu aujourd'hui
        try:
            with open(FICHIER, encoding="utf-8") as f:
                post = json.load(f)["posts"][0]
        except Exception:
            post = None
    if not post:
        print("Le Média Paris : rien de prévu aujourd'hui dans paris_semaine.json")
        return None
    dossier = os.path.join(ICI, "..", "sortie", "paris", post["date"])
    chemins, credits = fabriquer(post, dossier)
    texte = legende(post, credits)
    if test:
        print(f"Test : {len(chemins)} slides dans {dossier}\n\n{texte}")
        return chemins
    urls = [images.mettre_en_ligne(ch) for ch in chemins]
    IG.utiliser_compte("paris")
    try:
        if IG.deja_publie(texte):
            print("Le Média Paris : déjà publié, on ne republie pas")
            return None
        lien = IG.publier_carrousel(urls, texte)
    finally:
        IG.utiliser_compte(None)
    _telegram(f"🗼 Le Média Paris : « {post['couverture']['texte']} » est en ligne\n{lien}")
    return lien
