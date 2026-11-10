"""Rédaction des carrousels et des actus, avec garde-fous."""
import json
import re
import xml.etree.ElementTree as ET
from urllib.parse import quote

import requests

from . import config as C
from . import gemini

STYLE = """Tu écris pour Le Média Français, un compte Instagram de 210 000 abonnés (80 % de 18-34 ans)
qui raconte l'histoire et le patrimoine de la France en carrousels.

LA FORMULE QUI MARCHE (tirée des posts du compte à 500 000 vues et plus) :
- Partir de quelque chose que le lecteur connaît ou utilise : un objet, un geste, un mot, une habitude,
  une chose célèbre dans le monde. Lui révéler l'origine française cachée ou l'histoire derrière.
- Couverture : ne JAMAIS nommer le sujet. Utiliser « ce Français », « cet homme », « ce geste », « cet objet »,
  « un truc que tu utilises encore »… La réponse arrive dans les slides, jamais sur la couverture.
- Parler au lecteur (« tu », « vous ») et créer un léger choc : « c'est une loi », « interdit en France »,
  « il n'a jamais été aboli », « tu l'utilises tous les jours ».
- Exemples de couvertures qui ont cartonné (pour le TON uniquement, ne reprends jamais ces sujets) :
  « Ce Français a inventé l'objet le plus vendu de l'histoire » ; « Pourquoi dit-on que le roi d'Espagne est français ? » ;
  « Le pire embouteillage en France a créé un truc que tu utilises encore » ; « Ce geste que les serveurs font sans vous
  le dire… c'est une loi » ; « Le président est co-prince d'Andorre ».
- CE QUI FAIT LE PLUS S'ABONNER (abonnés gagnés par post) : la langue française (« Ces mots français qui n'existent
  dans aucune autre langue », « Quand un avion est en détresse, le pilote crie… en français », « Pourquoi dit-on allô ? »)
  et un héros français méconnu montré en portrait, avec une couverture du type
  « Grâce à lui, … », « L'homme dont le nom est devenu… », « Ce Français a inventé… ». Quand le sujet est une personne,
  la couverture parle de lui sans le nommer et son image est son portrait.
- PUBLIC : 80 % d'hommes de 18 à 34 ans. Ce qui les accroche : marques et objets célèbres, automobile, sport,
  géopolitique, armée, records, rivalité France / grandes puissances.
- LE NOM DU SUJET EST OBLIGATOIRE DANS LE POST : seule la couverture garde le mystère. La personne, le lieu ou l'objet
  est nommé clairement dès la slide 2 ou 3 (en rouge). Quand le sujet est une femme, elle est nommée et mise en avant
  (jamais « une personne », « un scientifique » pour la désigner).
- L'IMAGE DE COUVERTURE NE DOIT PAS DÉVOILER LA RÉPONSE : si la réponse est un monument, un tableau, un objet ou une
  marque célèbre et reconnaissable, la couverture montre autre chose (le Français méconnu, un tableau d'époque, un détail).
- À ÉVITER (ça a fait des flops) : les sujets lointains et explicatifs (« comment X est devenu… », « pourquoi la France
  envoie des fusées… »), les couvertures qui donnent déjà la réponse, le ton scolaire.
Style : phrases courtes, percutantes, faciles à lire sur téléphone, en français impeccable.
Jamais de vulgarité, jamais de parti pris politique, jamais d'emoji dans les slides."""

# Familles en rotation (poids = fréquence relative quand le programme choisit lui-même un sujet).
# Classées d'après les abonnés gagnés par post sur 180 jours (Poubelle 6 100, Bic 3 700, eau du robinet 1 900…).
FAMILLES = [
    ("Les Français cachés : un Français méconnu derrière une chose célèbre dans le monde entier (une marque, une ville, "
     "une compétition sportive, une institution, une invention) — c'est le format qui gagne le plus d'abonnés", 5),
    ("La France face aux grandes puissances : une décision, une rivalité ou un record où la France tient tête au monde", 3),
    ("Le héros français méconnu derrière ton quotidien : une personne précise grâce à qui un objet ou un geste existe", 3),
    ("La langue française : un mot ou une expression que tout le monde connaît et son origine surprenante", 2),
    ("Une règle ou une loi française étonnante, toujours en vigueur", 1),
]

