"""Vidéos longues YouTube « Pourquoi… ? » (16:9, ~8 min, avec la vraie voix de Mathis).

Le circuit, étape par étape (workflow « Vidéo longue », lancé par l'horloge du Sheet ou par un bouton Telegram) :
  seance  : le dimanche vers 14 h 30. Écrit les scripts manquants (Gemini, clé GEMINI_KEY_YT) et envoie sur Telegram
            les 2 prochains épisodes, bloc par bloc. Mathis répond à chaque bloc par un vocal.
  voix    : lancé par le Sheet dès qu'un vocal arrive. Range chaque vocal dans son bloc ; épisode complet → montage.
  montage : voix nettoyée → sous-titres calés sur la voix (Whisper) → une image par phrase (Ken Burns, cartes chiffres,
            texte à l'écran) → musique de la bibliothèque en sourdine sous la voix → MP4 1080p → 3 miniatures →
            mise en ligne NON RÉPERTORIÉE → aperçu Telegram : choix de la miniature, « Programmer » ou « Publier ».
            Les 3 extraits verticaux (Shorts) sont fabriqués en même temps.
  bouton  : ym:<id>:<n> miniature n · yp:<id> programmer (prochain jeudi ou samedi 18 h) · yn:<id> publier maintenant.
            À la publication, les 3 Shorts sont programmés sur YouTube à J+1, J+2, J+3 (18 h) et envoyés sur Telegram.
  test    : fabrique un épisode avec une voix de synthèse (2 blocs), sans rien mettre en ligne (artefact GitHub).

Règles de contenu : la réponse est toujours donnée (pas de suite à attendre), aucun chiffre inventé, sources dans la
description, images libres de droits uniquement (Wikimedia Commons), sinon une carte texte (jamais une image fausse).
"""
import datetime as dt
import difflib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import unicodedata
import zoneinfo

import requests
from PIL import Image, ImageDraw, ImageFilter

from . import config as C
from . import gemini, images, render, wiki

RACINE = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
FICHIER = os.path.join(RACINE, "data", "longues.json")
SORTIE = os.path.join(RACINE, "out", "longues")
ASSETS = os.path.join(RACINE, "assets")
RELEASE = "longues"
PARIS = zoneinfo.ZoneInfo("Europe/Paris")
L, H = 1920, 1080
FPS = 30
ECART_BLOCS = 0.6          # silence entre deux blocs (s)
FIN_EN_PLUS = 5.0          # la carte de fin reste 5 s après la dernière phrase
CRENEAUX = [(3, 18), (5, 18)]   # jeudi 18 h et samedi 18 h (heure de Paris)
SEANCE_EPISODES = 2


# ---------------------------------------------------------------------------
# État
# ---------------------------------------------------------------------------
def charger():
    with open(FICHIER, encoding="utf-8") as f:
        return json.load(f)


def sauver(d):
    with open(FICHIER, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)


def episode(d, eid):
    return next((e for e in d["episodes"] if e["id"] == eid), None)


def _ff():
    return shutil.which("ffmpeg") or __import__("imageio_ffmpeg").get_ffmpeg_exe()


def _ffprobe_duree(chemin):
    r = subprocess.run([_ff(), "-nostdin", "-i", chemin], capture_output=True, text=True, stdin=subprocess.DEVNULL)
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", r.stderr)
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3)) if m else 0.0


def _run(args, **kw):
    if args and args[0] == _ff() and "-nostdin" not in args:
        args = [args[0], "-nostdin"] + list(args[1:])
    r = subprocess.run(args, capture_output=True, text=True, stdin=subprocess.DEVNULL, **kw)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg a échoué : {r.stderr[-800:]}")
    return r


# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------
def _chat():
    from . import prospection as P
    return P.charger().get("telegram_chat")


def tg(methode, **params):
    from . import prospection as P
    return P.tg(methode, **params)


def tg_fichier(methode, champ, chemin, **donnees):
    if not C.TELEGRAM_TOKEN or not _chat():
        print(f"(Telegram) {methode} {os.path.basename(chemin)}")
        return None
    donnees = {"chat_id": _chat(), **{k: (json.dumps(v) if isinstance(v, (dict, list)) else v) for k, v in donnees.items()}}
    try:
        with open(chemin, "rb") as f:
            r = requests.post(f"https://api.telegram.org/bot{C.TELEGRAM_TOKEN}/{methode}", data=donnees,
                              files={champ: f}, timeout=600)
        return r.json()
    except Exception as ex:
        print(f"Telegram ({methode}) : {ex}")
        return None


def dire(texte, boutons=None):
    extra = {"reply_markup": {"inline_keyboard": boutons}} if boutons else {}
    if not _chat():
        print("(Telegram) " + texte[:300])
        return None
    return tg("sendMessage", chat_id=_chat(), text=texte[:4000], disable_web_page_preview=True, **extra)


# ---------------------------------------------------------------------------
# Stockage des vocaux et des vidéos (release GitHub « longues »)
# ---------------------------------------------------------------------------
def _gh(*args, check=True):
    return subprocess.run(["gh", *args], capture_output=True, text=True, check=check)


def _repo():
    return os.environ.get("GITHUB_REPOSITORY")


def stocker(chemin):
    if not _repo():
        return os.path.abspath(chemin)
    if _gh("release", "view", RELEASE, "--repo", _repo(), check=False).returncode != 0:
        _gh("release", "create", RELEASE, "--repo", _repo(), "--title", "Vidéos longues (stockage automatique)",
            "--notes", "Vocaux et vidéos préparés par le robot.", "--latest=false")
    _gh("release", "upload", RELEASE, chemin, "--clobber", "--repo", _repo())
    return os.path.basename(chemin)


def recuperer(nom, dossier):
    os.makedirs(dossier, exist_ok=True)
    cible = os.path.join(dossier, os.path.basename(nom))
    if os.path.exists(cible):
        return cible
    if os.path.isabs(nom) and os.path.exists(nom):
        shutil.copy(nom, cible)
        return cible
    _gh("release", "download", RELEASE, "--repo", _repo(), "--pattern", nom, "--dir", dossier, "--clobber")
    return cible


# ---------------------------------------------------------------------------
# 1. Écriture des scripts (épisodes sans « blocs »)
# ---------------------------------------------------------------------------
REGLES = """Tu écris le script d'une vidéo YouTube de 8 à 9 minutes pour la chaîne « Le Média Français », lue par
Mathis (voix off, tutoiement, ton de conteur vif et clair, phrases courtes faciles à dire à voix haute).
Codes YouTube à respecter :
- Les 15 premières secondes : le fait le plus surprenant, puis la promesse « À la fin de cette vidéo, tu sauras… ».
- 6 blocs de 180 à 220 mots, chacun avec un titre de chapitre court. Une relance (question, retournement, détail fou)
  à la fin de chaque bloc pour donner envie de rester.
- Le dernier bloc DONNE LA RÉPONSE clairement à la question du titre, puis une question pour les commentaires et
  « abonne-toi ». Jamais de suite à attendre, jamais « dans un prochain épisode ».
- Uniquement des faits du dossier fourni. Aucun chiffre, date ou nom qui n'y est pas. Pas de politique actuelle.
Chaque bloc est découpé en segments de 1 à 3 phrases (15 à 40 mots), chacun avec un visuel :
- {"type":"photo","wiki":"Titre EXACT d'un article Wikipédia FR (lieu, personne, objet, événement)",
   "nature":"lieu|personne|objet|evenement","commons":"requête de secours en anglais pour Wikimedia Commons",
   "ecran":"2 À 5 MOTS EN CAPITALES (facultatif)"}
- {"type":"chiffre","chiffre":"1278 ou 9 MILLIONS…","legende":"5 À 8 MOTS EN CAPITALES"} pour les dates et chiffres forts
- {"type":"titre","texte":"1 À 4 MOTS"} pour ouvrir un bloc
- {"type":"fin"} pour le tout dernier segment uniquement
Varie : environ 60 % de photos, 30 % de chiffres, 10 % de titres."""


