"""Reels automatiques : script (Gemini, faits vérifiés) → images EXACTES (Wikipédia/Wikidata + contrôle visuel)
→ animation papier (lmf/reel.html, capturée par Chromium) → musique (assets/musique) → MP4.

Trois formats qui tournent dans la semaine : l'anecdote, une ville de France, le Français oublié.
Rien n'est publié sans passer par l'aperçu Telegram (mêmes boutons que les posts).
"""
import datetime as dt
import json
import os
import random
import re
import shutil
import subprocess

import requests

from . import config as C
from . import contenu, gemini, wiki

ICI = os.path.dirname(__file__)
RACINE = os.path.join(ICI, "..")
ETAT = os.path.join(RACINE, "data", "reels.json")
MUSIQUES = os.path.join(RACINE, "assets", "musique")
RELEASE = "reels"   # les vidéos sont stockées en pièces jointes d'une « release » GitHub du repo (gratuit)

FOND = {"base": "#c7cfdc", "grille": "carre", "pas": 72, "alpha": .12}   # option C validée le 21/11
# Lundi … dimanche
ROTATION = ["anecdote", "ville", "anecdote", "francais", "anecdote", "ville", "francais"]
FIN = {"anecdote": ["Une histoire de France", "*chaque jour*"],
       "ville": ["Une ville de France", "*chaque semaine*"],
       "francais": ["Les Français *oubliés*", "chaque semaine"]}

FORMATS = {
    "anecdote": """FORMAT « L'ANECDOTE » : une histoire vraie et surprenante de l'histoire de France, racontée comme un
mini-thriller : une accroche qui intrigue, 3 ou 4 rebondissements, puis la révélation. Thèmes qui marchent le
mieux chez nous : armée et guerre, Napoléon et les grands personnages, argent et sommes folles, secrets d'État,
inventions nées d'une guerre, records. Il faut au moins 2 personnes, lieux ou objets RÉELS et précis à montrer en
image (un portrait, un monument, un tableau, un objet célèbre).""",
    "ville": """FORMAT « UNE VILLE DE FRANCE » : une ville ou un village de France métropolitaine (pas déjà traité),
présenté par 4 ou 5 faits forts et peu connus (un événement historique marquant, un personnage né là, un record,
un monument, une curiosité). L'accroche ne nomme pas la ville ; la carte la révèle juste après. Il faut au moins 2
lieux ou monuments précis de cette ville qui ont un article Wikipédia (le monument, le quartier, le château…).""",
    "francais": """FORMAT « LE FRANÇAIS OUBLIÉ » : un Français ou une Française méconnu(e) derrière une chose célèbre dans
le monde entier (une invention, une ville, une marque, un exploit). L'accroche ne le nomme pas ; il est nommé à la
2e ou 3e scène, avec son portrait. Il faut son portrait (article Wikipédia de la personne) et au moins une autre
image précise (l'objet, le lieu, l'œuvre liée).""",
}


# ---------------------------------------------------------------------------
# État (sujets déjà faits, musiques récentes)
# ---------------------------------------------------------------------------
def charger():
    try:
        with open(ETAT, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"sujets": [], "musiques": []}


def sauver(etat):
    os.makedirs(os.path.dirname(ETAT), exist_ok=True)
    with open(ETAT, "w", encoding="utf-8") as f:
        json.dump(etat, f, ensure_ascii=False, indent=1)


def format_du_jour(jour):
    return ROTATION[jour.weekday()]


