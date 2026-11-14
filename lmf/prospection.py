"""Prospection automatique : trouve des partenaires, leur écrit, suit les réponses.

- du lundi au vendredi, 3 mails par jour (réglages dans config.py) ;
- l'adresse est cherchée sur le site officiel de chaque cible (jamais devinée) ;
- chaque mail est écrit pour la cible, avec le media kit en pièce jointe ;
- une relance au bout de 7 jours sans réponse ;
- les réponses arrivent sur Telegram avec un résumé et un brouillon :
  réponds « ok » pour l'envoyer, ou écris ce qu'il faut changer (rien ne part sans « ok »).

Lancé toutes les heures (8 h – 20 h) par l'horloge du Sheet : tâche « Prospection »
L'état (contacts, envois, réponses) est gardé dans data/prospection.json.
"""
import datetime as dt
import email
import email.utils
import html
import imaplib
import json
import os
import random
import re
import smtplib
import ssl
import sys
import time
from email.header import decode_header, make_header
from email.message import EmailMessage
from urllib.parse import urljoin, urlparse
from zoneinfo import ZoneInfo

import requests

from . import config as C
from . import gemini

PARIS = ZoneInfo("Europe/Paris")
RACINE = os.path.join(os.path.dirname(__file__), "..")
FICHIER_ETAT = os.path.join(RACINE, "data", "prospection.json")
NAVIGATEUR = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                            "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
              "Accept-Language": "fr-FR,fr;q=0.9"}
MESSAGERIES_PERSO = {"gmail.com", "hotmail.com", "hotmail.fr", "outlook.com", "outlook.fr", "yahoo.fr",
                     "yahoo.com", "orange.fr", "free.fr", "wanadoo.fr", "sfr.fr", "laposte.net", "icloud.com"}
BONNES = ["presse", "press", "partenariat", "partenaires", "partnership", "partner", "communication", "com",
          "marketing", "media", "medias", "influence", "relationspresse", "rp", "brand", "marque", "mecenat"]
MOYENNES = ["contact", "info", "infos", "bonjour", "hello", "accueil", "information", "direction"]
INTERDITES = ["rgpd", "dpo", "privacy", "donnees", "cnil", "recrut", "job", "emploi", "candidat", "rh", "sav",
              "noreply", "no-reply", "nepasrepondre", "webmaster", "factur", "compta", "commande", "support",
              "abuse", "postmaster", "billing", "invoice", "service-client", "serviceclient", "reservation"]
MOTS_PAGES = ["contact", "presse", "press", "media", "partenari", "partner", "mentions", "legal",
              "qui-sommes", "about", "a-propos", "apropos", "equipe", "communication"]
CHEMINS = ["contact", "nous-contacter", "contactez-nous", "contact-us", "presse", "espace-presse", "press",
           "presse-medias", "medias", "fr/contact", "fr/presse", "mentions-legales", "fr/mentions-legales",
           "partenariats", "partenaires", "espace-pro", "professionnels", "groupes"]
RECHERCHE = {"hs": False}   # la recherche Google de Gemini est coupée pour la journée dès qu'elle échoue


# --------------------------------------------------------------------------
# État
# --------------------------------------------------------------------------
def charger():
    try:
        with open(FICHIER_ETAT, encoding="utf-8") as f:
            e = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        e = {}
    e.setdefault("active", True)
    e.setdefault("telegram_chat", None)
    e.setdefault("telegram_offset", 0)
    e.setdefault("telegram_messages", {})
    e.setdefault("jours_generes", [])
    e.setdefault("contacts", [])
    e.setdefault("mails_traites", [])
    e.setdefault("mails_divers", {})   # id court -> Message-ID (rien d'autre : le dépôt est public)
    e.setdefault("brouillons", {})
    e.setdefault("mails_depuis", None)
    return e


def sauver(e):
    e["mails_traites"] = e["mails_traites"][-500:]
    e["jours_generes"] = e["jours_generes"][-60:]
    if len(e["mails_divers"]) > 80:
        e["mails_divers"] = dict(list(e["mails_divers"].items())[-80:])
    if len(e["telegram_messages"]) > 300:
        e["telegram_messages"] = dict(list(e["telegram_messages"].items())[-300:])
    with open(FICHIER_ETAT, "w", encoding="utf-8") as f:
        json.dump(e, f, ensure_ascii=False, indent=1)


def maintenant():
    return dt.datetime.now(PARIS)


def iso(d):
    return d.isoformat(timespec="minutes")


def lire_date(s):
    return dt.datetime.fromisoformat(s)


def domaine_base(domaine):
    domaine = (domaine or "").lower().split(":")[0]
    if domaine.startswith("www."):
        domaine = domaine[4:]
    parties = domaine.split(".")
    if len(parties) >= 3 and parties[-2] in ("gouv", "co", "com", "asso", "org"):
        return ".".join(parties[-3:])
    return ".".join(parties[-2:])


def domaine_mail(adresse):
    return adresse.split("@")[-1].lower().strip(">").strip()


# --------------------------------------------------------------------------
# Telegram
# --------------------------------------------------------------------------
def tg(methode, **params):
    if not C.TELEGRAM_TOKEN:
        return None
    try:
        r = requests.post(f"https://api.telegram.org/bot{C.TELEGRAM_TOKEN}/{methode}", json=params, timeout=40)
        return r.json()
    except Exception as ex:
        print(f"Telegram : {ex}")
        return None


def dire(e, texte, lien=None, boutons=None):
    """Envoie un message Telegram ; `lien` = (id_contact, type) pour savoir à quoi il répond ;
    `boutons` = [(libellé, données), …] affichés sous le message."""
    if not e.get("telegram_chat"):
        print("(Telegram pas encore relié) " + texte[:200])
        return None
    extra = {"reply_markup": {"inline_keyboard": [[{"text": t, "callback_data": d} for t, d in boutons]]}} if boutons else {}
    rep = tg("sendMessage", chat_id=e["telegram_chat"], text=texte[:4000], disable_web_page_preview=True, **extra)
    if rep and rep.get("ok") and lien:
        e["telegram_messages"][str(rep["result"]["message_id"])] = {"contact": lien[0], "type": lien[1]}
    return rep


def contact(e, cid):
    return next((c for c in e["contacts"] if c["id"] == cid), None)


def _mises_a_jour(e):
    """Messages Telegram à traiter. Depuis l'étape 6, c'est le Sheet (horloge, toutes les 5 min) qui lit Telegram
    et nous transmet la file ; sinon on lit Telegram directement."""
    if C.SHEET_URL and C.SHEET_SECRET:
        try:
            from . import sheet
            file = sheet.appel("tgInbox")
            if file is not None:
                return file
        except Exception as ex:
            print(f"File Telegram du Sheet indisponible ({ex}), lecture directe.")
    rep = tg("getUpdates", offset=e["telegram_offset"] + 1, timeout=0)
    if not rep or not rep.get("ok"):
        return []
    for u in rep["result"]:
        e["telegram_offset"] = max(e["telegram_offset"], u["update_id"])
    return rep["result"]