def ecrire(ep):
    """Écrit le script d'un épisode (dossier de faits vérifiés → script → relecture)."""
    print(f"Écriture du script : {ep['titre']}")
    dossier = gemini.texte(f"""Prépare un dossier de faits VÉRIFIÉS pour une vidéo intitulée « {ep['titre']} ».
Piste : {ep.get('consigne', '')}
Donne 25 à 35 faits précis et surprenants (dates, noms, chiffres exacts), chacun avec l'adresse de sa source.
N'écris rien dont tu n'es pas sûr. Termine par la réponse claire à la question du titre.""", recherche=True, temperature=0.2)
    script = gemini.json_(f"""{REGLES}

Titre : « {ep['titre']} »
Dossier de faits (seule source autorisée) :
{dossier}

Réponds en JSON : {{"miniatures": ["3 textes de miniature de 2 à 5 mots en capitales, très accrocheurs, différents"],
"photo_miniature": {{"wiki": "...", "nature": "...", "commons": "..."}},
"tags": ["8 mots-clés"], "sources": ["Nom : adresse", "..."],
"blocs": [{{"chapitre": "...", "segments": [{{"texte": "...", "visuel": {{...}}}}]}}]}}""", temperature=0.6)
    verif = gemini.json_(f"""Relis ce script de vidéo et compare-le au dossier. Liste chaque affirmation qui n'est pas
appuyée par le dossier ou qui est fausse. Réponds en JSON : {{"problemes": ["…"]}} (liste vide si tout est bon).
DOSSIER :
{dossier}
SCRIPT :
{json.dumps(script.get('blocs', []), ensure_ascii=False)}""", temperature=0.1)
    if verif.get("problemes"):
        print("Corrections : " + " | ".join(verif["problemes"])[:500])
        script = gemini.json_(f"""Corrige ce script en supprimant ou corrigeant ces affirmations : {verif['problemes']}.
Garde exactement le même format JSON et les mêmes règles.
{REGLES}
DOSSIER :
{dossier}
SCRIPT :
{json.dumps(script, ensure_ascii=False)}""", temperature=0.3)
    blocs = script.get("blocs") or []
    if len(blocs) < 4 or not all(b.get("segments") for b in blocs):
        raise RuntimeError("Script incomplet renvoyé par Gemini")
    for k in ("miniatures", "photo_miniature", "tags", "sources"):
        if script.get(k):
            ep[k] = script[k]
    ep["blocs"] = blocs


# ---------------------------------------------------------------------------
# 2. Séance voix du dimanche
# ---------------------------------------------------------------------------
def texte_bloc(b):
    return " ".join(s["texte"].strip() for s in b["segments"])


def envoyer_script(ep):
    n = len(ep["blocs"])
    ep["tg_blocs"] = {}
    ep.setdefault("voix", {})
    for i, b in enumerate(ep["blocs"], 1):
        if str(i) in ep["voix"]:
            continue
        rep = dire(f"🎙 {ep['titre']}\nBloc {i}/{n} · {b['chapitre']}\n\n{texte_bloc(b)}")
        if rep and rep.get("ok"):
            ep["tg_blocs"][str(rep["result"]["message_id"])] = i
    ep["statut"] = "voix"


def seance(d, forcer=None):
    eps = [episode(d, forcer)] if forcer else [e for e in d["episodes"] if e.get("statut") == "a_ecrire"][:SEANCE_EPISODES]
    eps = [e for e in eps if e]
    if not eps:
        dire("🎙 Séance voix : aucun épisode en attente. Ajoute des sujets dans data/longues.json (ou demande-les à Claude).")
        return
    for ep in eps:
        if not ep.get("blocs"):
            try:
                ecrire(ep)
            except Exception as ex:
                dire(f"⚠️ Script « {ep['titre']} » impossible à écrire ({ex}). Il sera retenté dimanche prochain.")
                eps.remove(ep)
    if not eps:
        return
    total = sum(len(e["blocs"]) for e in eps)
    dire(f"🎙 SÉANCE VOIX — {len(eps)} épisode(s), {total} blocs (≈ {total * 3} min).\n\n"
         "• Réponds à chaque bloc par un vocal (appui long → Répondre), ou envoie simplement les vocaux dans l'ordre.\n"
         "• Pièce calme, téléphone à 15 cm, ton de conteur, pas trop vite. Une respiration entre les phrases.\n"
         "• Raté ? Réponds de nouveau au même bloc : le dernier vocal remplace l'ancien.\n"
         "Dès qu'un épisode est complet, le montage part tout seul.")
    for ep in eps:
        envoyer_script(ep)


# ---------------------------------------------------------------------------
# 3. Réception des vocaux
# ---------------------------------------------------------------------------
def _file_voix():
    try:
        from . import sheet
        return sheet.appel("voixInbox") or []
    except Exception as ex:
        print(f"File des vocaux indisponible : {ex}")
        return []


def _telecharger_tg(file_id, cible):
    r = tg("getFile", file_id=file_id)
    chemin = (r or {}).get("result", {}).get("file_path")
    if not chemin:
        raise RuntimeError("Telegram ne donne pas le fichier (plus de 20 Mo ?)")
    contenu = requests.get(f"https://api.telegram.org/file/bot{C.TELEGRAM_TOKEN}/{chemin}", timeout=300).content
    with open(cible, "wb") as f:
        f.write(contenu)
    return cible


def recevoir(d):
    """Range les vocaux reçus ; renvoie les épisodes devenus complets."""
    complets = []
    for m in _file_voix():
        m = m.get("message", m)
        fichier = m.get("voice") or m.get("audio") or (m.get("document") if str(m.get("document", {}).get("mime_type", "")).startswith("audio") else None)
        if not fichier:
            continue
        cible = (m.get("reply_to_message") or {}).get("message_id")
        ep, bloc = None, None
        for e in d["episodes"]:
            if cible and str(cible) in e.get("tg_blocs", {}):
                ep, bloc = e, e["tg_blocs"][str(cible)]
                break
        if not ep:   # pas une réponse : le prochain bloc manquant de l'épisode en cours
            for e in d["episodes"]:
                if e.get("statut") == "voix":
                    manquants = [i for i in range(1, len(e["blocs"]) + 1) if str(i) not in e.get("voix", {})]
                    if manquants:
                        ep, bloc = e, manquants[0]
                        break
        if not ep:
            dire("🎙 Vocal reçu, mais aucun bloc n'attend de voix. Réponds directement au bloc à refaire.")
            continue
        dossier = os.path.join(SORTIE, ep["id"])
        os.makedirs(dossier, exist_ok=True)
        ext = ".ogg" if "voice" in m else os.path.splitext(fichier.get("file_name", "a.m4a"))[1] or ".m4a"
        chemin = os.path.join(dossier, f"{ep['id']}-b{bloc}{ext}")
        try:
            _telecharger_tg(fichier["file_id"], chemin)
        except Exception as ex:
            dire(f"⚠️ Bloc {bloc} de « {ep['titre']} » : vocal illisible ({ex}). Renvoie-le.")
            continue
        ep.setdefault("voix", {})[str(bloc)] = stocker(chemin)
        if ep.get("statut") not in ("voix",):   # refaire un bloc après montage : on remonte
            ep["statut"] = "voix"
        n = len(ep["blocs"])
        restants = [i for i in range(1, n + 1) if str(i) not in ep["voix"]]
        duree = fichier.get("duration")
        dire(f"✅ Bloc {bloc}/{n} reçu{f' ({duree} s)' if duree else ''} · {ep['titre']}"
             + (f"\nIl reste : {', '.join(map(str, restants))}" if restants else "\n🎬 Épisode complet : montage lancé (≈ 30 min)."))
        if not restants and ep not in complets:
            complets.append(ep)
    return complets