# ---------------------------------------------------------------------------
# 1. Le script
# ---------------------------------------------------------------------------
REGLES = """RÈGLES DU REEL (vidéo verticale de 25 à 32 secondes, SANS voix : uniquement du texte à l'écran et de la musique)
- 6 ou 7 scènes. Chaque scène = 2 à 4 lignes de texte COURTES (26 caractères maximum par ligne, espaces compris).
  Entoure 1 ou 2 mots importants par scène d'astérisques pour les mettre en rouge : « a été une *république* ».
- Scène 1 = l'ACCROCHE : elle NOMME TOUJOURS le sujet (la ville, la personne, l'objet, l'événement), dans le texte
  ET dans le "tag", et donne envie de rester (un secret, un chiffre fou, une guerre). Jamais « cette ville », « cet
  homme » sans dire lequel. Pas de formule usée (« que tu utilises tous les jours », « que le monde
  entier utilise »).
- La dernière scène donne la révélation ou le fait le plus fort. Le post donne TOUJOURS la réponse précise (nom,
  lieu, date, chiffre), jamais « une ville », « un homme » sans dire lequel.
- Types de scènes :
  · "photo" : une image RÉELLE. Mets dans "entite" le titre EXACT de l'article Wikipédia en français de ce qu'on
    montre (ex. « Pierre Charles L'Enfant », « Pont du Gard », « Château de Combourg », « Ferdinand de Lesseps »),
    et dans "nature" : personne, lieu, objet, oeuvre ou evenement. Jamais une notion générale (« guerre », « argent »).
  · "carte" : une carte de France avec un repère. "entite" = titre exact de l'article Wikipédia de la commune ou du
    lieu (France métropolitaine uniquement).
  · "chiffre" : un nombre tamponné en grand : "chiffre" = 9 caractères maximum (« 1534 », « 80 % », « 6 M€ »).
  · "texte" : du texte seul (pour une phrase forte sans image).
  Au moins 2 scènes "photo" (idéalement 3 ou 4), au moins 1 "chiffre".
- "tag" (facultatif) : petite étiquette en MAJUSCULES, 22 caractères max (un lieu, une date, un nom) : « AOÛT 1944 ».
- Uniquement des faits VRAIS et vérifiables, tirés des FAITS ci-dessous. Français impeccable, pas d'emoji.
- "legende" : 2 à 4 phrases qui racontent l'histoire en entier (avec les noms, dates et lieux), puis une question
  au lecteur. "hashtags" : 5 hashtags."""


def _faits(format_, etat, consigne="", sujet=""):
    deja = "\n".join(f"- {s}" for s in etat.get("sujets", [])[-120:]) or "(aucun)"
    demande = (f"Sujet imposé : {sujet}." if sujet else
               f"Choisis UN sujet fort, différent de ceux déjà faits :\n{deja}")
    return gemini.texte(f"""{contenu.STYLE}

{FORMATS[format_]}

{demande}
{('Consigne de Mathis : ' + consigne) if consigne else ''}
Sujets interdits : {", ".join(C.SUJETS_INTERDITS)} ; pas de politique.

Avec une recherche Google, rassemble les FAITS précis et vérifiés de ce sujet : noms complets, dates exactes, lieux
exacts (commune), chiffres exacts, et le titre exact des articles Wikipédia en français des personnes, lieux et
objets à montrer. Première ligne : « SUJET : … ». Puis une liste à puces. Puis « SOURCES : » et 2 ou 3 liens.""",
                        recherche=True, temperature=0.6)


def ecrire(format_, etat, consigne="", sujet=""):
    faits = _faits(format_, etat, consigne, sujet)
    s = gemini.json_(f"""{contenu.STYLE}

{FORMATS[format_]}

FAITS :
{faits}

{REGLES}
{('Consigne de Mathis pour cette version (prioritaire) : ' + consigne) if consigne else ''}

Réponds en JSON :
{{"sujet": "nom court du sujet", "scenes": [
  {{"type": "photo", "entite": "...", "nature": "lieu", "tag": "...", "texte": ["...", "..."]}},
  {{"type": "carte", "entite": "...", "tag": "...", "texte": ["..."]}},
  {{"type": "chiffre", "chiffre": "...", "tag": "...", "texte": ["..."]}},
  {{"type": "texte", "tag": "...", "texte": ["..."]}}],
 "legende": "...", "hashtags": "#...", "sources": "..."}}""", temperature=0.6)
    s["faits"] = faits
    return s


def modifier_script(script, consigne):
    """Applique une consigne de Mathis au script (scène 1 = la première scène, l'accroche)."""
    s = gemini.json_(f"""Voici le script JSON d'un Reel Instagram :
{json.dumps({k: script[k] for k in ("sujet", "scenes", "legende", "hashtags") if k in script}, ensure_ascii=False)}

Consigne de Mathis : {consigne}

Applique la consigne et UNIQUEMENT elle. Numérotation : « scène 1 » / « première » = la première scène de la liste
(l'accroche), « scène 2 » = la deuxième, etc. Ne change pas les "entite" (les images) des scènes dont la consigne ne
parle pas. {REGLES}
Réponds avec le script JSON complet, même structure.""", temperature=0.3)
    s["faits"] = script.get("faits", "")
    return s