THEMES_TON_MOIS = ["personnage historique français", "château français", "ville française", "plat régional français",
                   "roi ou reine de France", "invention française", "monument français", "région française",
                   "écrivain français", "village français", "fromage français", "île française"]


def famille_du_jour(n):
    """Choisit une famille selon une rotation pondérée (n = numéro du créneau)."""
    # répartit chaque famille régulièrement dans la roue (pas deux fois la même d'affilée)
    places = sorted(((k + 0.5) / poids, i) for i, (f, poids) in enumerate(FAMILLES) for k in range(poids))
    roue = [FAMILLES[i][0] for _, i in places]
    return roue[n % len(roue)]


# ---------------------------------------------------------------------------
# Carrousels
# ---------------------------------------------------------------------------
def rediger_carrousel(fiche):
    n = C.SLIDES_PAR_CARROUSEL - 2
    prompt = f"""{STYLE}

Rédige un carrousel de {C.SLIDES_PAR_CARROUSEL} slides sur ce sujet.

SUJET : {fiche.get('sujet')}
COUVERTURE SUGGÉRÉE : {fiche.get('couverture', '')}
FAITS VÉRIFIÉS (tu ne dois utiliser QUE ces faits, aucun autre chiffre, nom ou date) :
{fiche.get('faits')}

Règles :
- Couverture : 6 à 12 mots MAXIMUM (4 lignes à l'écran au plus), une seule idée choc qui garde la réponse secrète.
  Exemples de longueur : « Ce Français a inventé l'objet le plus vendu de l'histoire » ; « Ce geste des serveurs… c'est une loi ».
- Puis exactement {n} slides de texte : une idée par slide, {C.MOTS_MAX_PAR_SLIDE} mots maximum, qui se suivent comme une histoire.
- La dernière slide de texte conclut avec une chute marquante.
- Pour chaque slide, 1 ou 2 mots importants à mettre en rouge (copiés exactement depuis le texte).
- Pour chaque slide, une requête d'image pour Wikimedia Commons : concrète (nom d'un personnage, d'un lieu, d'un tableau,
  d'une carte), en anglais si c'est plus courant, sans mots abstraits. Chaque slide a une requête DIFFÉRENTE
  (jamais deux fois la même), pour que chaque slide ait sa propre photo.
- Images : jamais d'animal, d'objet générique ou de photo d'illustration « stock ». Toujours le vrai lieu, la vraie
  personne, le vrai objet ou un document d'époque.
- Si le sujet tourne autour d'une personne précise : l'image de la COUVERTURE est son portrait (« Prénom Nom portrait »)
  et "portrait" vaut true. Sinon "portrait" vaut false.
- Une question finale courte pour faire réagir en commentaire (ex. « Et toi, tu le savais ? »).
- Une légende Instagram de 2 à 4 phrases qui donne envie de lire, puis « Envoie ce post à quelqu'un qui… ».
- 5 hashtags pertinents.

Réponds uniquement en JSON :
{{"nom_sujet": "le nom exact de la personne, du lieu ou de l'objet dont parle le post",
  "couverture": {{"texte": "...", "rouges": ["..."], "image": "...", "portrait": false}},
  "slides": [{{"texte": "...", "rouges": ["..."], "image": "..."}}],
  "question_finale": {{"texte": "...", "rouges": ["..."]}},
  "legende": "...", "hashtags": "#... #..."}}"""
    contenu = gemini.json_(prompt, temperature=0.7)
    contenu["slides"] = contenu.get("slides", [])[:n]
    if len(contenu["slides"]) < n:
        raise ValueError(f"Gemini a rendu {len(contenu['slides'])} slides au lieu de {n}")
    return contenu