def _action_synthetique(e, u):
    """Boutons des aperçus de prospection, relayés par le Sheet."""
    if "bouton" in u:
        action, _, cid = u["bouton"].partition(":")
        c = contact(e, cid)
        if c and action == "ps":
            if c["statut"] == "planifié":
                c["statut"] = "annulé"
            else:
                dire(e, f"Trop tard, le mail à {c['organisation']} est déjà parti.")
        return True
    if "modif_prospect" in u:
        c = contact(e, u["modif_prospect"])
        if c:
            traiter_consigne(e, c, "apercu", "modif : " + u.get("texte", ""))
        return True
    return False


def lire_telegram(e):
    for u in _mises_a_jour(e):
        try:
            if _action_synthetique(e, u):
                continue
        except Exception as ex:
            dire(e, f"⚠️ Problème : {ex}")
            continue
        m = u.get("message")
        if not m or "text" not in m:
            continue
        chat = m["chat"]["id"]
        texte = m["text"].strip()
        if not e["telegram_chat"]:
            e["telegram_chat"] = chat
            dire(e, "✅ C'est relié ! Ici tu recevras :\n• l'aperçu de chaque mail avant envoi (réponds « stop » "
                    "à un aperçu pour l'annuler) ;\n• les réponses des contacts, avec un brouillon : réponds « ok » "
                    "pour l'envoyer, ou écris ce qu'il faut changer : rien ne part sans « ok ».\n\n"
                    "Commandes : /liste, /pause, /reprise")
            continue
        if chat != e["telegram_chat"]:
            continue  # quelqu'un d'autre : on ignore
        bas = texte.lower()
        if bas.startswith("/liste"):
            dire(e, resume_semaine(e))
            continue
        if bas.startswith("/pause"):
            e["active"] = False
            dire(e, "⏸ Prospection en pause : plus aucun nouveau mail (les réponses restent suivies). /reprise pour relancer.")
            continue
        if bas.startswith("/reprise"):
            e["active"] = True
            dire(e, "▶️ Prospection relancée.")
            continue
        cible = m.get("reply_to_message")
        lien = e["telegram_messages"].get(str(cible["message_id"])) if cible else None
        if lien and lien["type"] == "mail":
            try:
                traiter_mail(e, lien["contact"], texte)
            except Exception as ex:
                dire(e, f"⚠️ Problème : {ex}")
            continue
        if not lien:
            dire(e, "Réponds directement au message concerné (appui long → Répondre), sinon je ne sais pas de quel contact tu parles.")
            continue
        c = contact(e, lien["contact"])
        if not c:
            continue
        try:
            traiter_consigne(e, c, lien["type"], texte)
        except Exception as ex:
            dire(e, f"⚠️ Problème : {ex}")


def traiter_consigne(e, c, type_, texte):
    bas = texte.lower().strip(" .!")
    if type_ == "apercu":
        if bas in ("stop", "annule", "annuler", "non", "supprime", "supprimer"):
            if c["statut"] == "planifié":
                c["statut"] = "annulé"
                dire(e, f"🗑 Annulé : {c['organisation']}")
            elif c["statut"] == "annulé":
                pass  # déjà annulé (stop envoyé deux fois)
            else:
                dire(e, f"Trop tard, le mail à {c['organisation']} est déjà parti.")
        elif bas.startswith("modif"):
            if c["statut"] != "planifié":
                dire(e, "Trop tard, il est déjà parti.")
                return
            m = rediger(c, c.get("contexte", ""), consigne=texte.split(":", 1)[-1].strip())
            c["objet"], c["corps"] = m["objet"], m["corps"]
            envoyer_apercu(e, c)
        else:
            dire(e, "Pour un aperçu : « stop » pour annuler, ou « modif : ta consigne » pour le réécrire.")
        return
    # type "reponse" : un contact nous a répondu
    if bas in ("ok", "oui", "go", "envoie", "envoyer", "valide"):
        corps = c.get("brouillon")
        if not corps:
            dire(e, "Je n'ai plus de brouillon pour ce contact, écris ta réponse.")
            return
    else:
        # Sécurité : rien ne part sans « ok ». Le texte de Mathis (consigne OU réponse rédigée) donne un brouillon.
        consigne = texte.split(":", 1)[-1].strip() if bas.startswith("modif") else texte
        b = brouillon_reponse(c, c.get("derniere_reponse", ""),
                              consigne="Voici ce que Mathis veut dire ou changer (consigne, ou réponse à remettre au propre "
                                       "sans en changer le sens) : " + consigne)
        c["brouillon"] = b["brouillon"]
        dire(e, f"✍️ Brouillon pour {c['organisation']} (rien n'est parti) :\n\n{b['brouillon']}\n\n"
                "👉 « ok » pour l'envoyer, sinon écris ce qu'il faut changer.", (c["id"], "reponse"))
        return
    envoyer_reponse(c, corps)
    c["brouillon"] = None
    c.pop("derniere_reponse", None)  # on ne garde pas leurs messages dans le suivi
    c["statut"] = "en discussion"
    dire(e, f"✅ Réponse envoyée à {c['organisation']} ({c['repondre_a']}).")


def resume_semaine(e):
    debut = maintenant() - dt.timedelta(days=7)
    recents = [c for c in e["contacts"] if lire_date(c["cree_le"]) >= debut]
    lignes = [f"📊 7 derniers jours : {len(recents)} contacts"]
    for c in recents[-20:]:
        lignes.append(f"• {c['organisation']} — {c['statut']}")
    enc = [c for c in e["contacts"] if c["statut"] in ("répondu", "en discussion")]
    if enc:
        lignes.append("\n💬 En discussion : " + ", ".join(c["organisation"] for c in enc[-10:]))
    if not e["active"]:
        lignes.append("\n⏸ (en pause)")
    return "\n".join(lignes)


# --------------------------------------------------------------------------
# Mails (Zimbra : SMTP pour envoyer, IMAP pour lire)
# --------------------------------------------------------------------------
def _smtp():
    ctx = ssl.create_default_context()
    try:
        s = smtplib.SMTP_SSL(C.MAIL_SERVEUR, 465, context=ctx, timeout=60)
    except (OSError, smtplib.SMTPException):
        s = smtplib.SMTP(C.MAIL_SERVEUR, 587, timeout=60)
        s.starttls(context=ctx)
    s.login(C.MAIL_UTILISATEUR, C.MAIL_MOT_DE_PASSE)
    return s


def _ranger_dans_envoyes(msg):
    try:
        im = imaplib.IMAP4_SSL(C.MAIL_SERVEUR, 993)
        im.login(C.MAIL_UTILISATEUR, C.MAIL_MOT_DE_PASSE)
        for dossier in ("Sent", "Envoyés", "INBOX.Sent", "Sent Items"):
            typ, _ = im.append(f'"{dossier}"', "\\Seen", imaplib.Time2Internaldate(time.time()), msg.as_bytes())
            if typ == "OK":
                break
        im.logout()
    except Exception as ex:
        print(f"Copie dans « Envoyés » impossible : {ex}")