# ---------------------------------------------------------------------------
# 2. Les images exactes et la mise en scène
# ---------------------------------------------------------------------------
def _ligne(t):
    return re.sub(r"\s+", " ", str(t)).strip()[:34]


def _duree(sc):
    mots = sum(len(str(l).split()) for l in sc.get("texte", []))
    return round(min(4.6, max(3.0, 1.9 + 0.17 * mots)), 2)


def preparer_scenes(script, dossier, cache=None):
    """Résout chaque image (ou transforme la scène en texte si on n'est pas sûr). Renvoie (scenes, credits, cache)."""
    cache = dict(cache or {})
    os.makedirs(os.path.join(dossier, "img"), exist_ok=True)
    scenes, credits = [], []
    for i, sc in enumerate(script.get("scenes", [])[:8]):
        sc = {k: v for k, v in sc.items() if v not in (None, "")}
        sc["texte"] = [_ligne(t) for t in sc.get("texte", []) if str(t).strip()][:4]
        if sc.get("tag"):
            sc["tag"] = str(sc["tag"]).upper()[:24]
        typ = sc.get("type")
        if typ == "photo":
            cle = f"{sc.get('entite')}|{sc.get('nature', 'lieu')}"
            img = cache.get(cle) if cle in cache else wiki.image_exacte(sc.get("entite", ""), sc.get("nature", "lieu"), verifier=False)
            cache[cle] = img
            if img:
                nom = f"img/s{i}.jpg"
                try:
                    data = requests.get(img["url"], headers=wiki.UA, timeout=90).content
                    with open(os.path.join(dossier, nom), "wb") as f:
                        f.write(data)
                    sc["image"] = nom
                    sc["trame"] = sc.get("nature") == "personne"   # portraits en trame bleu marine, lieux en couleur
                    credits.append(img.get("credit", ""))
                except Exception as ex:
                    print(f"Téléchargement raté ({ex})")
                    img = None
            if not img:
                sc["type"] = "texte"
                sc.setdefault("tag", str(sc.get("entite", "")).upper()[:24])
        elif typ == "carte":
            cle = f"geo|{sc.get('entite')}"
            geo = cache.get(cle) if cle in cache else wiki.coordonnees(sc.get("entite", ""))
            cache[cle] = geo
            if geo and -5.3 <= geo[0] <= 9.7 and 41.3 <= geo[1] <= 51.2:
                sc["lieu"] = list(geo)
                sc.setdefault("tag", str(sc.get("entite", "")).upper()[:24])
            else:
                sc["type"] = "texte"
        elif typ == "chiffre":
            sc["chiffre"] = str(sc.get("chiffre", ""))[:10]
            if not sc["chiffre"]:
                sc["type"] = "texte"
        else:
            sc["type"] = "texte"
        # mise en page
        if sc["type"] == "photo":
            sc.update(y=300, capY=1020, rot=random.choice([-2.5, -1.5, 2, 2.5]), pan=random.choice([-1, 1]))
        elif sc["type"] == "carte":
            sc["capY"] = 1260
        elif sc["type"] == "chiffre":
            sc.update(y=520, capY=1010)
        else:
            sc["capY"] = 880
        sc["dur"] = 3.4 if i == 0 else _duree(sc)
        scenes.append(sc)
    return scenes, credits, cache


# ---------------------------------------------------------------------------
# 3. Rendu vidéo
# ---------------------------------------------------------------------------
def _ffmpeg():
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return shutil.which("ffmpeg") or "ffmpeg"


def _police(dossier):
    from . import render
    os.makedirs(os.path.join(dossier, "fonts"), exist_ok=True)
    try:
        render._assurer_police()
        shutil.copy(C.POLICE_FICHIER, os.path.join(dossier, "fonts", "Montserrat.ttf"))
    except Exception as ex:
        print(f"Police Montserrat indisponible ({ex}) : police de secours")