def _normal(t):
    import unicodedata
    t = unicodedata.normalize("NFD", (t or "").lower())
    return "".join(ch for ch in t if unicodedata.category(ch) != "Mn")


REPONSE_DONNEE = re.compile(r"(c['’]est (la|le|l['’]|les|un|une) )|(\bc['’]est\b.*$)", re.I)


def controler_carrousel(c):
    """Garde-fous : la couverture ne donne pas la réponse ; le nom du sujet apparaît bien dans les slides."""
    nom = (c.get("nom_sujet") or "").strip()
    couv = c["couverture"]["texte"]
    mots_nom = [m for m in re.findall(r"[\wÀ-ÿ'-]{4,}", nom)]
    revele = bool(re.search(r"c['’]est (la france|lui|elle|la|le|l['’])", couv, re.I)) or any(
        _normal(m) in _normal(couv) for m in mots_nom if m[:1].isupper())
    if revele:
        try:
            nouveau = gemini.texte(
                "Réécris cette accroche de couverture Instagram en 12 mots maximum pour qu'elle garde le MYSTÈRE : "
                f"ne cite pas « {nom} », ne donne pas la réponse, finis par un suspense (« … »). "
                "Réponds uniquement avec la phrase.\nAccroche : " + couv, temperature=0.5).strip().strip('"«» ')
            if 3 <= len(nouveau.split()) <= 14 and not any(_normal(m) in _normal(nouveau) for m in mots_nom if m[:1].isupper()):
                c["couverture"]["texte"] = nouveau
                c["couverture"]["rouges"] = [w for w in c["couverture"].get("rouges", []) if _normal(w) in _normal(nouveau)]
        except Exception:
            pass
    if nom and mots_nom:
        cle = max(mots_nom, key=len)
        if not any(_normal(cle) in _normal(sl["texte"]) for sl in c["slides"]):
            i = 1 if len(c["slides"]) > 1 else 0
            t = c["slides"][i]["texte"]
            c["slides"][i]["texte"] = f"{nom} : " + (t[:1].lower() + t[1:] if t[:2].istitle() else t)
            c["slides"][i]["rouges"] = list(c["slides"][i].get("rouges", [])) + [nom]
    return c


def verifier(textes, contexte):
    """Garde-fou : vérification de chaque affirmation par recherche Google.

    Renvoie (ok: bool, rapport: str). Au moindre doute, ok = False.
    """
    if not C.VERIFICATION_AUTO:
        return True, "Vérification automatique désactivée : relis le post avant l'heure de publication."
    lignes = "\n".join(f"{i + 1}. {t}" for i, t in enumerate(textes))
    prompt = f"""Tu es vérificateur de faits pour un média d'histoire. Contexte : {contexte}

Vérifie CHAQUE affirmation ci-dessous avec une recherche Google (dates, chiffres, noms, lieux, liens de cause).
{lignes}

Pour chaque ligne, réponds exactement : numéro | OK ou DOUTE ou FAUX | justification courte.
- OK seulement si des sources fiables confirment clairement.
- DOUTE si c'est discuté, approximatif, ou une anecdote non sourcée présentée comme un fait.
- FAUX si c'est contredit.
Termine par une dernière ligne : VERDICT: OK (tout est OK) ou VERDICT: A_VERIFIER."""
    try:
        rapport = gemini.texte(prompt, recherche=True, temperature=0.1)
    except gemini.ErreurGemini as e:
        return False, f"Vérification impossible : {e}"
    problemes = [l for l in rapport.splitlines() if re.search(r"\|\s*(DOUTE|FAUX)\s*\|", l)]
    ok = "VERDICT: OK" in rapport.upper().replace(" :", ":") and not problemes
    resume = "Tout est confirmé." if ok else ("\n".join(problemes) or rapport[-500:])
    return ok, resume


