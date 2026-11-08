"""Appels à Gemini (API REST, sans dépendance supplémentaire)."""
import json
import re
import time

import requests

from . import config as C

URL = "https://generativelanguage.googleapis.com/v1beta/models/{modele}:generateContent"


class ErreurGemini(Exception):
    pass


def _noter(modele, statut):
    """Compte chaque appel dans l'onglet Usage du Sheet (pour le récap quotidien)."""
    try:
        from . import sheet
        sheet.appel("logUsage", modele=modele, statut=statut)
    except Exception:
        pass


SECOURS = ["gemini-3.6-flash", "gemini-3.8-flash", "gemini-3.5-flash-lite"]  # modèles de repli si le principal est saturé


def _appel(corps, modele=None):
    """Essaie chaque clé (projets Google séparés = quotas séparés), et pour chaque clé
    le modèle demandé puis les modèles de secours."""
    cles = C.cles_gemini()
    if not cles:
        raise ErreurGemini("GEMINI_API_KEY manquant dans les secrets GitHub")
    derniere = None
    principal = modele or C.GEMINI_MODELE
    for i, cle in enumerate(cles):
        for m in [principal] + [x for x in SECOURS if x != principal]:
            try:
                return _appel_modele(corps, m, cle)
            except ErreurGemini as e:
                derniere = e
                if "indisponible" not in str(e) and "429" not in str(e) and "404" not in str(e):
                    raise
                print(f"{m} indisponible (clé {i + 1}), passage au modèle de secours…")
        if i + 1 < len(cles):
            print("Quota épuisé sur cette clé, passage à la clé suivante…")
    raise derniere


def _appel_modele(corps, modele=None, cle=None):
    modele = modele or C.GEMINI_MODELE
    url = URL.format(modele=modele)
    corps = dict(corps)
    gc = dict(corps.get("generationConfig", {}))
    gc.setdefault("thinkingConfig", {"thinkingLevel": "low"})  # réponses plus rapides
    corps["generationConfig"] = gc
    for essai in range(3):
        try:
            r = requests.post(url, params={"key": cle or C.GEMINI_API_KEY}, json=corps, timeout=300)
        except requests.exceptions.RequestException as e:  # délai dépassé, coupure réseau
            _noter(modele, "délai dépassé")
            time.sleep(15 * (essai + 1))
            continue
        if r.status_code == 400 and "thinking" in r.text.lower() and "thinkingConfig" in corps["generationConfig"]:
            corps["generationConfig"].pop("thinkingConfig")  # modèle qui ne connaît pas ce réglage
            continue
        if r.status_code == 429:
            _noter(modele, "quota dépassé (429)")
            time.sleep(15 * (essai + 1))
            continue
        if r.status_code in (500, 503):
            time.sleep(15 * (essai + 1))
            continue
        if r.status_code != 200:
            _noter(modele, f"erreur {r.status_code}")
            raise ErreurGemini(f"Gemini {r.status_code} : {r.text[:400]}")
        _noter(modele, "ok")
        data = r.json()
        try:
            parts = data["candidates"][0]["content"]["parts"]
        except (KeyError, IndexError):
            raise ErreurGemini(f"Réponse Gemini vide : {json.dumps(data)[:400]}")
        return "".join(p.get("text", "") for p in parts)
    raise ErreurGemini(f"{modele} indisponible après 3 essais (quota ? voir le mode opératoire)")


def texte(prompt, recherche=False, temperature=0.4, modele=None):
    """Réponse texte. recherche=True active la vérification par Google Search."""
    corps = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": temperature},
    }
    if recherche:
        corps["tools"] = [{"google_search": {}}]
    return _appel(corps, modele)


def json_(prompt, temperature=0.7, recherche=False, modele=None):
    """Réponse JSON (dict)."""
    corps = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": temperature},
    }
    if recherche:
        # La recherche n'est pas compatible avec le mode JSON strict : on extrait le JSON du texte.
        corps["tools"] = [{"google_search": {}}]
    else:
        corps["generationConfig"]["responseMimeType"] = "application/json"
    brut = _appel(corps, modele)
    return extraire_json(brut)


def extraire_json(brut):
    brut = brut.strip()
    brut = re.sub(r"^```(?:json)?|```$", "", brut, flags=re.M).strip()
    try:
        return json.loads(brut)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", brut, flags=re.S)
        if not m:
            raise ErreurGemini(f"JSON introuvable dans la réponse : {brut[:300]}")
        return json.loads(m.group(0))