# Ambiances : les noms utilisés dans les scripts renvoient aux dossiers de ta bibliothèque (assets/musique/<ambiance>/).
# Si le dossier n'existe pas ou est vide, on prend la musique générée du même nom (assets/musique/<nom>.mp3).
AMBIANCES = {"enquete": "mystere", "thriller": "mystere", "filature": "mystere", "nuit-blanche": "mystere",
             "piano-feutre": "emotion", "nostalgie": "emotion", "epique": "epique", "leger": "leger",
             "emotion": "emotion", "mystere": "mystere", "actu": "actu", "annees-80": "annees-80"}


def _musiques():
    """Toutes les musiques disponibles, en chemins relatifs sans « .mp3 » (ex. « epique/titre » ou « enquete »)."""
    tout = []
    for racine, _, fichiers in os.walk(MUSIQUES):
        for f in fichiers:
            if f.lower().endswith(".mp3"):
                tout.append(os.path.relpath(os.path.join(racine, f), MUSIQUES)[:-4].replace(os.sep, "/"))
    return sorted(tout)


def _catalogue():
    """assets/musique/catalogue.json : pistes avec ambiances, comptes, crédit, meilleur départ et volume."""
    try:
        with open(os.path.join(MUSIQUES, "catalogue.json"), encoding="utf-8") as f:
            pistes = json.load(f).get("pistes", [])
    except (OSError, ValueError):
        return {}
    return {p["fichier"]: p for p in pistes if os.path.exists(os.path.join(MUSIQUES, p["fichier"] + ".mp3"))}


def infos_musique(musique):
    return _catalogue().get(musique or "", {})


def choisir_musique(etat, demande=None, compte="lmf"):
    """1) catalogue filtré par ambiance + compte ; 2) dossier de l'ambiance ; 3) musiques générées à la racine."""
    cat = _catalogue()
    recentes = etat.get("musiques", [])[-3:]
    amb = AMBIANCES.get(demande, demande) if demande else None
    cands = []
    if cat:
        du_compte = [m for m, p in cat.items() if compte in p.get("comptes", [])]
        if amb:
            cands = [m for m in du_compte if amb in cat[m].get("ambiances", []) or demande in cat[m].get("ambiances", [])]
        if not cands:   # aucune ne correspond : musique de code (générée, sans crédit)
            cands = [m for m in du_compte if cat[m].get("genere")]
    if not cands:
        dispo = _musiques()
        if amb:
            cands = [m for m in dispo if m.startswith(amb + "/")] or [m for m in dispo if m == demande]
        cands = cands or [m for m in dispo if "/" not in m] or dispo
    choix = [m for m in cands if m not in recentes] or cands
    return random.choice(choix) if choix else None


def rendre(spec, dossier, sortie, musique=None):
    """Capture lmf/reel.html image par image (30 i/s) puis ajoute la musique. Renvoie la durée."""
    from playwright.sync_api import sync_playwright
    shutil.copy(os.path.join(ICI, "reel.html"), os.path.join(dossier, "reel.html"))
    shutil.copy(os.path.join(RACINE, "assets", "logo_pin.png"), os.path.join(dossier, "img", "logo_pin.png"))
    _police(dossier)
    ff = _ffmpeg()
    muette = os.path.join(dossier, "video.mp4")
    with sync_playwright() as p:
        b = p.chromium.launch(args=["--allow-file-access-from-files"])
        pg = b.new_page(viewport={"width": 1080, "height": 1920})
        pg.add_init_script("window.SPEC = " + json.dumps(spec, ensure_ascii=False) + ";")
        pg.goto("file://" + os.path.abspath(os.path.join(dossier, "reel.html")) + "?capture")
        pg.evaluate("window.ready")
        duree = pg.evaluate("window.DUR")
        proc = subprocess.Popen([ff, "-v", "error", "-y", "-f", "image2pipe", "-framerate", "30", "-i", "-",
                                 "-c:v", "libx264", "-crf", "17", "-preset", "slow", "-profile:v", "high",
                                 "-maxrate", "10M", "-bufsize", "20M", "-pix_fmt", "yuv420p", muette],
                                stdin=subprocess.PIPE)
        for i in range(round(duree * 30)):
            pg.evaluate(f"window.setT({i / 30})")
            proc.stdin.write(pg.screenshot(type="png"))   # sans perte : textes nets
        proc.stdin.close()
        proc.wait()
        b.close()
    fichier = os.path.join(MUSIQUES, f"{musique}.mp3") if musique else None
    if fichier and os.path.exists(fichier):
        info = infos_musique(musique)
        debut = info.get("debut", random.choice([8, 10, 12, 20, 30]))   # meilleur passage de 32 s, mesuré à l'analyse
        gain = info.get("volume", 1.6)
        subprocess.run([ff, "-v", "error", "-y", "-i", muette, "-ss", str(debut), "-i", fichier, "-map", "0:v", "-map", "1:a",
                        "-c:v", "copy", "-c:a", "aac", "-b:a", "160k",
                        "-af", f"volume={gain},afade=t=in:st=0:d=0.4,afade=t=out:st={max(0, duree - 1.5):.2f}:d=1.5",
                        "-shortest", "-movflags", "+faststart", sortie], check=True)
    else:
        shutil.copy(muette, sortie)
    return duree