def envoyer(dest, objet, corps, piece_jointe=False, en_reponse_a=None, references=None):
    msg = EmailMessage()
    msg["From"] = email.utils.formataddr((C.EXPEDITEUR_NOM, C.MAIL_UTILISATEUR))
    msg["To"] = dest
    msg["Subject"] = objet
    msg["Date"] = email.utils.formatdate(localtime=True)
    msg["Message-ID"] = email.utils.make_msgid(domain=domaine_mail(C.MAIL_UTILISATEUR))
    if en_reponse_a:
        msg["In-Reply-To"] = en_reponse_a
        msg["References"] = " ".join(x for x in [references, en_reponse_a] if x)
    msg.set_content(corps)
    if piece_jointe and os.path.exists(C.MEDIA_KIT):
        with open(C.MEDIA_KIT, "rb") as f:
            msg.add_attachment(f.read(), maintype="application", subtype="pdf",
                               filename="Media Kit – Le Média Français.pdf")
    s = _smtp()
    s.send_message(msg)
    s.quit()
    _ranger_dans_envoyes(msg)
    return msg["Message-ID"]


def envoyer_reponse(c, corps):
    objet = c.get("objet_reponse") or c["objet"]
    if not objet.lower().startswith("re"):
        objet = "Re: " + objet
    mid = envoyer(c["repondre_a"], objet, corps, en_reponse_a=c.get("reponse_id"),
                  references=c.get("message_id"))
    c.setdefault("echanges", []).append({"le": iso(maintenant()), "sens": "envoyé", "id": mid})


def _decoder(valeur):
    try:
        return str(make_header(decode_header(valeur or "")))
    except Exception:
        return valeur or ""


def _texte(msg):
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain" and "attachment" not in str(part.get("Content-Disposition")):
                return part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", "replace")
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                brut = part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", "replace")
                return html.unescape(re.sub(r"<[^>]+>", " ", brut))
        return ""
    brut = msg.get_payload(decode=True) or b""
    t = brut.decode(msg.get_content_charset() or "utf-8", "replace")
    return html.unescape(re.sub(r"<[^>]+>", " ", t)) if msg.get_content_type() == "text/html" else t


def _sans_citation(t):
    lignes = []
    for l in t.splitlines():
        if re.match(r"^\s*>", l):
            continue
        if re.match(r"^\s*(Le .{1,120} a écrit|On .{1,120} wrote|-----\s*Original|De\s*:|From\s*:)", l):
            break
        lignes.append(l)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lignes)).strip()


def lire_reponses(e):
    envoyes = [c for c in e["contacts"] if c.get("message_id")]
    if not e["mails_depuis"]:
        e["mails_depuis"] = iso(maintenant())  # on ne remonte pas les vieux mails à l'activation
    depuis_activation = lire_date(e["mails_depuis"])
    par_id = {}
    for c in envoyes:
        par_id[c["message_id"]] = c
        if c.get("relance_id"):
            par_id[c["relance_id"]] = c
        for x in c.get("echanges", []):
            par_id[x["id"]] = c
    im = imaplib.IMAP4_SSL(C.MAIL_SERVEUR, 993)
    im.login(C.MAIL_UTILISATEUR, C.MAIL_MOT_DE_PASSE)
    im.select("INBOX", readonly=True)
    depuis = (maintenant() - dt.timedelta(days=12)).strftime("%d-%b-%Y")
    typ, data = im.search(None, f'(SINCE "{depuis}")')
    for num in (data[0].split() if typ == "OK" else [])[-200:]:
        typ, brut = im.fetch(num, "(BODY.PEEK[])")
        if typ != "OK" or not brut or not isinstance(brut[0], tuple):
            continue
        msg = email.message_from_bytes(brut[0][1])
        mid = (msg.get("Message-ID") or "").strip()
        if not mid or mid in e["mails_traites"]:
            continue
        e["mails_traites"].append(mid)
        exp = email.utils.parseaddr(msg.get("From", ""))[1].lower()
        if exp == C.MAIL_UTILISATEUR.lower():
            continue
        refs = (msg.get("In-Reply-To", "") + " " + msg.get("References", "")).split()
        corps = _texte(msg)
        c = next((par_id[r] for r in refs if r in par_id), None)
        # rebond (adresse qui n'existe pas)
        if re.search(r"mailer-daemon|postmaster", exp) or "delivery status notification" in msg.get("Subject", "").lower():
            c = c or next((x for x in envoyes if x["email"].lower() in corps.lower()), None)
            if c and c["statut"] in ("envoyé", "relancé"):
                c["statut"] = "adresse invalide"
                dire(e, f"↩️ Adresse invalide pour {c['organisation']} ({c['email']}). Je passe à d'autres cibles.")
            continue
        if not c:
            c = next((x for x in envoyes if x["email"].lower() == exp), None)
        if not c:
            d = domaine_base(domaine_mail(exp))
            if d not in {domaine_base(x) for x in MESSAGERIES_PERSO}:
                c = next((x for x in reversed(envoyes) if domaine_base(domaine_mail(x["email"])) == d), None)
        if not c:
            try:
                quand = email.utils.parsedate_to_datetime(msg.get("Date"))
            except Exception:
                quand = None
            if quand and quand.tzinfo and quand < depuis_activation:
                continue
            signaler_mail(e, msg, exp, mid, corps)
            continue
        objet = _decoder(msg.get("Subject"))
        auto = (msg.get("Auto-Submitted", "no").lower() != "no" or msg.get("X-Autoreply") or msg.get("X-Autorespond")
                or re.search(r"absence|out of office|automatic reply|réponse automatique|autoreply|congés", objet, re.I))
        if auto:
            continue  # message d'absence : on ne compte pas comme une réponse
        texte = _sans_citation(corps)[:3000]
        c["repondre_a"] = exp
        c["reponse_id"] = mid
        c["objet_reponse"] = objet
        c["derniere_reponse"] = texte
        c.setdefault("echanges", []).append({"le": iso(maintenant()), "sens": "reçu", "id": mid})
        if re.fullmatch(r"\W*stop\W*", texte.strip().split("\n")[0], re.I):
            c["statut"] = "stop"
            dire(e, f"🚫 {c['organisation']} a répondu STOP : plus aucun mail ne leur sera envoyé.")
            continue
        c["statut"] = "répondu" if c["statut"] != "en discussion" else c["statut"]
        try:
            b = brouillon_reponse(c, texte)
        except Exception as ex:
            b = {"resume": "(résumé indisponible)", "brouillon": None}
            print(ex)
        c["brouillon"] = b.get("brouillon")
        message = (f"📩 {c['organisation']} a répondu ({exp})\n\n🧭 {b.get('resume', '')}\n\n"
                   f"— Leur message —\n{texte[:1500]}\n\n")
        if c["brouillon"]:
            message += (f"✍️ Brouillon de réponse :\n\n{c['brouillon']}\n\n"
                        "👉 Réponds à CE message : « ok » pour l'envoyer, ou écris ce qu'il faut changer. "
                        "Rien ne part sans « ok ».")
        else:
            message += "👉 Réponds à CE message avec ce que tu veux dire : je te montre le mail avant, rien ne part sans « ok »."
        dire(e, message, (c["id"], "reponse"))
    im.logout()