# ---------------------------------------------------------------------------
# 4. Montage
# ---------------------------------------------------------------------------
def _norm(mot):
    mot = unicodedata.normalize("NFD", mot.lower())
    return re.sub(r"[^a-z0-9]", "", "".join(c for c in mot if unicodedata.category(c) != "Mn"))


def preparer_voix(src, cible):
    """Nettoie un vocal : coupe les silences du début et de la fin, réduit le souffle, niveau YouTube (-16 LUFS)."""
    tmp1, tmp2 = cible + ".1.wav", cible + ".2.wav"   # en 3 passes : la chaîne complète bloque ffmpeg
    _run([_ff(), "-v", "error", "-y", "-i", src, "-af", "highpass=f=70,afftdn=nf=-25", "-ar", "48000", "-ac", "1", tmp1])
    _run([_ff(), "-v", "error", "-y", "-i", tmp1, "-af",
          "silenceremove=start_periods=1:start_threshold=-42dB:start_silence=0.12,areverse,"
          "silenceremove=start_periods=1:start_threshold=-42dB:start_silence=0.25,areverse", tmp2])
    _run([_ff(), "-v", "error", "-y", "-i", tmp2, "-af", "loudnorm=I=-16:TP=-1.5:LRA=11", "-ar", "48000", "-ac", "1", cible])
    for t in (tmp1, tmp2):
        os.remove(t)
    return _ffprobe_duree(cible)


_modele_whisper = None


def transcrire(wav):
    """Mots et horaires (faster-whisper, gratuit, sur le processeur). [] si indisponible."""
    global _modele_whisper
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        print("Whisper absent : sous-titres calés au prorata (moins précis).")
        return []
    try:
        if _modele_whisper is None:
            _modele_whisper = WhisperModel(os.environ.get("WHISPER_MODELE", "small"), device="cpu", compute_type="int8")
        segs, _ = _modele_whisper.transcribe(wav, language="fr", word_timestamps=True, vad_filter=True)
        return [(w.word.strip(), w.start, w.end) for s in segs for w in (s.words or [])]
    except Exception as ex:
        print(f"Whisper a échoué ({ex}) : calage au prorata.")
        return []


def caler(segments, duree, mots_w):
    """Horaires de chaque mot du script (dans le bloc), à partir des mots reconnus par Whisper.
    Renvoie [(mot, debut, fin, index_segment)]."""
    script = [(w, k) for k, s in enumerate(segments) for w in s["texte"].split()]
    n = len(script)
    if not n:
        return []
    ancres = {}
    if mots_w:
        a = [_norm(w) for w, _ in script]
        b = [_norm(w) for w, _, _ in mots_w]
        for bl in difflib.SequenceMatcher(None, a, b, autojunk=False).get_matching_blocks():
            for k in range(bl.size):
                ancres[bl.a + k] = (mots_w[bl.b + k][1], mots_w[bl.b + k][2])
    # bornes : début et fin du bloc ; entre deux ancres, au prorata du nombre de lettres
    poids = [max(2, len(w)) + 1 for w, _ in script]
    cumul = [0]
    for p in poids:
        cumul.append(cumul[-1] + p)
    points = sorted([(-1, 0.0, 0.0)] + [(i, t0, t1) for i, (t0, t1) in ancres.items()] + [(n, duree, duree)])
    temps = [None] * n
    for (i0, _, f0), (i1, d1, _) in zip(points, points[1:]):
        if i1 - i0 <= 1:
            continue
        debut, fin = f0, max(f0, d1)
        c0, c1 = cumul[i0 + 1], cumul[i1]
        for i in range(i0 + 1, i1):
            r0 = (cumul[i] - c0) / max(1, c1 - c0)
            r1 = (cumul[i + 1] - c0) / max(1, c1 - c0)
            temps[i] = (debut + r0 * (fin - debut), debut + r1 * (fin - debut))
    for i, t in ancres.items():
        temps[i] = t
    return [(script[i][0], temps[i][0], temps[i][1], script[i][1]) for i in range(n)]


# --- Images ----------------------------------------------------------------
def _fond_flou(photo):
    fond = photo.copy()
    r = max(L / fond.width, H / fond.height)
    fond = fond.resize((int(fond.width * r) + 1, int(fond.height * r) + 1)).crop((0, 0, L, H))
    fond = fond.filter(ImageFilter.GaussianBlur(40))
    return Image.blend(fond, Image.new("RGB", (L, H), (0, 0, 0)), 0.45)


