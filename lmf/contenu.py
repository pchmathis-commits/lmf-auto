"""Rédaction des carrousels et des actus, avec garde-fous."""
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
- À ÉVITER (ça a fait des flops) : les sujets lointains et explicatifs (« comment X est devenu… », « pourquoi la France
  envoie des fusées… »), les couvertures qui donnent déjà la réponse, le ton scolaire.
Style : phrases courtes, percutantes, faciles à lire sur téléphone, en français impeccable.
Jamais de vulgarité, jamais de parti pris politique, jamais d'emoji dans les slides."""

# Familles en rotation (poids = fréquence relative quand le programme choisit lui-même un sujet).
# Classées d'après les abonnés gagnés par post sur 180 jours (Poubelle 6 100, Bic 3 700, eau du robinet 1 900…).
FAMILLES = [
    ("Le héros français méconnu derrière ton quotidien : UNE personne précise (inventeur, savant, ingénieur, préfet…) "
     "grâce à qui un objet, un geste ou un confort de tous les jours existe", 4),
    ("La langue française : un mot, une expression ou un usage du français que tout le monde connaît, son origine "
     "surprenante, ou l'influence cachée du français dans le monde (« Pourquoi dit-on… ? », « Ce mot français que… »)", 4),
    ("Le quotidien caché : un objet, un geste ou une habitude de tous les jours dont l'origine est française", 2),
    ("Une règle ou une loi française étonnante, toujours en vigueur ou jamais abolie", 1),
    ("Un paradoxe sur la France qui paraît faux mais qui est vrai", 1),
    ("La France face au monde ou derrière quelque chose de célèbre à l'étranger", 1),
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
{{"couverture": {{"texte": "...", "rouges": ["..."], "image": "...", "portrait": false}},
  "slides": [{{"texte": "...", "rouges": ["..."], "image": "..."}}],
  "question_finale": {{"texte": "...", "rouges": ["..."]}},
  "legende": "...", "hashtags": "#... #..."}}"""
    contenu = gemini.json_(prompt, temperature=0.7)
    contenu["slides"] = contenu.get("slides", [])[:n]
    if len(contenu["slides"]) < n:
        raise ValueError(f"Gemini a rendu {len(contenu['slides'])} slides au lieu de {n}")
    return contenu


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
    return gemini.json_(prompt, temperature=0.9, recherche=True)


# Série hebdomadaire « Ton département = … » : un épisode = une région (8 départements max), un thème.
LOTS_DEPARTEMENTS = [
    ("Bretagne", "22 Côtes-d'Armor, 29 Finistère, 35 Ille-et-Vilaine, 56 Morbihan"),
    ("Normandie", "14 Calvados, 27 Eure, 50 Manche, 61 Orne, 76 Seine-Maritime"),
    ("Hauts-de-France", "02 Aisne, 59 Nord, 60 Oise, 62 Pas-de-Calais, 80 Somme"),
    ("Île-de-France", "75 Paris, 77 Seine-et-Marne, 78 Yvelines, 91 Essonne, 92 Hauts-de-Seine, 93 Seine-Saint-Denis, 94 Val-de-Marne, 95 Val-d'Oise"),
    ("Grand Est (1)", "08 Ardennes, 10 Aube, 51 Marne, 52 Haute-Marne, 54 Meurthe-et-Moselle"),
    ("Grand Est (2)", "55 Meuse, 57 Moselle, 67 Bas-Rhin, 68 Haut-Rhin, 88 Vosges"),
    ("Bourgogne-Franche-Comté", "21 Côte-d'Or, 25 Doubs, 39 Jura, 58 Nièvre, 70 Haute-Saône, 71 Saône-et-Loire, 89 Yonne, 90 Territoire de Belfort"),
    ("Centre-Val de Loire", "18 Cher, 28 Eure-et-Loir, 36 Indre, 37 Indre-et-Loire, 41 Loir-et-Cher, 45 Loiret"),
    ("Pays de la Loire", "44 Loire-Atlantique, 49 Maine-et-Loire, 53 Mayenne, 72 Sarthe, 85 Vendée"),
    ("Nouvelle-Aquitaine (1)", "16 Charente, 17 Charente-Maritime, 19 Corrèze, 23 Creuse, 24 Dordogne, 33 Gironde"),
    ("Nouvelle-Aquitaine (2)", "40 Landes, 47 Lot-et-Garonne, 64 Pyrénées-Atlantiques, 79 Deux-Sèvres, 86 Vienne, 87 Haute-Vienne"),
    ("Occitanie (1)", "09 Ariège, 11 Aude, 12 Aveyron, 30 Gard, 31 Haute-Garonne, 32 Gers, 34 Hérault"),
    ("Occitanie (2)", "46 Lot, 48 Lozère, 65 Hautes-Pyrénées, 66 Pyrénées-Orientales, 81 Tarn, 82 Tarn-et-Garonne"),
    ("Auvergne-Rhône-Alpes (1)", "01 Ain, 03 Allier, 07 Ardèche, 15 Cantal, 26 Drôme, 38 Isère"),
    ("Auvergne-Rhône-Alpes (2)", "42 Loire, 43 Haute-Loire, 63 Puy-de-Dôme, 69 Rhône, 73 Savoie, 74 Haute-Savoie"),
    ("Provence-Alpes-Côte d'Azur et Corse", "04 Alpes-de-Haute-Provence, 05 Hautes-Alpes, 06 Alpes-Maritimes, 13 Bouches-du-Rhône, 83 Var, 84 Vaucluse, 2A Corse-du-Sud, 2B Haute-Corse"),
    ("Outre-mer", "971 Guadeloupe, 972 Martinique, 973 Guyane, 974 La Réunion, 976 Mayotte"),
]
THEMES_DEPARTEMENT = ["sa spécialité culinaire", "son personnage célèbre", "son lieu à voir absolument",
                      "son record ou sa bizarrerie"]


def rediger_departement(episode):
    """Épisode n de la série : une région, un thème, une slide par département."""
    region, depts = LOTS_DEPARTEMENTS[episode % len(LOTS_DEPARTEMENTS)]
    theme = THEMES_DEPARTEMENT[(episode // len(LOTS_DEPARTEMENTS)) % len(THEMES_DEPARTEMENT)]
    region_nom = region.split(" (")[0]
    prompt = f"""{STYLE}

Écris l'épisode {episode + 1} de la série « TON DÉPARTEMENT = … » (format série, fait pour être partagé entre amis).
Région : {region_nom}. Départements (numéro et nom, à reprendre tels quels) : {depts}.
Thème de l'épisode : pour chaque département, {theme}. Un élément réel, connu et vérifiable par département.

- Couverture (12 mots max) : donne envie de chercher son département, sans donner les réponses.
  Exemple de ton : « Ton département en {region_nom} a {theme}… tu le connais ? »
- Puis UNE slide par département, dans l'ordre des numéros, au format « 29 · FINISTÈRE : X » suivi d'une courte phrase
  (20 mots max au total). Le mot rouge = X.
- Image de chaque slide : une requête Wikimedia Commons de X (ou du département si X n'est pas photographiable).
- Question finale : « Et ton département, il a quoi ? »
- Légende : 2 phrases, puis « Identifie quelqu'un de ton département 👇 », puis « Épisode {episode + 1} : abonne-toi pour ne
  pas rater le tien. »

Réponds uniquement en JSON :
{{"couverture": {{"texte": "...", "rouges": ["..."], "image": "...", "portrait": false}},
  "slides": [{{"texte": "...", "rouges": ["..."], "image": "..."}}],
  "question_finale": {{"texte": "Et ton département, il a quoi ?", "rouges": ["département"]}},
  "legende": "...", "hashtags": "#... #..."}}"""
    contenu = gemini.json_(prompt, temperature=0.6)
    contenu["slides"] = contenu.get("slides", [])[:8]
    if len(contenu["slides"]) < 3:
        raise ValueError("Épisode département incomplet")
    contenu["sujet"] = f"Ton département – {region_nom} – {theme} (épisode {episode + 1})"
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
def actualites(liens_deja_utilises):
    articles, vus = [], set(liens_deja_utilises)
    for q in C.REQUETES_ACTU:
        url = f"https://news.google.com/rss/search?q={quote(q + ' when:1d')}&hl=fr&gl=FR&ceid=FR:fr"
        try:
            racine = ET.fromstring(requests.get(url, timeout=60).content)
        except Exception:
            continue
        for item in racine.iter("item"):
            lien = item.findtext("link", "")
            if lien in vus:
                continue
            vus.add(lien)
            articles.append({
                "titre": item.findtext("title", ""),
                "lien": lien,
                "source": item.findtext("source", ""),
                "resume": re.sub(r"<[^>]+>", " ", item.findtext("description", ""))[:300],
            })
    return articles[:40]


def rediger_actu(articles):
    liste = "\n".join(f"{i}. {a['titre']} ({a['source']}) — {a['resume']}" for i, a in enumerate(articles))
    prompt = f"""{STYLE}

Voici des actualités françaises récentes. Choisis LA plus intéressante pour notre audience :
une découverte, un patrimoine, un lieu, une tradition, une histoire positive ou insolite liée à un territoire.
Interdit : {", ".join(C.SUJETS_INTERDITS)}. Si aucune ne convient, réponds {{"index": -1}}.

{liste}

Puis rédige un post en une seule image :
- étiquette : « Actu · » + région ou ville (ex. « Actu · Bretagne »)
- titre : une phrase de 15 à 30 mots, en minuscules normales (pas tout en majuscules), factuelle,
  uniquement ce que dit l'article, avec 1 à 3 mots ou chiffres importants à mettre en rouge
- requête d'image Wikimedia Commons du LIEU concerné (jamais une photo de presse)
- légende : 2 à 3 phrases qui résument l'info, puis « Source : <média> ».

Réponds uniquement en JSON :
{{"index": 0, "etiquette": "...", "titre": "...", "rouges": ["..."], "image": "...", "legende": "...", "hashtags": "#..."}}"""
    return gemini.json_(prompt, temperature=0.5)