def _est_masse(msg, exp):
    """Newsletters, notifications, pubs, spams : pas de message Telegram."""
    if msg.get("List-Unsubscribe") or msg.get("List-Id"):
        return True
    if (msg.get("Precedence", "").lower() in ("bulk", "list", "junk")
            or msg.get("X-Spam-Flag", "").lower() == "yes"
            or msg.get("Auto-Submitted", "no").lower() != "no"):
        return True
    return bool(re.search(r"no-?reply|ne-?pas-?repondre|newsletter|notification|mailer|news@|marketing@|"
                          r"bounce|alert|digest", exp))


def signaler_mail(e, msg, exp, mid, corps):
    if _est_masse(msg, exp):
        return
    nom = email.utils.parseaddr(msg.get("From", ""))[0] or exp
    objet = _decoder(msg.get("Subject"))
    apercu = re.sub(r"\s+", " ", _sans_citation(corps)).strip()[:500]
    cle = f"m{random.randint(10000, 99999)}"
    e["mails_divers"][cle] = mid
    dire(e, f"✉️ Nouveau mail de {nom} <{exp}>\nObjet : {objet}\n\n{apercu}\n\n"
            "👉 Réponds à CE message : « brouillon », ou écris ce que tu veux répondre. Je te montre le mail avant, rien ne part sans « ok ».",
         (cle, "mail"))


def _relire_mail(mid):
    im = imaplib.IMAP4_SSL(C.MAIL_SERVEUR, 993)
    im.login(C.MAIL_UTILISATEUR, C.MAIL_MOT_DE_PASSE)
    im.select("INBOX", readonly=True)
    typ, data = im.search(None, f'(HEADER Message-ID "{mid}")')
    msg = None
    if typ == "OK" and data[0]:
        typ, brut = im.fetch(data[0].split()[-1], "(BODY.PEEK[])")
        if typ == "OK" and brut and isinstance(brut[0], tuple):
            msg = email.message_from_bytes(brut[0][1])
    im.logout()
    if not msg:
        raise RuntimeError("mail introuvable dans la boîte (supprimé ou déplacé ?)")
    return msg


def traiter_mail(e, cle, texte):
    mid = e["mails_divers"].get(cle)
    if not mid:
        dire(e, "Je ne retrouve plus ce mail.")
        return
    msg = _relire_mail(mid)
    exp = email.utils.parseaddr(msg.get("From", ""))[1]
    objet = _decoder(msg.get("Subject"))
    bas = texte.lower().strip(" .!")
    if bas not in ("ok", "oui", "go", "envoie", "envoyer", "valide"):
        # Sécurité : rien ne part sans « ok ». Tout autre texte (consigne ou réponse rédigée) donne un brouillon.
        if bas.startswith("brouillon") or bas.startswith("modif"):
            consigne = texte.split(":", 1)[-1].strip() if ":" in texte else ""
        else:
            consigne = ("voici ce que Mathis veut dire ou changer (consigne, ou réponse à remettre au propre "
                        "sans en changer le sens) : " + texte)
        if e["brouillons"].get(cle):
            consigne += "\nBrouillon précédent à reprendre : " + e["brouillons"][cle][:1500]
        prompt = f"""Mathis Pouchol, fondateur du Média Français (média Instagram de 210 000 abonnés sur l'histoire et le
patrimoine français), a reçu ce mail de {exp} (objet : {objet}) :
\"\"\"{_sans_citation(_texte(msg))[:3000]}\"\"\"

Informations utiles si c'est une demande de partenariat :
{C.CHIFFRES}

Écris sa réponse : précise, courtoise, 30 à 140 mots ; s'il manque une information, pose la question au lieu d'inventer.
Termine par « Bien cordialement, » puis :
{C.SIGNATURE}
{REGLES_STYLE}
{('Consigne de Mathis : ' + consigne) if consigne else ''}
Réponds en JSON : {{"brouillon": "..."}}"""
        b = gemini.json_(prompt, temperature=0.5)["brouillon"]
        e["brouillons"][cle] = b
        dire(e, f"✍️ Brouillon pour {exp} (rien n'est parti) :\n\n{b}\n\n"
                "👉 « ok » pour l'envoyer, sinon écris ce qu'il faut changer.", (cle, "mail"))
        return
    corps = e["brouillons"].get(cle)
    if not corps:
        dire(e, "Pas de brouillon pour ce mail : écris ce que tu veux répondre, je te prépare un brouillon.")
        return
    if not objet.lower().startswith("re"):
        objet = "Re: " + objet
    envoyer(exp, objet, corps, en_reponse_a=mid, references=msg.get("References"))
    e["brouillons"].pop(cle, None)
    dire(e, f"✅ Réponse envoyée à {exp}.")


# --------------------------------------------------------------------------
# Recherche des cibles et des adresses
# --------------------------------------------------------------------------
def deja_vus(e):
    noms = [x.lower() for x in C.EXCLUS] + [c["organisation"].lower() for c in e["contacts"]]
    domaines = {domaine_base(urlparse(c["site"]).netloc) for c in e["contacts"] if c.get("site")}
    return noms, domaines


def quotas_du_jour():
    n_t = round(C.MAILS_PAR_JOUR * C.PART_TOURISME)
    return {"tourisme": n_t, "industrie": C.MAILS_PAR_JOUR - n_t}


# ---------------------------------------------------------------------------
# Liste des exposants du MIF Expo 2026 : prioritaire tant qu'il en reste
# ---------------------------------------------------------------------------
MIF_URL = ("https://www.mifexpo.fr/wp-admin/admin-ajax.php?lang=fr&action=ajax_search_exposants&pavillon_id=NaN"
           "&region_id=NaN&secteur_id=NaN&salon_id=952944&actionsearch=true")
MIF_POIDS = {"80": 3, "92": 3, "95": 3, "96": 3, "98": 3, "97": 2, "62": 3, "68": 3, "69": 4, "66": 2, "67": 1,
             "38": 1, "48": 2, "108": 4, "93": 2, "94": 1, "103": 1, "72": 1}
MIF_MOTS = re.compile(r"knife|knives|couteau|coutell|watch|horlog|pipe|whisk|rum\b|beer|bière|brasser|cognac|calvados|"
                      r"spirit|distill|moto|bike|vélo|velo|outdoor|hunting|chasse|navy|marine|military|militaire|"
                      r"aviation|guitar|billiard|leather|cuir|boots|bottes|béret|beret|\bhat|chapeau|denim|jeans|"
                      r"tourism|tourist|attractivit", re.I)
MIF_EXCLUS = re.compile(r"women|baby|bébé|child|enfant|lingerie|makeup", re.I)


