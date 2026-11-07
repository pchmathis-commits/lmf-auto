"""Rendu des slides dans la DA du Média Français.

- Photo en pleine page (recadrée au format 4:5)
- Couverture : bandeaux blancs, texte bleu marine
- Slides suivantes : bandeaux bleu marine, texte blanc
- Mots importants en rouge, quel que soit le bandeau
- Flèche courbe et logo pin en bas à droite
- Slide finale : fond blanc, question + appel à s'abonner
"""
import os
import re
import urllib.request

from PIL import Image, ImageDraw, ImageFont, ImageFilter

from . import config as C

ASSETS = os.path.join(os.path.dirname(__file__), "..", "assets")


# ---------------------------------------------------------------------------
# Police
# ---------------------------------------------------------------------------
def _assurer_police():
    if not os.path.exists(C.POLICE_FICHIER):
        os.makedirs(os.path.dirname(C.POLICE_FICHIER), exist_ok=True)
        urllib.request.urlretrieve(C.POLICE_URL, C.POLICE_FICHIER)


_cache_polices = {}


def police(taille, poids=None):
    poids = poids or C.POIDS_POLICE
    cle = (taille, poids)
    if cle in _cache_polices:
        return _cache_polices[cle]
    chemin = os.environ.get("LMF_POLICE_LOCALE")  # pour tester sans réseau
    if not chemin:
        _assurer_police()
        chemin = C.POLICE_FICHIER
    f = ImageFont.truetype(chemin, taille)
    try:  # police variable : on règle la graisse
        f.set_variation_by_axes([poids])
    except Exception:
        pass
    _cache_polices[cle] = f
    return f


# ---------------------------------------------------------------------------
# Outils
# ---------------------------------------------------------------------------
def recadrer(image, largeur=C.LARGEUR, hauteur=C.HAUTEUR):
    image = image.convert("RGB")
    ratio = max(largeur / image.width, hauteur / image.height)
    image = image.resize((int(image.width * ratio) + 1, int(image.height * ratio) + 1), Image.LANCZOS)
    x = (image.width - largeur) // 2
    y = (image.height - hauteur) // 3  # garde plutôt le haut (visages, monuments)
    return image.crop((x, y, x + largeur, y + hauteur))


def _normaliser(mot):
    mot = mot.upper()
    return re.sub(r"[^\wÀ-Ÿ'’-]", "", mot)


def _mots_rouges(mots_rouges):
    rouges = set()
    for expr in mots_rouges or []:
        for m in expr.split():
            rouges.add(_normaliser(m))
    return rouges


def _couper_lignes(texte, f, largeur_max):
    mots = []
    for m in texte.upper().split():  # « ? », « ! », « : » ne restent jamais seuls en début de ligne
        if mots and m in ("?", "!", ":", ";", "»"):
            mots[-1] += " " + m
        else:
            mots.append(m)
    lignes, courante = [], []
    for mot in mots:
        essai = " ".join(courante + [mot])
        if courante and f.getlength(essai) > largeur_max:
            lignes.append(courante)
            courante = [mot]
        else:
            courante.append(mot)
    if courante:
        lignes.append(courante)
    return lignes


def dessiner_bandeaux(img, texte, mots_rouges, fond, couleur_texte, taille, x, y_bas, largeur_max):
    """Dessine le texte en bandeaux (un bandeau par ligne), ancré en bas à y_bas."""
    f = police(taille)
    rouges = _mots_rouges(mots_rouges)
    lignes = _couper_lignes(texte, f, largeur_max)
    pad_x, pad_y = int(taille * 0.28), int(taille * 0.16)
    hauteur_ligne = int(taille * 1.22)
    espace = f.getlength(" ")
    pas = hauteur_ligne + 2 * pad_y + C.ESPACE_ENTRE_BANDEAUX  # bandeaux séparés
    y = y_bas - len(lignes) * pas
    d = ImageDraw.Draw(img)
    for ligne in lignes:
        texte_ligne = " ".join(ligne)
        larg = f.getlength(texte_ligne)
        d.rectangle([x, y, x + larg + 2 * pad_x, y + hauteur_ligne + 2 * pad_y], fill=fond)
        cx = x + pad_x
        for mot in ligne:
            couleur = C.ROUGE if _normaliser(mot) in rouges else couleur_texte
            d.text((cx, y + pad_y + int(taille * 0.08)), mot, font=f, fill=couleur)
            cx += f.getlength(mot) + espace
        y += pas
    return img