def proposer_sujet(titres_existants, famille=None):
    """Quand la banque de sujets est vide : propose un nouveau sujet sourcé."""
    deja = "\n".join(f"- {t}" for t in titres_existants[-150:])
    famille = famille or famille_du_jour(len(titres_existants))
    prompt = f"""{STYLE}

Propose UN nouveau sujet de carrousel dans cette famille : {famille}.
Il doit être surprenant, vérifiable, sans polémique, et différent de ces sujets déjà traités :
{deja}

Sujets interdits : {", ".join(C.SUJETS_INTERDITS)}.
Utilise la recherche Google pour rassembler 8 à 12 faits précis et vérifiés, avec leurs sources (URL).

Réponds uniquement en JSON :
{{"famille": "...", "sujet": "...", "couverture": "...", "faits": "- fait 1\\n- fait 2 ...", "sources": "url1 ; url2"}}"""
    try:
        return gemini.json_(prompt, temperature=0.9, recherche=True)
    except gemini.ErreurGemini:
        # quota de recherche Google épuisé : on demande des faits très établis, sans recherche
        return gemini.json_(prompt.replace("Utilise la recherche Google pour rassembler",
                                           "Sans recherche, rassemble uniquement des faits très connus et certains :"),
                            temperature=0.6)


# Série hebdomadaire « Ton département = … » : un épisode = une région (8 départements max), un thème.
THEMES_REGION = ["sa spécialité culinaire culte", "son personnage célèbre", "son monument incontournable",
                 "son record ou sa bizarrerie", "son mot ou son expression typique", "sa marque ou son produit célèbre"]
REGIONS = ("Hauts-de-France, Normandie, Bretagne, Île-de-France, Grand Est, Pays de la Loire, Centre-Val de Loire, "
           "Bourgogne-Franche-Comté, Nouvelle-Aquitaine, Auvergne-Rhône-Alpes, Occitanie, Provence-Alpes-Côte d'Azur, "
           "Corse, Outre-mer")


def rediger_departement(episode):
    """Série hebdomadaire « Ta région = … » : toute la France dans un seul carrousel (chacun y trouve son coin)."""
    theme = THEMES_REGION[episode % len(THEMES_REGION)]
    prompt = f"""{STYLE}

Écris l'épisode {episode + 1} de la série « TA RÉGION = … » : pour chaque région de France, {theme}.
Régions, dans cet ordre : {REGIONS}.
Un élément réel, connu et vérifiable par région, nommé précisément.

- Couverture (10 mots max) : donne envie de chercher sa région, sans donner de réponse. Ex. de ton : « Chaque région
  a {theme}… tu connais la tienne ? »
- Puis exactement 8 slides : 7 slides de 2 régions chacune, au format « BRETAGNE : X. NORMANDIE : Y. » (16 mots max),
  les mots rouges = les deux réponses ; et une 8e slide de conclusion qui pose une question (« Laquelle manque ? »).
- Image de chaque slide : une requête Wikimedia Commons du premier élément de la slide.
- Question finale : « Et dans ta région, on dit quoi ? » ou adaptée au thème.
- Légende : 2 phrases, puis « Identifie quelqu'un de ta région 👇 », puis « Épisode {episode + 1} : abonne-toi pour la suite. »

Réponds uniquement en JSON :
{{"nom_sujet": "", "couverture": {{"texte": "...", "rouges": ["..."], "image": "map of France regions", "portrait": false}},
  "slides": [{{"texte": "...", "rouges": ["..."], "image": "..."}}],
  "question_finale": {{"texte": "...", "rouges": ["..."]}},
  "legende": "...", "hashtags": "#... #..."}}"""
    contenu = gemini.json_(prompt, temperature=0.6)
    contenu["slides"] = contenu.get("slides", [])[:8]
    if len(contenu["slides"]) < 6:
        raise ValueError("Épisode « Ta région » incomplet")
    contenu["sujet"] = f"Ton département – Ta région = {theme} (épisode {episode + 1})"
    return contenu