def cadrer(photo):
    """Photo en 1920×1080 : plein cadre si elle est en paysage, sinon centrée sur un fond flou."""
    photo = photo.convert("RGB")
    if photo.width / photo.height >= 1.3:
        return render.recadrer(photo, L, H)
    img = _fond_flou(photo)
    r = min(L * 0.9 / photo.width, H / photo.height)
    p = photo.resize((int(photo.width * r), int(photo.height * r)), Image.LANCZOS)
    img.paste(p, ((L - p.width) // 2, (H - p.height) // 2))
    return img


def trouver_image(ep, v):
    """Image exacte (Wikipédia + contrôle visuel) puis Wikimedia Commons ; mise en cache dans l'épisode."""
    cache = ep.setdefault("images", {})
    cle = json.dumps([v.get("wiki"), v.get("commons")], ensure_ascii=False)
    if cle in cache:
        return cache[cle]
    trouve = None
    if v.get("wiki"):
        trouve = wiki.image_exacte(v["wiki"], v.get("nature", "lieu"))
    if not trouve and v.get("commons"):
        res = images.wikimedia(v["commons"], nombre=1)
        trouve = res[0] if res else None
    cache[cle] = {"url": trouve["url"], "credit": trouve.get("credit", "")} if trouve else None
    return cache[cle]


def _logo(img, largeur=64):
    """Le logo est ajouté une seule fois sur la vidéo finale (sinon les zooms le coupent)."""
    return img


def etalonner(img, fort=False):
    """Étalonnage « documentaire » : contraste, couleurs un peu plus riches, vignettage doux."""
    from PIL import ImageEnhance
    img = ImageEnhance.Contrast(img).enhance(1.18 if fort else 1.08)
    img = ImageEnhance.Color(img).enhance(1.25 if fort else 1.10)
    w, h = img.size
    masque = Image.radial_gradient("L").resize((w, h)).point(lambda p: int(max(0, p - 90) * (0.75 if fort else 0.55)))
    return Image.composite(Image.new("RGB", (w, h), (0, 0, 0)), img, masque)


def _barre(d, x, y, w=10, h=80):
    d.rectangle([x, y, x + w, y + h], fill=C.ROUGE)


def _texte_ombre(d, xy, texte, f, couleur, ombre=6):
    d.text((xy[0] + ombre * 0.6, xy[1] + ombre), texte, font=f, fill=(0, 0, 0))
    d.text(xy, texte, font=f, fill=couleur)


def image_photo(photo, ecran=None):
    img = etalonner(cadrer(photo))
    if ecran:   # titre à l'écran : barre rouge + bandeaux bleu marine (DA des carrousels)
        render.dessiner_bandeaux(img, ecran, [ecran.split()[-1]], C.BLEU_MARINE, C.BLANC, 62, 104, 300, 1250)
        n = len(render._couper_lignes(ecran, render.police(62), 1250))
        _barre(ImageDraw.Draw(img), 80, 300 - n * (int(62 * 1.22) + 2 * int(62 * 0.16) + C.ESPACE_ENTRE_BANDEAUX), 12,
               n * (int(62 * 1.22) + 2 * int(62 * 0.16) + C.ESPACE_ENTRE_BANDEAUX) - C.ESPACE_ENTRE_BANDEAUX)
    return img


def _fond_marine():
    img = Image.new("RGB", (L, H), C.BLEU_MARINE)
    d = ImageDraw.Draw(img)
    for y in range(0, H, 4):   # léger dégradé vertical
        k = 1 - 0.35 * y / H
        d.line([(0, y), (L, y)], fill=tuple(int(c * k) for c in C.BLEU_MARINE), width=4)
    return img


def _fond(photo):
    """Fond des cartes : la photo voisine floutée et teintée bleu marine (sinon un dégradé bleu marine)."""
    if photo is None:
        return _fond_marine()
    flou = render.recadrer(photo.convert("RGB"), L, H).filter(ImageFilter.GaussianBlur(28))
    return Image.blend(flou, _fond_marine(), 0.72)


def _taille_pour(texte, taille_max, largeur_max):
    taille = taille_max
    while taille > 40 and render.police(taille).getlength(texte) > largeur_max:
        taille -= 6
    return taille


def _texte_centre(img, texte, taille_max, y, couleur, largeur_max=1700, ombre=0):
    d = ImageDraw.Draw(img)
    taille = _taille_pour(texte, taille_max, largeur_max)
    f = render.police(taille)
    x = (L - f.getlength(texte)) / 2
    if ombre:
        _texte_ombre(d, (x, y - taille / 2), texte, f, couleur, ombre)
    else:
        d.text((x, y - taille / 2), texte, font=f, fill=couleur)
    return taille


def image_chiffre(chiffre, legende, photo=None):
    img = _fond(photo)
    d = ImageDraw.Draw(img)
    t = _texte_centre(img, chiffre.upper(), 230, 440, C.BLANC, ombre=8)
    _barre(d, L / 2 - 90, 440 + t * 0.62, 180, 12)
    if legende:
        f = render.police(52)
        y = 440 + t * 0.62 + 50
        for ligne in render._couper_lignes(legende, f, 1500):
            txt = " ".join(ligne)
            _texte_ombre(d, ((L - f.getlength(txt)) / 2, y), txt, f, C.BLANC, 4)
            y += 72
    return img


def image_titre(texte, chapitre="", photo=None):
    """Carte de chapitre : photo floutée bleu marine, petite étiquette rouge, gros titre blanc."""
    img = _fond(photo)
    d = ImageDraw.Draw(img)
    if chapitre:
        f = render.police(40)
        w = f.getlength(chapitre.upper())
        d.rectangle([(L - w) / 2 - 22, 330, (L + w) / 2 + 22, 398], fill=C.ROUGE)
        d.text(((L - w) / 2, 340), chapitre.upper(), font=f, fill=C.BLANC)
    if texte:
        _texte_centre(img, texte.upper(), 180, H / 2 + 40, C.BLANC, ombre=8)
    return img


def image_fin():
    img = _fond_marine()
    render._coller(img, os.path.join(ASSETS, "logo_pin.png"), 170, ((L - 170) // 2, 170))
    _texte_centre(img, "LE MÉDIA FRANÇAIS", 110, 560, C.BLANC)
    f = render.police(60)
    txt = "ABONNE-TOI POUR LA PROCHAINE HISTOIRE"
    w = f.getlength(txt)
    d = ImageDraw.Draw(img)
    d.rectangle([(L - w) / 2 - 30, 690, (L + w) / 2 + 30, 790], fill=C.ROUGE)
    d.text(((L - w) / 2, 704), txt, font=f, fill=C.BLANC)
    return img


_photos_cache = {}


def _photo(ep, v, credits=None):
    """Télécharge (une seule fois) la photo d'un visuel ; None si aucune image sûre."""
    if v.get("type", "photo") != "photo":
        return None
    trouve = trouver_image(ep, v)
    if not trouve:
        return None
    if trouve["url"] not in _photos_cache:
        try:
            _photos_cache[trouve["url"]] = images.telecharger(trouve["url"])
        except Exception as ex:
            print(f"Téléchargement raté ({ex})")
            _photos_cache[trouve["url"]] = None
    if credits is not None and _photos_cache[trouve["url"]] is not None and trouve.get("credit"):
        credits.add(trouve["credit"])
    return _photos_cache[trouve["url"]]


def _photo_voisine(ep, timeline, k):
    """Pour les cartes : la photo de la scène suivante (ou précédente)."""
    for j in list(range(k + 1, min(len(timeline), k + 4))) + list(range(k - 1, max(-1, k - 4), -1)):
        p = _photo(ep, timeline[j][2])
        if p is not None:
            return p
    return None


def fabriquer_image(ep, v, chemin, credits, voisine=None, chapitre=""):
    t = v.get("type", "photo")
    if t == "chiffre":
        img = image_chiffre(v.get("chiffre", ""), v.get("legende", ""), voisine)
    elif t == "titre":
        img = image_titre(v.get("texte", ""), chapitre, voisine)
    elif t == "fin":
        img = image_fin()
    else:
        photo = _photo(ep, v, credits)
        if photo is None:   # jamais d'image fausse : une carte texte à la place
            img = image_titre(v.get("ecran") or "", "", voisine)
        else:
            img = image_photo(photo, v.get("ecran"))
    img.save(chemin, quality=95)
    return chemin


# --- Mouvements (Ken Burns) ------------------------------------------------
MOUVEMENTS = ["zoom_avant", "gauche_droite", "zoom_arriere", "droite_gauche"]


def plan(image, duree, mouvement, sortie):
    n = max(1, round(duree * FPS))
    if mouvement == "fixe":
        z, x, y = "1.0", "0", "0"
    elif mouvement == "zoom_avant":
        z, x, y = f"1+0.10*on/{n}", "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
    elif mouvement == "zoom_arriere":
        z, x, y = f"1.10-0.10*on/{n}", "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
    elif mouvement == "gauche_droite":
        z, x, y = "1.10", f"(iw-iw/zoom)*on/{n}", "ih/2-(ih/zoom/2)"
    else:
        z, x, y = "1.10", f"(iw-iw/zoom)*(1-on/{n})", "ih/2-(ih/zoom/2)"
    _run([_ff(), "-v", "error", "-y", "-i", image, "-vf",
          f"scale=3840:2160,zoompan=z='{z}':x='{x}':y='{y}':d={n}:s={L}x{H}:fps={FPS},format=yuv420p",
          "-frames:v", str(n), "-c:v", "libx264", "-preset", "veryfast", "-crf", "17", "-r", str(FPS), sortie])
    return sortie


# --- Sous-titres (ASS) -----------------------------------------------------
def _police_sous_titres():
    """Montserrat ExtraBold figée (libass ne sait pas choisir la graisse d'une police variable)."""
    dossier = os.path.join(SORTIE, "polices")
    cible = os.path.join(dossier, "Montserrat-ExtraBold.ttf")
    if os.path.exists(cible):
        return dossier
    os.makedirs(dossier, exist_ok=True)
    src = os.environ.get("LMF_POLICE_LOCALE") or C.POLICE_FICHIER
    if not os.path.exists(src):
        render._assurer_police()
    try:
        from fontTools.ttLib import TTFont
        from fontTools.varLib import instancer
        f = TTFont(src)
        if "fvar" in f:
            f = instancer.instantiateVariableFont(f, {"wght": C.POIDS_POLICE})
        f.save(cible)
    except Exception as ex:
        print(f"Police des sous-titres : copie simple ({ex})")
        shutil.copy(src, cible)
    return dossier


def _nom_police(dossier):
    try:
        from fontTools.ttLib import TTFont
        return TTFont(os.path.join(dossier, "Montserrat-ExtraBold.ttf"))["name"].getDebugName(1) or "Montserrat"
    except Exception:
        return "Montserrat"


def _t(s):
    s = max(0, s)
    return f"{int(s // 3600)}:{int(s % 3600 // 60):02d}:{s % 60:05.2f}"


def _bgr(rgb, alpha=0):
    return f"&H{alpha:02X}{rgb[2]:02X}{rgb[1]:02X}{rgb[0]:02X}"


def ass(mots, chapitres, chemin, police, vertical=False, decalage=0.0, fin=None):
    """Sous-titres en bandeaux bleu marine (DA), 2 à 5 mots, en capitales ; étiquette rouge de chapitre."""
    w, h = (1080, 1920) if vertical else (L, H)
    taille = 72 if vertical else 60
    marge = 520 if vertical else 64
    lignes = [
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {w}", f"PlayResY: {h}", "WrapStyle: 0", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
        "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: ST,{police},{taille},{_bgr(C.BLANC)},{_bgr(C.BLANC)},&H00141414,&H90000000,0,0,0,0,100,100,0,0,1,{4 if vertical else 3.5},{3 if vertical else 2.5},2,80,80,{marge},1",
        f"Style: CH,{police},40,{_bgr(C.BLANC)},{_bgr(C.BLANC)},{_bgr(C.ROUGE)},{_bgr(C.ROUGE)},0,0,0,0,100,100,0,0,3,14,0,7,80,80,70,1",
        "", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    morceaux, morceau = [], []

    def vider():
        if morceau:
            morceaux.append(list(morceau))
            morceau.clear()

    for m in mots:
        if fin is not None and (m[1] - decalage < 0 or m[1] - decalage > fin):
            continue
        morceau.append(m)
        long = len(" ".join(x[0] for x in morceau))
        coupure = re.search(r"[.!?…:;,»]$", m[0]) is not None
        if long >= (22 if vertical else 38) or len(morceau) >= (5 if vertical else 7) or (coupure and len(morceau) >= 2) or re.search(r"[.!?…]$", m[0]):
            vider()
    vider()
    for k, mm in enumerate(morceaux):
        debut = mm[0][1] - decalage
        fin_m = mm[-1][2] - decalage + 0.25
        if k + 1 < len(morceaux):
            fin_m = min(fin_m, morceaux[k + 1][0][1] - decalage - 0.01)
        if fin is not None:
            fin_m = min(fin_m, fin)
        if fin_m <= debut:
            continue
        texte = " ".join(x[0] for x in mm)
        texte = (texte.upper() if vertical else texte[:1].upper() + texte[1:]).replace("{", "(").replace("}", ")")
        lignes.append(f"Dialogue: 0,{_t(debut)},{_t(fin_m)},ST,,0,0,0,,{texte}")
    for debut, titre, numero in chapitres:
        if fin is None:
            lignes.append(f"Dialogue: 1,{_t(debut + 0.3)},{_t(debut + 4.5)},CH,,0,0,0,,{numero} · {titre.upper()}")
    with open(chemin, "w", encoding="utf-8") as f:
        f.write("\n".join(lignes) + "\n")
    return chemin


# --- Musique ---------------------------------------------------------------
def musique(ep):
    from . import reels
    m = reels.choisir_musique({"musiques": []}, ep.get("musique"))
    if not m:
        return None, "", 1.0
    info = reels.infos_musique(m)
    return os.path.join(reels.MUSIQUES, m + ".mp3"), info.get("credit", ""), info.get("volume", 1.0)


def montage(d, ep, voix_locales=None, televerser=True):
    """Fabrique la vidéo (et les Shorts) de l'épisode. voix_locales : {bloc: chemin} pour les tests."""
    dossier = os.path.join(SORTIE, ep["id"])
    plans = os.path.join(dossier, "plans")
    os.makedirs(plans, exist_ok=True)
    ep["statut"] = "montage"
    credits = set()
    mots_tous, chapitres, timeline = [], [], []   # timeline : (debut, fin, visuel)
    pistes, t = [], 0.0
    n = len(ep["blocs"])
    blocs = range(1, n + 1) if not voix_locales else sorted(voix_locales)
    for i in blocs:
        b = ep["blocs"][i - 1]
        src = voix_locales[i] if voix_locales else recuperer(ep["voix"][str(i)], dossier)
        wav = os.path.join(dossier, f"b{i}.wav")
        duree = preparer_voix(src, wav)
        mots = caler(b["segments"], duree, transcrire(wav))
        print(f"Bloc {i} : {duree:.1f} s, {len(mots)} mots")
        chapitres.append((t, b["chapitre"], f"CHAPITRE {i}"))
        for k, s in enumerate(b["segments"]):
            ms = [m for m in mots if m[3] == k]
            if not ms:
                continue
            debut = t + (0 if k == 0 else ms[0][1])
            suivant = [m for m in mots if m[3] == k + 1]
            fin = t + (suivant[0][1] if suivant else duree + ECART_BLOCS)
            timeline.append([debut, fin, s["visuel"]])
        mots_tous += [(m[0], t + m[1], t + m[2], i) for m in mots]
        pistes.append(wav)
        t += duree + ECART_BLOCS
    if ep["blocs"][-1]["segments"][-1]["visuel"].get("type") != "fin" or voix_locales:
        timeline.append([t, t + FIN_EN_PLUS, {"type": "fin"}])
    else:
        timeline[-1][1] = t + FIN_EN_PLUS
    total = timeline[-1][1]
    timeline[0][0] = 0.0
    for a, b2 in zip(timeline, timeline[1:]):   # pas de trou ni de chevauchement
        a[1] = b2[0]

    # Voix complète (blocs séparés par un court silence)
    silence = os.path.join(dossier, "silence.wav")
    _run([_ff(), "-v", "error", "-y", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono", "-t", str(ECART_BLOCS), silence])
    liste = os.path.join(dossier, "voix.txt")
    with open(liste, "w") as f:
        for p in pistes:
            f.write(f"file '{os.path.abspath(p)}'\nfile '{os.path.abspath(silence)}'\n")
    voix = os.path.join(dossier, "voix.wav")
    _run([_ff(), "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", liste, "-c", "copy", voix])

    # Images et plans
    print(f"{len(timeline)} scènes, {total:.0f} s")
    clips = []
    for k, (debut, fin, v) in enumerate(timeline):
        voisine = _photo_voisine(ep, timeline, k) if v.get("type") in ("chiffre", "titre") or not _photo(ep, v) else None
        chap = next((f"{c[2]} · {c[1]}" for c in reversed(chapitres) if c[0] <= debut + 0.01), "")
        img = fabriquer_image(ep, v, os.path.join(plans, f"s{k:03d}.jpg"), credits, voisine, chap)
        duree = fin - debut
        statique = v.get("type") in ("chiffre", "titre", "fin")
        morceaux = 1 if (statique or duree <= 11) else 2
        for j in range(morceaux):
            mvt = "zoom_avant" if statique else MOUVEMENTS[(k * 2 + j) % len(MOUVEMENTS)]
            if statique:
                mvt = "fixe" if v.get("type") == "fin" else "zoom_avant"
            clips.append(plan(img, duree / morceaux, mvt, os.path.join(plans, f"s{k:03d}_{j}.mp4")))
    liste_v = os.path.join(dossier, "plans.txt")
    with open(liste_v, "w") as f:
        f.writelines(f"file '{os.path.abspath(c)}'\n" for c in clips)

    # Sous-titres, musique, mixage
    polices = _police_sous_titres()
    nom_police = _nom_police(polices)
    # l'étiquette de chapitre n'apparaît que si le bloc ne s'ouvre pas déjà sur une carte de chapitre
    etiquettes = [c for c, i in zip(chapitres, blocs) if ep["blocs"][i - 1]["segments"][0]["visuel"].get("type") != "titre"]
    sous_titres = ass(mots_tous, etiquettes, os.path.join(dossier, "sous_titres.ass"), nom_police)
    mus = musique(ep)
    finale = os.path.join(dossier, f"{ep['id']}.mp4")
    propre = os.path.join(dossier, "sans_sous_titres.mp4")
    entrees = [_ff(), "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", liste_v, "-i", voix]
    if mus and mus[0] and os.path.exists(mus[0]):
        vol = 0.30 * float(mus[2] or 1)
        entrees += ["-stream_loop", "-1", "-i", mus[0]]
        audio = (f"[2:a]aformat=sample_rates=48000:channel_layouts=stereo,volume={vol:.2f},"
                 f"afade=t=in:d=2,afade=t=out:st={max(0, total - 3):.2f}:d=3[m];"
                 "[1:a]aformat=sample_rates=48000:channel_layouts=stereo,apad,asplit=2[v1][v2];"
                 "[m][v2]sidechaincompress=threshold=0.015:ratio=10:attack=15:release=450:makeup=1[md];"
                 "[v1][md]amix=inputs=2:normalize=0:duration=first,alimiter=limit=0.95[a]")
    else:
        audio = "[1:a]aformat=sample_rates=48000:channel_layouts=stereo,apad[a]"
    # 1) version sans sous-titres (sert aux Shorts), 2) version finale avec sous-titres
    _run(entrees + ["-filter_complex", audio, "-map", "0:v", "-map", "[a]", "-t", f"{total:.2f}",
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-c:a", "aac", "-b:a", "192k", propre])
    chemin_ass = sous_titres.replace("\\", "/").replace(":", "\\:")
    _run([_ff(), "-v", "error", "-y", "-i", propre, "-i", os.path.join(ASSETS, "logo_pin.png"), "-filter_complex",
          f"[0:v]ass='{chemin_ass}':fontsdir='{polices}'[s];[1:v]scale=64:-1[l];[s][l]overlay=W-w-44:36[v]",
          "-map", "[v]", "-map", "0:a",
          "-c:v", "libx264", "-preset", "medium", "-crf", "19", "-profile:v", "high", "-pix_fmt", "yuv420p",
          "-c:a", "copy", "-movflags", "+faststart", finale])
    ep["duree"] = round(total)
    ep["chapitres"] = [[round(c[0]), c[1]] for c in chapitres]
    ep["credits_images"] = sorted(credits)
    ep["credit_musique"] = mus[1] if mus else ""
    ep["mots"] = [[m[0], round(m[1], 2), round(m[2], 2)] for m in mots_tous]

    # Miniatures et Shorts
    minis = miniatures(ep, dossier)
    shorts = fabriquer_shorts(ep, dossier, propre, mots_tous, timeline, polices, nom_police)
    print(f"Vidéo prête : {finale} ({total / 60:.1f} min) · {len(minis)} miniatures · {len(shorts)} Shorts")
    if not televerser:
        ep["statut"] = "test"
        return finale
    mettre_en_ligne(ep, finale, minis, shorts)
    return finale


# ---------------------------------------------------------------------------
# 5. Miniatures (1280×720)
# ---------------------------------------------------------------------------
def miniatures(ep, dossier):
    """3 miniatures 1280×720 aux codes YouTube : une photo forte, 2 à 4 mots énormes, un mot en rouge, contraste fort."""
    pm = ep.get("photo_miniature") or next((s["visuel"] for b in ep["blocs"] for s in b["segments"]
                                            if s["visuel"].get("type") == "photo"), {})
    photo = _photo(ep, dict(pm, type="photo")) if pm else None
    textes = (ep.get("miniatures") or [ep["titre"].upper()] * 3)[:3]
    while len(textes) < 3:
        textes.append(textes[0])
    W, Hm = 1280, 720
    sorties = []
    for n, texte in enumerate(textes, 1):
        texte = texte.upper()
        rouge = render._mots_rouges([max(texte.split(), key=len)])
        base = etalonner(cadrer(photo), fort=True).resize((W, Hm), Image.LANCZOS) if photo else _fond_marine().resize((W, Hm))
        if n == 1:    # code YouTube classique : dégradé sombre à gauche, texte énorme blanc/rouge avec contour
            masque = Image.linear_gradient("L").rotate(90).resize((W, Hm)).point(lambda p: int(p * 0.85))
            img = Image.composite(Image.new("RGB", (W, Hm), (4, 12, 40)), base, masque)
            d = ImageDraw.Draw(img)
            taille = 118
            f = render.police(taille)
            lignes = render._couper_lignes(texte, f, 700)
            while len(lignes) > 3 and taille > 70:
                taille -= 8
                f = render.police(taille)
                lignes = render._couper_lignes(texte, f, 700)
            y = (Hm - len(lignes) * taille * 1.08) / 2
            for ligne in lignes:
                x = 52
                for mot in ligne:
                    coul = C.ROUGE if render._normaliser(mot) in rouge else C.BLANC
                    d.text((x, y), mot, font=f, fill=coul, stroke_width=5, stroke_fill=(0, 0, 0))
                    x += f.getlength(mot + " ")
                y += taille * 1.08
        elif n == 2:  # DA des carrousels : bandeaux blancs, texte bleu marine, étiquette rouge
            img = base
            noir = Image.linear_gradient("L").resize((W, Hm)).point(lambda p: int(max(0, p - 110) * 1.2))
            img = Image.composite(Image.new("RGB", (W, Hm), (0, 0, 0)), img, noir)
            render.dessiner_bandeaux(img, texte, list(rouge), C.BLANC, C.BLEU_MARINE, 96, 48, 680, 820)
            d = ImageDraw.Draw(img)
            f = render.police(34)
            d.rectangle([48, 40, 48 + f.getlength("POURQUOI ?") + 36, 98], fill=C.ROUGE)
            d.text((66, 49), "POURQUOI ?", font=f, fill=C.BLANC)
        else:         # panneau bleu marine + photo, liseré rouge
            img = base
            d = ImageDraw.Draw(img)
            d.rectangle([0, 0, 540, Hm], fill=C.BLEU_MARINE)
            d.rectangle([540, 0, 552, Hm], fill=C.ROUGE)
            taille = 92
            f = render.police(taille)
            lignes = render._couper_lignes(texte, f, 470)
            while len(lignes) > 4 and taille > 60:
                taille -= 8
                f = render.police(taille)
                lignes = render._couper_lignes(texte, f, 470)
            y = (Hm - len(lignes) * taille * 1.1) / 2
            for ligne in lignes:
                x = 40
                for mot in ligne:
                    d.text((x, y), mot, font=f, fill=C.ROUGE if render._normaliser(mot) in rouge else C.BLANC)
                    x += f.getlength(mot + " ")
                y += taille * 1.1
        chemin = os.path.join(dossier, f"miniature_{n}.jpg")
        img.convert("RGB").save(chemin, quality=93)
        sorties.append(chemin)
    return sorties


# ---------------------------------------------------------------------------
# 6. Shorts (extraits verticaux de 30 à 58 s)
# ---------------------------------------------------------------------------
def choisir_extraits(timeline, total, nombre=3):
    """Le début (l'accroche), puis les passages qui contiennent un chiffre fort, dans des blocs différents."""
    fenetres = []
    debut0 = 0.0
    fin0 = next((f for d0, f, _ in timeline if f - debut0 >= 30), min(total, 45))
    fenetres.append((debut0, min(fin0, 58)))
    candidats = []
    for k, (d0, f0, v) in enumerate(timeline):
        if v.get("type") != "chiffre" or d0 < 60:
            continue
        a = d0
        for d1, f1, _ in timeline[max(0, k - 2):k]:   # remonter jusqu'à 2 phrases avant pour le contexte
            if d0 - d1 <= 20:
                a = d1
                break
        b = a + 30
        for d1, f1, _ in timeline[k:]:
            if f1 - a > 58:
                break
            b = f1
        if b - a >= 25:
            candidats.append((a, b))
    for a, b in candidats:
        if all(abs(a - x) > 90 for x, _ in fenetres):
            fenetres.append((a, b))
        if len(fenetres) >= nombre:
            break
    return fenetres[:nombre]


def _habillage_short(titre, chemin):
    img = Image.new("RGBA", (1080, 1920), (0, 0, 0, 0))
    render.dessiner_bandeaux(img, titre, [max(titre.split(), key=len)], C.BLANC, C.BLEU_MARINE, 64, 60, 520, 960)
    f = render.police(40)
    txt = "▶ VIDÉO COMPLÈTE SUR LA CHAÎNE"
    if not f.getmask("▶").getbbox():
        txt = "VIDÉO COMPLÈTE SUR LA CHAÎNE"
    w = f.getlength(txt)
    d = ImageDraw.Draw(img)
    d.rectangle([(1080 - w) / 2 - 22, 1610, (1080 + w) / 2 + 22, 1680], fill=C.ROUGE)
    d.text(((1080 - w) / 2, 1618), txt, font=f, fill=C.BLANC)
    img.save(chemin)
    return chemin


def fabriquer_shorts(ep, dossier, propre, mots, timeline, polices, nom_police):
    sorties = []
    for n, (a, b) in enumerate(choisir_extraits(timeline, timeline[-1][1]), 1):
        habillage = _habillage_short(ep["titre"], os.path.join(dossier, f"short_{n}_habillage.png"))
        st = ass([m for m in mots if a <= m[1] <= b], [], os.path.join(dossier, f"short_{n}.ass"), nom_police,
                 vertical=True, decalage=a, fin=b - a)
        chemin_ass = st.replace("\\", "/").replace(":", "\\:")
        sortie = os.path.join(dossier, f"{ep['id']}_short_{n}.mp4")
        filtre = ("[0:v]split[a][b];[a]scale=-2:1920,crop=1080:1920,gblur=sigma=30,eq=brightness=-0.12[fond];"
                  "[b]scale=1080:-2[avant];[fond][avant]overlay=0:(H-h)/2-60[c];[c][1:v]overlay=0:0[d];"
                  f"[d]ass='{chemin_ass}':fontsdir='{polices}',format=yuv420p[v]")
        _run([_ff(), "-v", "error", "-y", "-ss", f"{a:.2f}", "-t", f"{b - a:.2f}", "-i", propre, "-i", habillage,
              "-filter_complex", filtre, "-map", "[v]", "-map", "0:a", "-c:v", "libx264", "-preset", "medium",
              "-crf", "19", "-c:a", "aac", "-b:a", "160k", "-af", "afade=t=out:st=" + f"{max(0, b - a - 0.8):.2f}" + ":d=0.8",
              "-movflags", "+faststart", sortie])
        sorties.append(sortie)
    return sorties


# ---------------------------------------------------------------------------
# 7. Mise en ligne, validation, publication
# ---------------------------------------------------------------------------
def _mmss(s):
    s = int(s)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60:02d}:{s % 60:02d}"


def description(ep):
    accroche = ep["blocs"][0]["segments"][0]["texte"]
    lignes = [accroche, "", "⏱ CHAPITRES"]
    lignes += [f"{_mmss(t)} {titre}" for t, titre in ep.get("chapitres", [])]
    if ep.get("sources"):
        lignes += ["", "📚 SOURCES"] + [f"• {s}" for s in ep["sources"]]
    if ep.get("credit_musique"):
        lignes += ["", f"🎵 Musique : {ep['credit_musique']}"]
    if ep.get("credits_images"):
        lignes += ["", "📷 Images : " + " · ".join(ep["credits_images"])[:1200]]
    lignes += ["", "👉 Abonne-toi : des histoires de France que personne ne t'a racontées.",
               f"📸 Instagram : {C.COMPTE}", "", "#histoire #France #histoiredefrance"]
    return "\n".join(lignes)[:4900]


def mettre_en_ligne(ep, finale, minis, shorts):
    from . import youtube
    ep["miniatures_fichiers"] = [stocker(m) for m in minis]
    ep["shorts_fichiers"] = [stocker(s) for s in shorts]
    if not youtube.actif():
        dire(f"⚠️ « {ep['titre']} » est montée, mais les secrets YouTube manquent : rien n'est en ligne.")
        ep["statut"] = "a_valider"
        return
    vid = youtube.envoyer(finale, ep["titre"], description(ep), ep.get("tags"), statut="unlisted")
    ep["video_id"] = vid
    ep["statut"] = "a_valider"
    try:
        youtube.changer_miniature(vid, minis[0])
        ep["miniature_choisie"] = 1
    except Exception as ex:
        print(f"Miniature : {ex}")
    for n, m in enumerate(minis, 1):
        tg_fichier("sendPhoto", "photo", m, caption=f"Miniature {n}")
    creneau = prochain_creneau(None)
    dire(f"🎬 « {ep['titre']} » ({_mmss(ep['duree'])}) est en ligne en NON RÉPERTORIÉ :\nhttps://youtu.be/{vid}\n\n"
         f"1. Regarde-la. 2. Choisis la miniature. 3. Programme-la ({creneau.strftime('%A %d/%m à %H h')}) ou publie-la.",
         boutons=[[{"text": f"Miniature {n}", "callback_data": f"ym:{ep['id']}:{n}"} for n in range(1, len(minis) + 1)],
                  [{"text": "✅ Programmer", "callback_data": f"yp:{ep['id']}"},
                   {"text": "🚀 Publier maintenant", "callback_data": f"yn:{ep['id']}"}]])


def prochain_creneau(d, apres=None):
    """Prochain jeudi ou samedi 18 h (Paris), au moins 2 h plus tard, pas déjà pris par un autre épisode."""
    pris = set()
    if d:
        pris = {e.get("publie_le") for e in d["episodes"] if e.get("publie_le")}
    t = (apres or dt.datetime.now(PARIS)) + dt.timedelta(hours=2)
    for k in range(0, 30):
        jour = (t + dt.timedelta(days=k)).date()
        for wd, h in CRENEAUX:
            if jour.weekday() == wd:
                c = dt.datetime(jour.year, jour.month, jour.day, h, 0, tzinfo=PARIS)
                if c >= t and c.isoformat() not in pris:
                    return c
    return t


def _utc(c):
    return c.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def bouton(d, valeur):
    from . import youtube
    action, _, reste = valeur.partition(":")
    eid, _, n = reste.partition(":")
    ep = episode(d, eid)
    if not ep or not ep.get("video_id"):
        dire("Épisode introuvable ou pas encore en ligne.")
        return
    dossier = os.path.join(SORTIE, ep["id"])
    if action == "ym":
        m = recuperer(ep["miniatures_fichiers"][int(n) - 1], dossier)
        youtube.changer_miniature(ep["video_id"], m)
        ep["miniature_choisie"] = int(n)
        dire(f"🖼 Miniature {n} en place sur « {ep['titre']} ».")
        return
    if action in ("yp", "yn"):
        if action == "yp":
            c = prochain_creneau(d)
            youtube.changer_statut(ep["video_id"], publier_le=_utc(c))
            ep["publie_le"] = c.isoformat()
            ep["statut"] = "programmee"
            quand = c.strftime("%A %d/%m à %H h")
        else:
            youtube.changer_statut(ep["video_id"], "public")
            c = dt.datetime.now(PARIS)
            ep["publie_le"] = c.isoformat()
            ep["statut"] = "publiee"
            quand = "maintenant"
        dire(f"✅ « {ep['titre']} » : publication {quand}.\nhttps://youtu.be/{ep['video_id']}")
        programmer_shorts(ep, c, dossier)


def programmer_shorts(ep, publication, dossier):
    from . import youtube
    liens = []
    for n, nom in enumerate(ep.get("shorts_fichiers", []), 1):
        chemin = recuperer(nom, dossier)
        jour = (publication + dt.timedelta(days=n)).replace(hour=18, minute=0, second=0, microsecond=0)
        titre = f"{ep['titre']} #Shorts" if n == 1 else f"{ep['titre']} ({n}) #Shorts"
        try:
            vid = youtube.envoyer(chemin, titre, f"La vidéo complète 👉 https://youtu.be/{ep['video_id']}\n\n#histoire #France",
                                  ep.get("tags"), publier_le=_utc(jour))
            liens.append(f"https://youtube.com/shorts/{vid} ({jour.strftime('%d/%m')})")
        except Exception as ex:
            liens.append(f"Short {n} raté : {ex}")
        tg_fichier("sendVideo", "video", chemin, caption=f"Extrait {n} de « {ep['titre']} » — à reposter en Reel/TikTok si tu veux.")
    ep["shorts"] = liens
    dire("📱 Shorts programmés sur YouTube :\n" + "\n".join(liens))


# ---------------------------------------------------------------------------
# 8. Test (voix de synthèse, rien n'est mis en ligne)
# ---------------------------------------------------------------------------
def voix_test(ep, dossier, blocs=(1, 2)):
    """Voix de synthèse (espeak-ng) pour tester le montage sans Mathis."""
    sorties = {}
    for i in blocs:
        texte = texte_bloc(ep["blocs"][i - 1])
        cible = os.path.join(dossier, f"test_b{i}.wav")
        if shutil.which("espeak-ng"):
            subprocess.run(["espeak-ng", "-v", "fr", "-s", "165", "-w", cible, texte], check=True)
        else:   # bruit rythmé qui imite une voix (pour tester sans espeak)
            mots = len(texte.split())
            _run([_ff(), "-v", "error", "-y", "-f", "lavfi", "-i",
                  f"anoisesrc=d={mots / 2.6:.1f}:c=pink:a=0.25", "-af",
                  "volume='0.4+0.6*gt(sin(2*PI*2.6*t),0)':eval=frame,lowpass=f=3000", "-ar", "48000", cible])
        sorties[i] = cible
    return sorties


# ---------------------------------------------------------------------------
# Entrée
# ---------------------------------------------------------------------------
def main(etape, valeur=""):
    d = charger()
    try:
        if etape == "seance":
            seance(d, valeur or None)
        elif etape == "voix":
            for ep in recevoir(d):
                sauver(d)
                try:
                    montage(d, ep)
                except Exception as ex:
                    ep["statut"] = "voix"
                    dire(f"⚠️ Montage de « {ep['titre']} » raté : {str(ex)[:600]}\nRelance : Actions → Vidéo longue → montage.")
                    raise
        elif etape == "montage":
            ep = episode(d, valeur) if valeur else next((e for e in d["episodes"] if e.get("voix") and
                                                         len(e["voix"]) >= len(e.get("blocs", [])) and
                                                         e.get("statut") in ("voix", "montage")), None)
            if not ep:
                print("Aucun épisode complet à monter.")
                return
            montage(d, ep)
        elif etape == "bouton":
            bouton(d, valeur)
        elif etape == "test":
            ep = episode(d, valeur) if valeur else next(e for e in d["episodes"] if e.get("blocs"))
            copie = json.loads(json.dumps(ep))
            dossier = os.path.join(SORTIE, copie["id"])
            os.makedirs(dossier, exist_ok=True)
            finale = montage(d, copie, voix_locales=voix_test(copie, dossier), televerser=False)
            dire(f"🧪 Test de montage terminé ({copie['titre']}, 2 blocs, voix de synthèse : seule la voix est fausse, "
                 "les images sont les vraies). Aperçu ci-dessous ; vidéo complète dans l'artefact « video-longue-test ».")
            for n in (1, 2, 3):
                tg_fichier("sendPhoto", "photo", os.path.join(dossier, f"miniature_{n}.jpg"), caption=f"Miniature {n}")
            for t in (8, 30, 55, 80):
                img = os.path.join(dossier, f"apercu_{t}.jpg")
                try:
                    _run([_ff(), "-v", "error", "-y", "-ss", str(t), "-i", finale, "-frames:v", "1", img])
                    tg_fichier("sendPhoto", "photo", img, caption=f"Vidéo à {t} s")
                except Exception:
                    pass
            print(finale)
            return   # le test ne modifie pas l'état
        else:
            raise SystemExit(f"Étape inconnue : {etape}")
    finally:
        if etape != "test":
            sauver(d)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "seance", sys.argv[2] if len(sys.argv) > 2 else "")