# ---------------------------------------------------------------------------
# 4. Stockage (release GitHub) et aperçu Telegram
# ---------------------------------------------------------------------------
def _gh(*args, check=True):
    return subprocess.run(["gh", *args], capture_output=True, text=True, check=check)


def heberger(chemin):
    """Met la vidéo en pièce jointe de la release « reels » du repo et renvoie son adresse publique."""
    repo = os.environ.get("GITHUB_REPOSITORY")
    if not repo or not (os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")):
        return os.path.abspath(chemin)   # test en local
    if _gh("release", "view", RELEASE, "--repo", repo, check=False).returncode != 0:
        _gh("release", "create", RELEASE, "--repo", repo, "--title", "Reels (stockage automatique)",
            "--notes", "Vidéos préparées par le robot, supprimées automatiquement après 10 jours.", "--latest=false")
    _gh("release", "upload", RELEASE, chemin, "--clobber", "--repo", repo)
    return f"https://github.com/{repo}/releases/download/{RELEASE}/{os.path.basename(chemin)}"


def nettoyer(jours=10):
    repo = os.environ.get("GITHUB_REPOSITORY")
    if not repo:
        return
    try:
        r = _gh("release", "view", RELEASE, "--repo", repo, "--json", "assets", check=False)
        if r.returncode != 0:
            return
        limite = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=jours)
        for a in json.loads(r.stdout).get("assets", []):
            if dt.datetime.fromisoformat(a["createdAt"].replace("Z", "+00:00")) < limite:
                _gh("release", "delete-asset", RELEASE, a["name"], "--repo", repo, "-y", check=False)
    except Exception as ex:
        print(f"Nettoyage des vieilles vidéos impossible : {ex}")


def envoyer_video(chat, chemin_ou_url, legende, boutons):
    if not C.TELEGRAM_TOKEN:
        return
    url = f"https://api.telegram.org/bot{C.TELEGRAM_TOKEN}/sendVideo"
    donnees = {"chat_id": chat, "caption": legende[:1000], "supports_streaming": "true"}
    if boutons:
        donnees["reply_markup"] = json.dumps({"inline_keyboard": [boutons]})
    try:
        if os.path.exists(chemin_ou_url):
            with open(chemin_ou_url, "rb") as f:
                requests.post(url, data=donnees, files={"video": f}, timeout=180)
        else:
            requests.post(url, data={**donnees, "video": chemin_ou_url}, timeout=180)
    except Exception as ex:
        print(f"Telegram (vidéo) : {ex}")


# ---------------------------------------------------------------------------
# 5. Tout en un
# ---------------------------------------------------------------------------
SEMAINE = os.path.join(RACINE, "data", "reels_semaine.json")


def script_planifie(jour):
    """Le Reel écrit à l'avance pour ce jour (fichier data/reels_semaine.json, préparé chaque samedi), ou None."""
    try:
        with open(SEMAINE, encoding="utf-8") as f:
            for r in json.load(f):
                if r.get("date") == jour.isoformat():
                    return r
    except (OSError, ValueError) as ex:
        print(f"Pas de fichier de Reels de la semaine utilisable ({ex})")
    return None