def cibles_mif(e):
    """Exposants du MIF Expo 2026 qui parlent à notre public, du plus pertinent au moins pertinent.
    La liste est téléchargée une fois puis gardée dans le suivi (data/prospection.json)."""
    if e.get("cibles_mif"):
        return e["cibles_mif"]
    page = requests.get(MIF_URL, headers=NAVIGATEUR, timeout=60).text
    liste = []
    for bloc in re.split(r'(?=<div data-exposantid=)', page)[1:]:
        nom = html.unescape((re.search(r'data-name="([^"]*)"', bloc) or [None, ""])[1]).strip()
        site = (re.search(r'class="exposants-site">\s*<a href="([^"]+)"', bloc) or [None, ""])[1].strip()
        produits = html.unescape((re.search(r'data-produits="([^"]*)"', bloc) or [None, ""])[1])
        desc = html.unescape((re.search(r'data-produits-desc="([^"]*)"', bloc) or [None, ""])[1])
        secteurs = re.findall(r"##(\d+)##", (re.search(r'data-secteurs="([^"]*)"', bloc) or [None, ""])[1])
        if not nom or not re.match(r"^https?://(www\.)?[a-z0-9-]+(\.[a-z0-9-]+)*\.[a-z]{2,}(/|$)", site, re.I):
            continue
        note = sum(MIF_POIDS.get(x, 0) for x in secteurs) + (4 if MIF_MOTS.search(produits + " " + desc) else 0) \
            - (4 if MIF_EXCLUS.search(produits) else 0)
        if note >= 5:
            liste.append({"organisation": nom.title() if nom.isupper() else nom, "site": site, "note": note,
                          "type": "marque exposante au MIF Expo 2026", "famille": "mif",
                          "idee": f"Marque exposante au MIF Expo 2026 (salon du Made in France, Paris). Produits : {produits[:200]}"})
    liste.sort(key=lambda x: -x["note"])
    if liste:
        e["cibles_mif"] = liste
    print(f"Liste MIF Expo : {len(liste)} exposants retenus")
    return liste


def prochaines_cibles_mif(e, nb):
    noms, domaines = deja_vus(e)
    domaines |= set(e.get("mif_essayes", []))   # exposants déjà explorés sans adresse fiable : on ne les retente pas
    exclus = [x.lower() for x in C.EXCLUS]
    choisies = []
    for c in cibles_mif(e):
        d = domaine_base(urlparse(c["site"]).netloc)
        nom = c["organisation"].lower()
        if d in domaines or nom in noms or any(x in nom or nom in x for x in exclus):
            continue
        choisies.append(dict(c))
        if len(choisies) >= nb:
            break
    return choisies


def proposer_cibles(e, nb=20):
    jour = maintenant().toordinal()
    tour = [C.CATEGORIES_TOURISME[(jour * 3 + i) % len(C.CATEGORIES_TOURISME)] for i in range(3)]
    indu = [C.CATEGORIES_INDUSTRIE[(jour * 2 + i) % len(C.CATEGORIES_INDUSTRIE)] for i in range(2)]
    nb_t = round(nb * 0.6)
    noms, _ = deja_vus(e)
    prompt = f"""Tu aides le média Instagram « Le Média Français » (histoire, patrimoine, savoir-faire et marques
françaises ; 210 000 abonnés, 80 % de 18-34 ans, 75 % d'hommes) à trouver des partenaires qui paient
des posts sponsorisés ou des campagnes.

Propose {nb} organisations françaises différentes :
- {nb_t} dans le TOURISME et le PATRIMOINE, réparties entre :
{chr(10).join('  - ' + x for x in tour)}
- {nb - nb_t} dans l'INDUSTRIE, réparties entre :
{chr(10).join('  - ' + x for x in indu)}

Règles :
- organisations qui existent vraiment, actives aujourd'hui, avec un site officiel et un budget communication ;
- EN PRIORITÉ celles qui travaillent déjà avec des créateurs de contenu ou des médias Instagram (posts sponsorisés,
  campagnes d'influence, accueils d'influenceurs, offices de tourisme qui invitent des créateurs) : ce sont
  elles qui répondent ;
- pas de musée national, d'association, de festival culturel ni de maison d'édition ;
- privilégie les tailles moyennes et les acteurs régionaux (plus de chances de réponse) aux géants mondiaux ;
- évite toute organisation en difficulté financière connue, controversée, politique ou religieuse ;
- n'en propose AUCUNE de cette liste (déjà contactées) : {', '.join(sorted(set(noms)))[:3500]}

Réponds en JSON : {{"cibles": [{{"organisation": "...", "famille": "tourisme ou industrie", "type": "...",
"site": "https://... (page d'accueil officielle)",
"idee": "une idée de contenu en une phrase qui ferait le lien entre eux et l'histoire de France"}}]}}"""
    r = gemini.json_(prompt, temperature=0.9)
    cibles = r.get("cibles", [])
    for c in cibles:
        c["famille"] = "industrie" if "indus" in str(c.get("famille", "")).lower() else "tourisme"
    return cibles


def _cfemail(code):
    try:
        k = int(code[:2], 16)
        return "".join(chr(int(code[i:i + 2], 16) ^ k) for i in range(2, len(code), 2))
    except ValueError:
        return ""