def _coller(img, chemin, largeur, position):
    a = Image.open(chemin).convert("RGBA")
    a = a.resize((largeur, int(a.height * largeur / a.width)), Image.LANCZOS)
    x, y = position
    if x < 0:
        x = img.width + x - a.width
    if y < 0:
        y = img.height + y - a.height
    img.paste(a, (x, y), a)


def _decor(img, fleche=True):
    # logo pin en bas à droite, flèche juste au-dessus
    _coller(img, os.path.join(ASSETS, "logo_pin.png"), 72, (-38, -36))
    if fleche:
        haut_logo = int(400 * 72 / 348)
        fleche_perso = os.path.join(ASSETS, "fleche.png")  # ta flèche, prioritaire si elle existe
        chemin = fleche_perso if os.path.exists(fleche_perso) else os.path.join(ASSETS, "arrow.png")
        _coller(img, chemin, 150, (-30, -(36 + haut_logo + 6)))


def _assombrir_bas(img):
    """Léger dégradé en bas pour que les bandeaux ressortent sur toutes les photos."""
    grad = Image.new("L", (1, img.height))
    for y in range(img.height):
        grad.putpixel((0, y), int(max(0, (y - img.height * 0.55) / (img.height * 0.45)) * 110))
    grad = grad.resize(img.size)
    noir = Image.new("RGB", img.size, (0, 0, 0))
    return Image.composite(noir, img, grad)


# ---------------------------------------------------------------------------
# Slides
# ---------------------------------------------------------------------------
def _etiquette(img, texte):
    f = police(30)
    d = ImageDraw.Draw(img)
    larg = f.getlength(texte)
    d.rectangle([C.MARGE_GAUCHE, 70, C.MARGE_GAUCHE + larg + 36, 70 + 56], fill=C.ROUGE)
    d.text((C.MARGE_GAUCHE + 18, 79), texte, font=f, fill=C.BLANC)


def slide_couverture(photo, texte, mots_rouges, etiquette=None):
    img = _assombrir_bas(recadrer(photo))
    if etiquette:
        _etiquette(img, etiquette)
    dessiner_bandeaux(img, texte, mots_rouges, C.BLANC, C.BLEU_MARINE,
                      C.TAILLE_TEXTE_COUVERTURE, C.MARGE_GAUCHE, C.HAUTEUR - 190, C.LARGEUR_TEXTE_MAX)
    _decor(img)
    return img


def slide_texte(photo, texte, mots_rouges):
    img = _assombrir_bas(recadrer(photo))
    dessiner_bandeaux(img, texte, mots_rouges, C.BLEU_MARINE, C.BLANC,
                      C.TAILLE_TEXTE_SLIDE, C.MARGE_GAUCHE, C.HAUTEUR - 190, C.LARGEUR_TEXTE_MAX)
    _decor(img)
    return img


def _bandeaux_inclines(question, largeur_max, taille):
    """Bandeaux centrés et inclinés, en alternance bleu marine / rouge / bleu marine, texte blanc."""
    f = police(taille)
    lignes = _couper_lignes(question, f, largeur_max)
    while len(lignes) > 3 and taille > 40:
        taille -= 4
        f = police(taille)
        lignes = _couper_lignes(question, f, largeur_max)
    # équilibre les lignes (évite une ligne très longue suivie d'une très courte)
    n = len(lignes)
    while largeur_max > 400:
        essai = _couper_lignes(question, f, largeur_max - 20)
        if len(essai) > n:
            break
        largeur_max -= 20
        lignes = essai
    pad_x, pad_y = int(taille * 0.3), int(taille * 0.14)
    h = int(taille * 1.2) + 2 * pad_y
    calque = Image.new("RGBA", (C.LARGEUR, len(lignes) * (h + 8) + 40), (0, 0, 0, 0))
    d = ImageDraw.Draw(calque)
    y = 20
    for i, ligne in enumerate(lignes):
        txt = " ".join(ligne)
        larg = f.getlength(txt)
        x = (C.LARGEUR - larg) / 2 + (i - (len(lignes) - 1) / 2) * 24  # léger décalage, comme sur ta slide
        fond = C.ROUGE if i % 2 == 1 else C.BLEU_MARINE
        d.rectangle([x - pad_x, y, x + larg + pad_x, y + h], fill=fond)
        d.text((x, y + pad_y + int(taille * 0.06)), txt, font=f, fill=C.BLANC)
        y += h + 8
    return calque.rotate(C.ANGLE_BANDEAUX_FIN, expand=True, resample=Image.BICUBIC)