def rediger_ton_mois(titres_existants):
    """Format « Ton mois de naissance = ton … » : 12 réponses, 2 par slide."""
    deja = "\n".join(f"- {t}" for t in titres_existants[-150:])
    prompt = f"""{STYLE}

Crée un carrousel « TON MOIS DE NAISSANCE = TON … » (format jeu, fait pour être partagé entre amis).
Choisis UN thème parmi : {", ".join(THEMES_TON_MOIS)}, qui ne figure pas déjà ici :
{deja}

Pour chacun des 12 mois, associe un élément réel et connu de ce thème (un nom précis, pas d'invention).
Regroupe-les par 2 : 6 slides (janvier-février, mars-avril, etc.), au format « JANVIER : X. FÉVRIER : Y. »
Couverture : « TON MOIS = TON … » (5 mots maximum). Pour chaque slide, le mot rouge = les deux réponses.
Image de chaque slide : une requête Wikimedia Commons pour le PREMIER élément de la slide.
Légende : 2 phrases, puis « Identifie quelqu'un et dis-nous ce que tu as eu 👇 ».

Réponds uniquement en JSON :
{{"theme": "...", "couverture": {{"texte": "...", "rouges": ["..."], "image": "..."}},
  "slides": [{{"texte": "...", "rouges": ["..."], "image": "..."}}],
  "question_finale": {{"texte": "Et toi, tu as eu quoi ?", "rouges": ["quoi"]}},
  "legende": "...", "hashtags": "#... #..."}}"""
    contenu = gemini.json_(prompt, temperature=0.9)
    contenu["slides"] = contenu.get("slides", [])[:6]
    if len(contenu["slides"]) < 6:
        raise ValueError("Format Ton mois incomplet")
    return contenu


# ---------------------------------------------------------------------------
# Images
# ---------------------------------------------------------------------------
def choisir_images(requetes, candidats_par_slide, sujet):
    """Demande à Gemini de choisir, pour chaque slide, l'image la plus pertinente."""
    blocs = []
    for i, (req, cands) in enumerate(zip(requetes, candidats_par_slide)):
        options = "\n".join(f"   {j}: {c['titre']} — {c['description'][:120]}" for j, c in enumerate(cands)) or "   (aucune)"
        blocs.append(f"Slide {i} (recherche « {req} ») :\n{options}")
    prompt = f"""Sujet du carrousel : {sujet}
Pour chaque slide, choisis l'image qui illustre le mieux le texte (personnage, lieu ou objet exact).
Réponds -1 si aucune ne convient (image hors sujet, animal, photo d'illustration générique, anachronique, logo, document illisible). Dans le doute, -1.
{chr(10).join(blocs)}

Réponds uniquement en JSON : {{"choix": [index_slide0, index_slide1, ...]}}"""
    try:
        choix = gemini.json_(prompt, temperature=0.1, modele=C.GEMINI_MODELE_LEGER).get("choix", [])
    except Exception:
        choix = []
    resultat = []
    for i, cands in enumerate(candidats_par_slide):
        j = choix[i] if i < len(choix) and isinstance(choix[i], int) else (0 if cands else -1)
        resultat.append(cands[j] if 0 <= j < len(cands) else None)
    return resultat


# ---------------------------------------------------------------------------
# Actualité locale
# ---------------------------------------------------------------------------
def _mots(t):
    return {m for m in re.findall(r"[a-zà-ÿ]{5,}", (t or "").lower())}


def actualites(liens_deja_utilises):
    """Articles du jour, sans ceux déjà utilisés — ni le même lien, ni la même histoire reprise par un autre média."""
    vus, titres_vus = set(), []
    for x in liens_deja_utilises:
        lien, _, titre = str(x).partition(" | ")
        vus.add(lien.strip())
        if titre:
            titres_vus.append(_mots(titre))
    articles = []
    for q in C.REQUETES_ACTU:
        url = f"https://news.google.com/rss/search?q={quote(q + ' when:1d')}&hl=fr&gl=FR&ceid=FR:fr"
        try:
            racine = ET.fromstring(requests.get(url, timeout=60).content)
        except Exception:
            continue
        for item in racine.iter("item"):
            lien = item.findtext("link", "")
            titre = item.findtext("title", "")
            if lien in vus:
                continue
            m = _mots(titre)
            if any(len(m & t) >= 3 or (m and len(m & t) / len(m) >= 0.4) for t in titres_vus):
                continue  # même histoire déjà traitée
            vus.add(lien)
            titres_vus.append(m)
            articles.append({
                "titre": titre,
                "lien": lien,
                "source": item.findtext("source", ""),
                "resume": re.sub(r"<[^>]+>", " ", item.findtext("description", ""))[:300],
            })
    return articles[:40]


