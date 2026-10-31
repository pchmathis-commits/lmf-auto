"""Dialogue avec le Google Sheet de pilotage, via l'application Web Apps Script.

Le Sheet stocke les posts, les sujets et le jeton Instagram, et envoie les mails.
"""
import requests

from . import config as C


class ErreurSheet(Exception):
    pass


def appel(action, **donnees):
    if not C.SHEET_URL or not C.SHEET_SECRET:
        raise ErreurSheet("SHEET_URL ou SHEET_SECRET manquant dans les secrets GitHub")
    corps = {"secret": C.SHEET_SECRET, "action": action, **donnees}
    r = requests.post(C.SHEET_URL, json=corps, timeout=120, allow_redirects=True)
    if r.status_code != 200:
        raise ErreurSheet(f"Sheet {r.status_code} : {r.text[:300]}")
    try:
        rep = r.json()
    except ValueError:
        raise ErreurSheet(
            "Le Sheet n'a pas répondu en JSON. Vérifie le déploiement de l'application Web "
            f"(accès « Tout le monde »). Début de la réponse : {r.text[:200]}"
        )
    if not rep.get("ok"):
        raise ErreurSheet(rep.get("erreur", "erreur inconnue"))
    return rep.get("data")


# Raccourcis
def prendre_sujet():
    return appel("getTopic")


def marquer_sujet(ligne, statut):
    return appel("markTopic", row=ligne, statut=statut)


def ajouter_sujet(sujet):
    return appel("addTopic", sujet=sujet)


def titres_sujets():
    return appel("listTitles") or []


def liens_utilises():
    return appel("usedLinks") or []


def ajouter_post(post):
    return appel("addPost", post=post)


def posts_a_publier():
    return appel("listDue") or []


def maj_post(post_id, **champs):
    return appel("updatePost", id=post_id, champs=champs)


def lire_jeton():
    return appel("getToken")


def ecrire_jeton(jeton):
    return appel("setToken", token=jeton)