def slide_finale(question, mots_rouges):
    # 1) Ton fond de slide de fin (sans les bandeaux du haut) : on y ajoute la question.
    for nom in ("slide_finale_fond.png", "slide_finale_fond.jpg", "slide_finale_fond.jpeg"):
        chemin = os.path.join(ASSETS, nom)
        if os.path.exists(chemin):
            img = recadrer(Image.open(chemin)).convert("RGBA")
            calque = _bandeaux_inclines(question, 900, C.TAILLE_TEXTE_FIN)
            x = (C.LARGEUR - calque.width) // 2
            y = C.CENTRE_BANDEAUX_FIN - calque.height // 2
            img.paste(calque, (x, y), calque)
            return img.convert("RGB")
    # 2) Ta slide de fin complète, utilisée telle quelle.
    for nom in ("slide_finale.jpg", "slide_finale.jpeg", "slide_finale.png"):
        chemin = os.path.join(ASSETS, nom)
        if os.path.exists(chemin):
            return recadrer(Image.open(chemin))
    img = Image.new("RGB", (C.LARGEUR, C.HAUTEUR), (250, 250, 248))
    # léger quadrillage de points, comme sur les slides finales actuelles
    d = ImageDraw.Draw(img)
    for x in range(30, C.LARGEUR, 45):
        for y in range(30, C.HAUTEUR, 45):
            d.ellipse([x, y, x + 3, y + 3], fill=(225, 225, 228))
    dessiner_bandeaux(img, question, mots_rouges, C.BLEU_MARINE, C.BLANC, 50, 90, 560, 900)
    appel = f"REJOINS {C.ABONNES_AFFICHES} PASSIONNÉS PAR LEUR PAYS EN SUIVANT NOTRE MÉDIA INDÉPENDANT"
    dessiner_bandeaux(img, appel, ["PASSIONNÉS", "PAYS"], C.BLEU_MARINE, C.BLANC, 46, 90, 900, 900)
    _coller(img, os.path.join(ASSETS, "logo_pin.png"), 120, (C.LARGEUR // 2 - 60, 960))
    f = police(40)
    larg = f.getlength(C.COMPTE)
    d.text(((C.LARGEUR - larg) / 2, 1130), C.COMPTE, font=f, fill=C.BLEU_MARINE)
    return img


def _fondu_bas(img, couleur, debut=0.42):
    """Fondu vers une couleur sombre sur le bas de l'image (template Orion)."""
    h = img.height
    masque = Image.new("L", (1, h))
    for y in range(h):
        t = max(0.0, (y - h * debut) / (h * (1 - debut)))
        masque.putpixel((0, y), int(min(1.0, t * 1.35) ** 1.2 * 255))
    masque = masque.resize(img.size)
    return Image.composite(Image.new("RGB", img.size, couleur), img, masque)


def slide_actu(photo, titre, mots_rouges, etiquette=None, source=None, credit=None):
    """Post d'actualité en une slide : template Orion, bleu clair remplacé par le rouge."""
    img = _fondu_bas(recadrer(photo), C.FOND_ACTU)
    d = ImageDraw.Draw(img)
    f = police(C.TAILLE_TEXTE_ACTU, 700)
    rouges = _mots_rouges(mots_rouges)
    marge = 62
    mots = titre.split()
    lignes, courante = [], []
    for mot in mots:
        if courante and f.getlength(" ".join(courante + [mot])) > C.LARGEUR - 2 * marge:
            lignes.append(courante)
            courante = [mot]
        else:
            courante.append(mot)
    if courante:
        lignes.append(courante)
    interligne = int(C.TAILLE_TEXTE_ACTU * 1.2)
    y_barre = C.HAUTEUR - 170
    y = y_barre - 40 - len(lignes) * interligne
    espace = f.getlength(" ")
    for ligne in lignes:
        x = marge
        for mot in ligne:
            couleur = C.ROUGE if _normaliser(mot) in rouges else C.BLANC
            d.text((x, y), mot, font=f, fill=couleur)
            x += f.getlength(mot) + espace
        y += interligne
    d.rectangle([marge - 4, y_barre, marge + 52, y_barre + 9], fill=C.ROUGE)
    if credit:
        fc = police(17, 500)
        txt = f"Image : {credit}"[:110]
        d.text((C.LARGEUR - 34 - fc.getlength(txt), C.HAUTEUR - 46), txt, font=fc, fill=(200, 204, 214))
    return img


def enregistrer(img, chemin):
    os.makedirs(os.path.dirname(chemin), exist_ok=True)
    img.convert("RGB").save(chemin, "JPEG", quality=95, subsampling=0, optimize=True)  # texte net, rouge sans bavure