def rediger_actu(articles):
    liste = "\n".join(f"{i}. {a['titre']} ({a['source']}) — {a['resume']}" for i, a in enumerate(articles))
    prompt = f"""{STYLE}

Voici des actualités françaises récentes. Choisis LA plus INSOLITE pour notre audience (hommes 18-34 ans) :
une découverte surprenante, un record, une histoire étonnante ou un clin d'œil France / monde
(exemples qui ont marché : un temple romain découvert en creusant pour un observatoire ; un artiste qui s'affiche à
Times Square pour un concert dans un village de la Sarthe).
À ÉVITER (flops) : annonces de travaux, restaurations, anniversaires d'institutions, inscriptions administratives,
sujets morbides. Mieux vaut répondre -1 qu'une actu banale.
Interdit : {", ".join(C.SUJETS_INTERDITS)}. Si aucune ne convient, réponds {{"index": -1}}.

{liste}

Puis rédige un post en une seule image :
- étiquette : « Actu · » + région ou ville (ex. « Actu · Bretagne »)
- titre : une phrase de 15 à 30 mots, avec les majuscules normales du français (majuscule en début de phrase et aux
  noms propres : Touraine, Charles Perrault…), pas tout en majuscules, factuelle,
  uniquement ce que dit l'article, avec 1 à 3 mots ou chiffres importants à mettre en rouge
- 3 requêtes d'image Wikimedia Commons, de la plus précise à la plus large (jamais une photo de presse) :
  1) le LIEU ou l'OBJET exact (ex. « Temple de Mercure puy de Dôme ») ;
  2) la commune ou le site (ex. « Puy de Dôme summit ») ;
  3) un paysage ou monument emblématique du département ou de la région (ex. « Chaîne des Puys landscape »)
- légende : 2 à 3 phrases qui résument l'info, puis « Source : <média> ».

Réponds uniquement en JSON :
{{"index": 0, "etiquette": "...", "titre": "...", "rouges": ["..."], "images": ["précise", "commune", "région"],
  "legende": "...", "hashtags": "#..."}}"""
    return gemini.json_(prompt, temperature=0.5)