def _emails(html_brut):
    trouves = set()
    for code in re.findall(r'data-cfemail="([0-9a-fA-F]+)"', html_brut):
        trouves.add(_cfemail(code))
    t = html.unescape(html_brut)
    t = re.sub(r"\s*[\[\(\{]\s*(at|arobase)\s*[\]\)\}]\s*", "@", t, flags=re.I)
    t = re.sub(r"\s*[\[\(\{]\s*(dot|point)\s*[\]\)\}]\s*", ".", t, flags=re.I)
    for m in re.findall(r"mailto:([^\"'?>\s]+)", t, flags=re.I):
        trouves.add(m)
    for m in re.findall(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", t):
        trouves.add(m)
    propres = set()
    for a in trouves:
        a = re.sub(r"^(\\?u003[ce]|\\?x3[ce]|%3[ce]|3e|3c)+", "", a.strip(), flags=re.I)
        a = a.strip().strip(".").lower()
        if re.search(r"\.(png|jpe?g|gif|svg|webp|css|js)$", a) or "example" in a or "sentry" in a:
            continue
        if re.fullmatch(r"[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}", a):
            propres.add(a)
    return propres


def _texte_page(html_brut):
    t = re.sub(r"(?is)<(script|style|noscript|svg).*?</\1>", " ", html_brut)
    t = re.sub(r"<[^>]+>", " ", t)
    return re.sub(r"\s+", " ", html.unescape(t)).strip()


def _note(adresse):
    local = adresse.split("@")[0].replace(".", "").replace("-", "").replace("_", "")
    if any(x in local for x in INTERDITES):
        return -1
    if any(local.startswith(x) or x in local for x in BONNES if len(x) > 3) or local in ("rp", "com"):
        return 3
    if any(x in local for x in MOYENNES):
        return 2
    if re.fullmatch(r"[a-z]+[a-z]+", local) and "." in adresse.split("@")[0]:
        return 1  # prenom.nom@ publié sur le site
    return 1


def _mx_ok(domaine):
    try:
        import dns.resolver
        return bool(dns.resolver.resolve(domaine, "MX", lifetime=10))
    except ImportError:
        return True
    except Exception:
        return False


def explorer_site(site):
    """Renvoie (adresses trouvées avec leur contexte, texte du site)."""
    if not site.startswith("http"):
        site = "https://" + site
    base = domaine_base(urlparse(site).netloc)
    vues, a_voir, textes, adresses = set(), [site], [], {}
    try:
        r = requests.get(site, headers=NAVIGATEUR, timeout=20)
        accueil = r.text
        site = r.url
    except requests.RequestException:
        return {}, ""
    liens = re.findall(r'<a[^>]+href="([^"#]+)"[^>]*>(.*?)</a>', accueil, flags=re.I | re.S)
    for href, txt in liens:
        cible = urljoin(site, href)
        etiquette = (href + " " + _texte_page(txt)).lower()
        if domaine_base(urlparse(cible).netloc) == base and any(m in etiquette for m in MOTS_PAGES):
            a_voir.append(cible)
    a_voir += [urljoin(site, "/" + ch) for ch in CHEMINS]
    try:  # plan du site : on y repère les pages contact / presse
        plan = requests.get(urljoin(site, "/sitemap.xml"), headers=NAVIGATEUR, timeout=8).text
        a_voir[1:1] = [u for u in re.findall(r"<loc>([^<]+)</loc>", plan)
                       if any(m in u.lower() for m in ("contact", "presse", "press", "partenai"))][:4]
    except requests.RequestException:
        pass
    pages = 0
    for url in a_voir:
        if url in vues or pages >= 9:
            continue
        vues.add(url)
        try:
            contenu = accueil if url == a_voir[0] else requests.get(url, headers=NAVIGATEUR, timeout=10).text
        except requests.RequestException:
            continue
        pages += 1
        texte = _texte_page(contenu)
        if url == a_voir[0] or len(textes) < 3:
            textes.append(texte[:2500])
        for a in _emails(contenu):
            d = domaine_base(domaine_mail(a))
            if d != base and base.split(".")[0] not in d and d.split(".")[0] not in base:
                continue  # adresse d'un autre site (agence, hébergeur…)
            i = texte.lower().find(a)
            ctx = texte[max(0, i - 300): i + 100] if i >= 0 else ""
            adresses.setdefault(a, ctx)
    return adresses, "\n\n".join(textes)[:6000]


def trouver_adresse(cible):
    adresses, texte = explorer_site(cible["site"])
    def note(a):
        n = _note(a)
        ctx = (adresses.get(a) or "").lower()
        local = a.split("@")[0]
        # une personne nommée, responsable com / marketing / presse / partenariats : c'est elle qu'on veut joindre
        if n >= 0 and "." in local and re.search(r"communication|marketing|presse|press|partenariat|relations|"
                                                  r"influence|brand|digital|réseaux sociaux|social media", ctx):
            return 5
        return n
    notees = sorted(((note(a), a) for a in adresses), reverse=True)
    notees = [(n, a) for n, a in notees if n >= 0 and _mx_ok(domaine_mail(a))]
    if notees:
        a = notees[0][1]
        return a, adresses[a], texte
    # Dernier essai : recherche Google via Gemini, acceptée seulement si l'adresse est sur le domaine officiel
    if RECHERCHE["hs"]:
        return None, "", texte
    try:
        base = domaine_base(urlparse(cible["site"]).netloc)
        r = gemini.json_(f"""Trouve l'adresse e-mail publique du service presse, communication, marketing ou
partenariats de « {cible['organisation']} » (site officiel : {cible['site']}).
Uniquement une adresse publiée par l'organisation elle-même. Si tu n'es pas certain, mets null.
Réponds en JSON : {{"email": "... ou null", "nom": "nom de la personne si l'adresse est nominative, sinon null"}}""",
                         temperature=0.1, recherche=True, rapide=True)
        a = (r.get("email") or "").lower().strip()
        if a and domaine_base(domaine_mail(a)) == base and _note(a) >= 0 and _mx_ok(domaine_mail(a)):
            return a, f"Contact : {r.get('nom') or ''} {a}", texte
    except Exception as ex:
        print(f"Recherche d'adresse ratée pour {cible['organisation']} : {ex}")
        if isinstance(ex, gemini.ErreurGemini):
            RECHERCHE["hs"] = True
    return None, "", texte


# --------------------------------------------------------------------------
# Rédaction
# --------------------------------------------------------------------------
REGLES_STYLE = """Style : français impeccable, vouvoiement, ton direct et chaleureux, phrases courtes, aucun emoji,
aucune formule creuse (« je me permets », « n'hésitez pas »), aucun superlatif gratuit.
N'invente RIEN sur l'organisation : n'utilise que ce qui figure dans le texte de leur site, ou des faits historiques
très connus et certains."""


def rediger(c, contexte, consigne=""):
    prompt = f"""Écris un premier mail de prospection de la part de Mathis Pouchol, fondateur du « Média Français »
(@lemediafrancais), média Instagram sur l'histoire, le patrimoine et les savoir-faire français.

Destinataire : {c['organisation']} ({c['type']}), adresse {c['email']}.
Contexte autour de l'adresse sur leur site : {c.get('contexte_adresse', '')[:400]}
Idée de départ : {c.get('idee', '')}
{"Point de contact : nous avons repéré leur marque parmi les exposants du MIF Expo 2026 (le salon du Made in France, Porte de Versailles, 12-15 novembre). Mentionne-le simplement en ouverture, sans dire qu'on les a rencontrés sur place. Proposition adaptée : un carrousel sponsorisé qui raconte leur fabrication en France et l'histoire de leur savoir-faire." if c.get("famille") == "mif" else ""}
Texte de leur site (pour personnaliser) :
\"\"\"{contexte[:5000]}\"\"\"

Nos chiffres (n'utilise que ceux-là, 2 ou 3 maximum, les plus parlants pour eux) :
{C.CHIFFRES}

Contenu du mail (le mail parle d'EUX et de ce qu'ils y gagnent, pas de Mathis) :
1. Salutation : « Bonjour Madame Nom, » ou « Bonjour Monsieur Nom, » UNIQUEMENT si le contexte nomme clairement
   la personne derrière cette adresse ; sinon « Bonjour, ».
2. Une phrase d'accroche sur EUX, factuelle et précise (un lieu, un produit, un pan de leur histoire tiré de leur site),
   sans compliment appuyé.
3. L'idée concrète tout de suite : l'histoire précise qu'on raconterait sur eux dans un carrousel, et ce que ça leur
   apporte (faire découvrir X à un public jeune, attirer des visiteurs, etc.).
   Marque ou entreprise : carrousel sponsorisé. Tourisme, site, territoire : collaboration, tournage sur place ou
   campagne d'attractivité (référence possible : la campagne faite avec la Région Normandie).
4. Le média en UNE phrase, présenté comme un média et non comme une personne : « Le Média Français, c'est … » avec
   2 chiffres maximum choisis pour eux. INTERDIT : « Je suis Mathis », « je m'appelle », « fondateur de ».
5. « Je vous joins notre media kit. » Aucun prix.
6. Une question de fin simple et légère (« Est-ce que ça vous parlerait d'en discuter 10 minutes ? »).
7. Termine par « Bien cordialement, » (la signature est ajoutée ensuite, ne l'écris pas).
Ton : sobre, direct, d'égal à égal, comme un mail qu'un humain écrit en 5 minutes. Pas de « captivant »,
« passionnant », « à merveille », « exceptionnel », « incarne ». Utilise « nous » pour le média, « je » au plus une fois.
Longueur : 80 à 130 mots. Objet : moins de 60 caractères, concret, qui donne envie d'ouvrir, avec leur nom ou le sujet
(ex. « Arcachon, la Ville d'Hiver de Napoléon III en carrousel ? »).
{REGLES_STYLE}
{('Consigne de Mathis pour cette version : ' + consigne) if consigne else ''}

Réponds en JSON : {{"objet": "...", "corps": "...", "nom_contact": "Madame/Monsieur Nom ou null"}}"""
    m = gemini.json_(prompt, temperature=0.7)
    m["corps"] = _avec_signature(m["corps"])
    return m


def _avec_signature(corps):
    """Coupe une éventuelle signature écrite par Gemini APRÈS « Bien cordialement » (jamais le corps du mail :
    « Je suis Mathis Pouchol… » doit rester !), puis ajoute la vraie signature."""
    corps = corps.strip()
    i = corps.rfind("Bien cordialement")
    if i >= 0:
        corps = corps[:i].rstrip() + "\n\nBien cordialement,"
    else:
        corps = re.sub(r"\n+Mathis Pouchol[^\n]*(\n[^\n]*){0,3}\s*\Z", "", corps).rstrip()
        corps = re.sub(r"\n+(très |bien )?cordialement,?\s*\Z", "", corps, flags=re.I).rstrip() + "\n\nBien cordialement,"
    return corps + "\n\n" + C.SIGNATURE


def _tronque(corps):
    return "Bien cordialement" not in (corps or "")


def brouillon_reponse(c, leur_message, consigne=""):
    prompt = f"""Mathis Pouchol (fondateur du Média Français) a écrit à {c['organisation']}.
Son premier mail :
\"\"\"{c['corps'][:2000]}\"\"\"

Ils viennent de répondre :
\"\"\"{leur_message[:3000]}\"\"\"

Informations utiles (media kit) :
{C.CHIFFRES}
Process : brief → proposition de sujet et des textes, validés avant publication → publication avec story et
diffusion TikTok → bilan complet (vues, comptes touchés, engagement). Réponse sous 24 h.

1. Résume leur réponse en une ou deux phrases pour Mathis (intérêt, question, demande, refus…).
2. Écris le brouillon de réponse de Mathis :
- répond précisément à ce qu'ils disent ou demandent ;
- s'ils demandent les tarifs, donne les offres du media kit ;
- s'ils proposent un appel ou un rendez-vous, accepte et propose deux créneaux en semaine en fin de journée
  (après 18 h) sans date précise inventée : « en début de semaine prochaine, après 18 h » ;
- s'il manque une information que tu ne connais pas, pose la question au lieu d'inventer ;
- s'ils refusent, remercie en deux lignes et laisse la porte ouverte ;
- 40 à 140 mots, termine par « Bien cordialement, » puis la signature :
{C.SIGNATURE}
{REGLES_STYLE}
{('Consigne de Mathis : ' + consigne) if consigne else ''}

Réponds en JSON : {{"resume": "...", "brouillon": "..."}}"""
    return gemini.json_(prompt, temperature=0.5)


def envoyer_apercu(e, c):
    prevu = lire_date(c["prevu_le"])
    quand = ("demain " if prevu.date() > maintenant().date() else "") + prevu.strftime("%H:%M")
    dire(e, f"📤 {quand} → {c['organisation']} ({c['email']})\nObjet : {c['objet']}\n\n{c['corps']}",
         (c["id"], "apercu"),
         boutons=[("✅ Valider", f"pv:{c['id']}"), ("✏️ Modifier", f"pm:{c['id']}"), ("🗑 Supprimer", f"ps:{c['id']}")])


def generer_du_jour(e, jour=None, apercus=True):
    """Prépare les mails d'un jour (par défaut aujourd'hui ; la veille au soir avec jour=demain)."""
    jour = jour or maintenant().date()
    noms, domaines = deja_vus(e)
    retenus, essais = [], 0
    heures = list(C.HEURES_ENVOI)[: C.MAILS_PAR_JOUR]
    quotas = quotas_du_jour()
    debut = time.time()
    while len(retenus) < C.MAILS_PAR_JOUR and essais < 4 and time.time() - debut < 15 * 60:
        essais += 1
        try:
            # d'abord les exposants du MIF Expo, jusqu'à la fin de la liste ; ensuite les cibles proposées par Gemini
            cibles = prochaines_cibles_mif(e, 15) if C.PRIORITE_MIF else []
            mif = bool(cibles)
            if not cibles:
                cibles = proposer_cibles(e)
        except Exception as ex:
            print(f"Propositions de cibles ratées : {ex}")
            mif = False
            try:
                cibles = proposer_cibles(e)
            except Exception:
                continue
        # 1) on écarte les déjà-vus, 2) on explore tous les sites en parallèle (bien plus rapide)
        a_explorer = []
        for cible in cibles:
            if not cible.get("site") or not cible.get("organisation"):
                continue
            d = domaine_base(urlparse(cible["site"] if "//" in cible["site"] else "https://" + cible["site"]).netloc)
            if d in domaines or cible["organisation"].lower() in noms:
                continue
            domaines.add(d)
            a_explorer.append(cible)
            if cible.get("famille") == "mif":
                e.setdefault("mif_essayes", []).append(d)
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=6) as pool:
            trouves = list(pool.map(trouver_adresse, a_explorer))
        for cible, (adresse, ctx, texte) in zip(a_explorer, trouves):
            if len(retenus) >= C.MAILS_PAR_JOUR or time.time() - debut > 15 * 60:
                break
            fam = cible.get("famille", "tourisme")
            if fam != "mif" and sum(1 for x in retenus if x.get("famille") == fam) >= quotas.get(fam, 0):
                continue  # quota du jour atteint pour cette famille (60 % tourisme, 40 % industrie)
            print(f"→ {cible['organisation']} ({cible['site']}) : {adresse or 'pas d adresse'}")
            if not adresse:
                print("   pas d'adresse fiable, cible suivante")
                continue
            c = {"id": f"{jour.isoformat()}-{len(retenus) + 1}-{random.randint(100, 999)}",
                 "organisation": cible["organisation"], "type": cible.get("type", ""), "site": cible["site"],
                 "idee": cible.get("idee", ""), "famille": cible.get("famille", "tourisme"), "email": adresse, "contexte_adresse": ctx, "contexte": texte[:3000],
                 "cree_le": iso(maintenant()), "statut": "planifié"}
            try:
                m = rediger(c, texte)
            except Exception as ex:
                print(f"   rédaction ratée : {ex}")
                continue
            hh, mm = map(int, heures[len(retenus)].split(":"))
            prevu = dt.datetime(jour.year, jour.month, jour.day, hh, mm, tzinfo=PARIS) + dt.timedelta(minutes=random.randint(0, 12))
            if prevu < maintenant() + dt.timedelta(minutes=30):
                prevu = maintenant() + dt.timedelta(minutes=30 + 40 * len(retenus))
            c.update({"objet": m["objet"], "corps": m["corps"], "nom_contact": m.get("nom_contact"),
                      "prevu_le": iso(prevu), "repondre_a": adresse})
            c["jeton"] = f"{random.getrandbits(48):012x}"
            e["contacts"].append(c)
            retenus.append(c)
            if apercus:
                envoyer_apercu(e, c)
            print(f"   ✓ {adresse}")
    e["jours_generes"].append(jour.isoformat())
    if apercus and len(retenus) < C.MAILS_PAR_JOUR:
        dire(e, f"ℹ️ Aujourd'hui je n'ai trouvé que {len(retenus)} adresse(s) fiable(s) sur {C.MAILS_PAR_JOUR}.")
    return retenus