def creer(post_id, prevu, format_=None, consigne="", donnees_avant=None):
    """Crée le Reel complet. Renvoie le dict « post » (même forme que les carrousels).
    Si un Reel est écrit à l'avance pour ce jour, on le monte tel quel : AUCUN appel à Gemini."""
    etat = charger()
    planifie = None if donnees_avant else script_planifie(prevu.date())
    if planifie:
        format_ = planifie.get("format") or format_
    format_ = format_ or (donnees_avant or {}).get("format") or format_du_jour(prevu)
    dossier = os.path.join(RACINE, "out", "reels", post_id)
    os.makedirs(dossier, exist_ok=True)
    cache = (donnees_avant or {}).get("cache", {})
    script, scenes, credits = None, [], []
    essais = []
    for essai in range(3):
        if planifie and essai == 0:
            script = planifie
        elif planifie:
            raise RuntimeError(f"Reel écrit à l'avance « {planifie.get('sujet')} » : seulement {nb_photos} image(s) "
                               "trouvée(s) sur Wikipédia, vérifie les titres des images dans reels_semaine.json")
        elif donnees_avant and essai == 0:
            script = modifier_script(donnees_avant["script"], consigne) if consigne else donnees_avant["script"]
        else:
            script = ecrire(format_, etat, consigne)
        scenes, credits, cache = preparer_scenes(script, dossier, cache)
        nb_photos = sum(1 for s in scenes if s["type"] == "photo")
        if nb_photos >= 2 and len(scenes) >= 5:
            break
        print(f"Reel « {script.get('sujet')} » : seulement {nb_photos} image(s) sûre(s), autre sujet…")
        essais.append(f"{script.get('sujet')} ({nb_photos} image)")
        donnees_avant = None
    else:
        raise RuntimeError("pas assez d'images sûres : " + " ; ".join(essais))
    scenes.append({"type": "fin", "dur": 2.8, "capY": 1100, "center": True, "texte": FIN[format_], "cta": "ABONNE-TOI"})
    musique = choisir_musique(etat, (planifie or {}).get("musique"))
    spec = {"fond": FOND, "scenes": scenes}
    sortie = os.path.join(dossier, f"{post_id}.mp4")
    duree = rendre(spec, dossier, sortie, musique)
    textes = [" ".join(s.get("texte", [])).replace("*", "") for s in scenes if s["type"] != "fin"]
    if planifie:
        ok, rapport = True, "Écrit et vérifié à l'avance (fichier des Reels de la semaine)."
    else:
        ok, rapport = contenu.verifier(textes + [script.get("legende", "")], f"Reel « {script.get('sujet')} »")
    legende = script.get("legende", "").strip()
    credits = sorted({c for c in credits if c})
    txt = (legende + "\n\n👉 Abonne-toi à " + C.COMPTE + " : chaque jour, une histoire de France que tu ne connais pas."
           + ("\n\n🎵 Musique : " + infos_musique(musique)["credit"] if infos_musique(musique).get("credit") else "")
           + ("\n\nCrédits images : " + " · ".join(credits) if credits else "")
           + "\n\n" + script.get("hashtags", "").strip())[:2200]
    url = heberger(sortie)
    etat["sujets"] = (etat.get("sujets", []) + [script.get("sujet", "")])[-300:]
    etat["musiques"] = (etat.get("musiques", []) + [musique])[-10:]
    sauver(etat)
    titre = " ".join(scenes[0].get("texte", [])).replace("*", "")
    print(f"Reel prêt : {script.get('sujet')} · {duree:.1f} s · musique {musique} · vérification {ok}")
    return {
        "id": post_id, "type": "Reel", "titre": f"🎬 {titre}", "images": [url], "legende": txt,
        "verification_ok": ok, "verification": rapport, "lien_source": script.get("sources", ""),
        "publication_prevue": prevu.isoformat(), "silencieux": True, "fichier_local": sortie,
        "donnees": json.dumps({"genre": "reel", "format": format_, "cache": cache,
                               "script": {**script, "faits": str(script.get("faits", ""))[:3000]}}, ensure_ascii=False),
    }