# ---------------------------------------------------------------------------
# Actu racontée en carrousel
# ---------------------------------------------------------------------------
def rediger_actu_carrousel(articles):
    """Choisit l'actu locale la plus insolite et la raconte en 6 slides avec suspense."""
    liste = "\n".join(f"{i}. {a['titre']} ({a['source']}) — {a['resume']}" for i, a in enumerate(articles))
    choix = gemini.json_(f"""{STYLE}

Voici des actualités françaises récentes. Choisis LA plus INSOLITE et LOCALE (un village, une ville, un
personnage, une découverte, un record, une histoire décalée) qui peut se raconter comme une histoire avec du
suspense. À ÉVITER : travaux, restaurations, anniversaires d'institutions, sujets administratifs ou morbides.
Interdit : {", ".join(C.SUJETS_INTERDITS)}. Si aucune ne convient, réponds {{"index": -1}}.

{liste}

Réponds en JSON : {{"index": 0, "recherche": "requête pour retrouver cette histoire sur le web"}}""", temperature=0.4)
    idx = choix.get("index", -1)
    if not isinstance(idx, int) or not 0 <= idx < len(articles):
        return None, None
    art = articles[idx]
    try:
        faits = gemini.texte(f"""Rassemble les faits précis et vérifiés de cette actualité française (qui, où, quand,
comment, chiffres, contexte historique ou local, réactions) : « {art['titre']} » ({art['source']}).
Recherche : {choix.get('recherche', art['titre'])}. Liste à puces, 8 à 14 faits, uniquement ce que disent les sources.""",
                             recherche=True, temperature=0.2)
    except gemini.ErreurGemini:
        faits = art["resume"]  # sans recherche : on s'en tient au résumé de l'article
    c = gemini.json_(f"""{STYLE}

Raconte cette actualité en carrousel Instagram de 7 slides : couverture + 5 slides de récit + slide finale.
ACTU : {art['titre']} ({art['source']})
FAITS (n'utilise QUE ceux-là) :
{faits}

Règles :
- Couverture : 6 à 12 mots, une accroche qui intrigue SANS donner la fin de l'histoire.
- 5 slides de récit, {C.MOTS_MAX_PAR_SLIDE} mots maximum chacune, qui font monter la curiosité slide après slide
  (contexte → l'élément surprenant → le détail fou → le rebondissement → la chute). Le lieu exact est cité dès la slide 1.
- 1 ou 2 mots en rouge par slide (copiés exactement du texte).
- Une requête d'image Wikimedia Commons DIFFÉRENTE par slide : le lieu exact, la commune, un monument ou paysage du
  département (jamais une photo de presse, jamais d'animal ni d'image générique).
- Slide finale : un appel à s'abonner court et donnant-donnant, ex. « Abonne-toi : chaque jour, une pépite de France ».
- Région ou ville pour l'étiquette (ex. « Bretagne »).
- Légende : 2 à 3 phrases, puis « Source : {art['source']} ».

Réponds uniquement en JSON :
{{"nom_sujet": "lieu ou personne principale", "region": "...",
  "couverture": {{"texte": "...", "rouges": ["..."], "image": "...", "portrait": false}},
  "slides": [{{"texte": "...", "rouges": ["..."], "image": "..."}}],
  "question_finale": {{"texte": "...", "rouges": ["..."]}},
  "legende": "...", "hashtags": "#... #..."}}""", temperature=0.6)
    c["slides"] = c.get("slides", [])[:5]
    if len(c["slides"]) < 4:
        raise ValueError("Récit trop court")
    return art, c


# ---------------------------------------------------------------------------
# Modification d'un post préparé, d'après la consigne de Mathis (bouton « Modifier » sur Telegram)
# ---------------------------------------------------------------------------
def modifier_contenu(donnees, consigne):
    contenu_actuel = donnees.get("c") if donnees.get("genre") == "carrousel" else donnees.get("a")
    prompt = f"""{STYLE}

Voici le contenu JSON d'un post Instagram du Média Français, prêt à être publié :
{json.dumps(contenu_actuel, ensure_ascii=False)[:12000]}

Mathis n'est pas satisfait. Sa consigne : « {consigne} »

- Si la consigne demande clairement un AUTRE SUJET (« change de sujet », « autre actu », « pas intéressant »…),
  réponds {{"nouveau_sujet": true}}.
- Sinon, applique la consigne et renvoie le contenu modifié dans EXACTEMENT la même structure JSON, en gardant tel quel
  tout ce que la consigne ne concerne pas. Mêmes règles : couverture de 6 à 12 mots, {C.MOTS_MAX_PAR_SLIDE} mots
  maximum par slide, mots en rouge copiés exactement du texte, requêtes d'image Wikimedia concrètes et différentes
  (si la consigne parle d'une image, change la requête d'image concernée). N'invente aucun fait.

Réponds uniquement en JSON : {{"nouveau_sujet": false, "contenu": {{...}}}}"""
    r = gemini.json_(prompt, temperature=0.5)
    if donnees.get("genre") == "carrousel" and r.get("contenu") and not r["contenu"].get("slides"):
        r["contenu"] = None  # réponse incomplète : on refera un post neuf
    return r