def annulations_du_mail(e):
    """Prospects annulés depuis le mail récap (bouton « Annuler »)."""
    try:
        from . import sheet
        annules = sheet.appel("cancelledProspects") or []
    except Exception as ex:
        print(f"Annulations illisibles : {ex}")
        return
    for a in annules:
        c = contact(e, a.get("pid"))
        if c and c.get("jeton") == a.get("t") and c["statut"] == "planifié":
            c["statut"] = "annulé"
            print(f"Annulé depuis le mail : {c['organisation']}")


def envoyer_les_mails_dus(e):
    for c in e["contacts"]:
        if c["statut"] == "planifié" and lire_date(c["prevu_le"]) <= maintenant():
            try:
                c["message_id"] = envoyer(c["email"], c["objet"], c["corps"], piece_jointe=True)
                c["statut"] = "envoyé"
                c["envoye_le"] = iso(maintenant())
                c.pop("contexte", None)
                print(f"Envoyé : {c['organisation']}")
            except Exception as ex:
                c["statut"] = "erreur"
                c["erreur"] = str(ex)[:300]
                dire(e, f"⚠️ Envoi impossible à {c['organisation']} : {ex}")
            time.sleep(5)


def relancer(e):
    m = maintenant()
    if m.weekday() >= 5 or not (9 <= m.hour < 17):
        return
    for c in e["contacts"]:
        if c["statut"] != "envoyé" or not c.get("envoye_le"):
            continue
        if m - lire_date(c["envoye_le"]) < dt.timedelta(days=C.RELANCE_APRES_JOURS):
            continue
        salut = f"Bonjour {c['nom_contact']}," if c.get("nom_contact") else "Bonjour,"
        corps = (f"{salut}\n\nJe reviens vers vous au sujet de mon message de la semaine dernière. "
                 f"J'aurais vraiment aimé raconter une histoire liée à {c['organisation']} à notre communauté.\n\n"
                 "Auriez-vous quinze minutes pour en parler, ou pouvez-vous m'indiquer la bonne personne ?\n\n"
                 f"Bien cordialement,\n\n{C.SIGNATURE}")
        try:
            c["relance_id"] = envoyer(c["email"], "Re: " + c["objet"], corps,
                                      en_reponse_a=c["message_id"], references=c["message_id"])
            c["statut"] = "relancé"
            c["relance_le"] = iso(m)
        except Exception as ex:
            print(f"Relance ratée {c['organisation']} : {ex}")
        time.sleep(5)


# --------------------------------------------------------------------------
def reparer(e):
    """Mails coupés après « Je suis » (bug corrigé le 11/11) : on réécrit ceux qui sont prévus, et on envoie la
    version complète, avec un mot d'excuse, dans le même fil à ceux qui sont déjà partis."""
    m = maintenant()
    for c in e["contacts"]:
        c["email"] = re.sub(r"^(u003[ce]|x3[ce])+", "", c.get("email", ""))
        c["repondre_a"] = re.sub(r"^(u003[ce]|x3[ce])+", "", c.get("repondre_a", c["email"]))
        if not _tronque(c.get("corps")) or c.get("repare"):
            continue
        try:
            if c["statut"] == "planifié":
                nouveau = rediger(c, c.get("contexte", ""))
                c["objet"], c["corps"] = nouveau["objet"], nouveau["corps"]
                c["repare"] = True
                envoyer_apercu(e, c)
            elif c["statut"] in ("envoyé", "relancé") and c.get("message_id") and m.weekday() < 5 and 9 <= m.hour < 18:
                nouveau = rediger(c, c.get("contexte", ""))
                lignes = nouveau["corps"].split("\n", 1)
                corps = (lignes[0] + "\n\nMon précédent message est parti incomplet, toutes mes excuses. Le voici en entier :"
                         + ("\n" + lignes[1] if len(lignes) > 1 else ""))
                envoyer(c["email"], "Re: " + c["objet"], corps, piece_jointe=True,
                        en_reponse_a=c["message_id"], references=c["message_id"])
                c["corps"], c["repare"] = corps, True
                c["envoye_le"] = iso(m)  # la relance repart de zéro
                dire(e, f"🔁 Version complète renvoyée à {c['organisation']} (le 1er mail était coupé).")
                time.sleep(5)
        except Exception as ex:
            print(f"Réparation ratée pour {c['organisation']} : {ex}")


def tick():
    manque = [n for n in ("MAIL_SERVEUR", "MAIL_UTILISATEUR", "MAIL_MOT_DE_PASSE") if not getattr(C, n)]
    if manque:
        print("Prospection pas encore configurée (secrets manquants : " + ", ".join(manque) + ").")
        return
    C.CLE_2_EN_PREMIER = True  # la prospection consomme d'abord la 2e clé Gemini
    e = charger()
    try:
        lire_telegram(e)
        reparer(e)
        m = maintenant()
        if (C.PROSPECTION_ACTIVE and e["active"] and m.weekday() < 5 and 8 <= m.hour < 14
                and m.date().isoformat() not in e["jours_generes"]):
            try:
                generer_du_jour(e)
            except Exception as ex:
                e["jours_generes"].append(m.date().isoformat())
                dire(e, f"⚠️ Préparation des mails du jour ratée : {ex}")
        annulations_du_mail(e)
        envoyer_les_mails_dus(e)
        relancer(e)
        try:
            lire_reponses(e)
        except Exception as ex:
            print(f"Lecture de la boîte impossible : {ex}")
    finally:
        sauver(e)


def test():
    """Envoie un mail de test à toi-même et un message Telegram."""
    e = charger()
    lire_telegram(e)
    envoyer(C.MAIL_UTILISATEUR, "Test prospection – Le Média Français",
            "Si tu lis ce mail, l'envoi depuis GitHub fonctionne.\n\n" + C.SIGNATURE, piece_jointe=True)
    print("Mail de test envoyé.")
    dire(e, "🧪 Test : mail envoyé à ta propre adresse, regarde ta boîte Zimbra.")
    sauver(e)


if __name__ == "__main__":
    (test if len(sys.argv) > 1 and sys.argv[1] == "test" else tick)()
