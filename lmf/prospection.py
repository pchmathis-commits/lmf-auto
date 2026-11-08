"""Prospection automatique : trouve des partenaires, leur écrit, suit les réponses.

- du lundi au vendredi, 3 mails par jour (réglages dans config.py) ;
- l'adresse est cherchée sur le site officiel de chaque cible (jamais devinée) ;
- chaque mail est écrit pour la cible, avec le media kit en pièce jointe ;
- une relance au bout de 7 jours sans réponse ;
- les réponses arrivent sur Telegram avec un résumé et un brouillon :
  réponds « ok » pour l'envoyer, « modif : … » pour le retoucher, ou écris ta réponse.

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
CHEMINS = ["contact", "nous-contacter", "contactez-nous", "presse", "espace-presse", "press", "fr/contact",
           "fr/presse", "mentions-legales", "partenariats", "fr/mentions-legales"]


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
    return e


def sauver(e):
    e["mails_traites"] = e["mails_traites"][-500:]
    e["jours_generes"] = e["jours_generes"][-60:]
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


def dire(e, texte, lien=None):
    """Envoie un message Telegram ; `lien` = (id_contact, type) pour savoir à quoi il répond."""
    if not e.get("telegram_chat"):
        print("(Telegram pas encore relié) " + texte[:200])
        return None
    rep = tg("sendMessage", chat_id=e["telegram_chat"], text=texte[:4000], disable_web_page_preview=True)
    if rep and rep.get("ok") and lien:
        e["telegram_messages"][str(rep["result"]["message_id"])] = {"contact": lien[0], "type": lien[1]}
    return rep


def contact(e, cid):
    return next((c for c in e["contacts"] if c["id"] == cid), None)


def lire_telegram(e):
    rep = tg("getUpdates", offset=e["telegram_offset"] + 1, timeout=0)
    if not rep or not rep.get("ok"):
        return
    for u in rep["result"]:
        e["telegram_offset"] = max(e["telegram_offset"], u["update_id"])
        m = u.get("message")
        if not m or "text" not in m:
            continue
        chat = m["chat"]["id"]
        texte = m["text"].strip()
        if not e["telegram_chat"]:
            e["telegram_chat"] = chat
            dire(e, "✅ C'est relié ! Ici tu recevras :\n• l'aperçu de chaque mail avant envoi (réponds « stop » "
                    "à un aperçu pour l'annuler) ;\n• les réponses des contacts, avec un brouillon : réponds « ok » "
                    "pour l'envoyer, « modif : … » pour le retoucher, ou écris directement ta réponse.\n\n"
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
    elif bas.startswith("modif"):
        b = brouillon_reponse(c, c.get("derniere_reponse", ""), consigne=texte.split(":", 1)[-1].strip())
        c["brouillon"] = b["brouillon"]
        dire(e, f"✍️ Nouveau brouillon pour {c['organisation']} :\n\n{b['brouillon']}\n\n"
                "Réponds « ok » pour l'envoyer, ou « modif : … » encore.", (c["id"], "reponse"))
        return
    else:
        corps = texte
        if "Mathis" not in corps[-200:]:
            corps = corps.rstrip() + "\n\nBien cordialement,\n\n" + C.SIGNATURE
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
    if not envoyes:
        return
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
                        "👉 Réponds à CE message : « ok » pour l'envoyer, « modif : … » pour le retoucher, "
                        "ou écris directement ta réponse.")
        else:
            message += "👉 Réponds à CE message avec ta réponse, je l'envoie."
        dire(e, message, (c["id"], "reponse"))
    im.logout()


# --------------------------------------------------------------------------
# Recherche des cibles et des adresses
# --------------------------------------------------------------------------
def deja_vus(e):
    noms = [x.lower() for x in C.EXCLUS] + [c["organisation"].lower() for c in e["contacts"]]
    domaines = {domaine_base(urlparse(c["site"]).netloc) for c in e["contacts"] if c.get("site")}
    return noms, domaines


def proposer_cibles(e, nb=12):
    jour = maintenant().toordinal()
    cats = [C.CATEGORIES_CIBLES[(jour * 3 + i) % len(C.CATEGORIES_CIBLES)] for i in range(3)]
    noms, _ = deja_vus(e)
    prompt = f"""Tu aides le média Instagram « Le Média Français » (histoire, patrimoine, savoir-faire et marques
françaises ; 210 000 abonnés, 80 % de 18-34 ans, 75 % d'hommes) à trouver des partenaires.

Propose {nb} organisations françaises différentes, réparties à peu près également entre ces types :
{chr(10).join('- ' + x for x in cats)}

Règles :
- organisations qui existent vraiment, actives aujourd'hui, avec un site officiel ;
- qui ont un lien naturel avec l'histoire ou le patrimoine français et un intérêt à toucher des jeunes ;
- privilégie les tailles moyennes et les acteurs régionaux (plus de chances de réponse) aux géants mondiaux ;
- évite toute organisation en difficulté financière connue, controversée, politique ou religieuse ;
- n'en propose AUCUNE de cette liste (déjà contactées) : {', '.join(sorted(set(noms)))[:3500]}

Réponds en JSON : {{"cibles": [{{"organisation": "...", "type": "...", "site": "https://... (page d'accueil officielle)",
"idee": "une idée de contenu en une phrase qui ferait le lien entre eux et l'histoire de France"}}]}}"""
    r = gemini.json_(prompt, temperature=0.9)
    return r.get("cibles", [])


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
    pages = 0
    for url in a_voir:
        if url in vues or pages >= 7:
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
    notees = sorted(((_note(a), a) for a in adresses), reverse=True)
    notees = [(n, a) for n, a in notees if n >= 0 and _mx_ok(domaine_mail(a))]
    if notees:
        a = notees[0][1]
        return a, adresses[a], texte
    # Dernier essai : recherche Google via Gemini, acceptée seulement si l'adresse est sur le domaine officiel
    try:
        base = domaine_base(urlparse(cible["site"]).netloc)
        r = gemini.json_(f"""Trouve l'adresse e-mail publique du service presse, communication, marketing ou
partenariats de « {cible['organisation']} » (site officiel : {cible['site']}).
Uniquement une adresse publiée par l'organisation elle-même. Si tu n'es pas certain, mets null.
Réponds en JSON : {{"email": "... ou null", "nom": "nom de la personne si l'adresse est nominative, sinon null"}}""",
                         temperature=0.1, recherche=True)
        a = (r.get("email") or "").lower().strip()
        if a and domaine_base(domaine_mail(a)) == base and _note(a) >= 0 and _mx_ok(domaine_mail(a)):
            return a, f"Contact : {r.get('nom') or ''} {a}", texte
    except Exception as ex:
        print(f"Recherche d'adresse ratée pour {cible['organisation']} : {ex}")
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
Texte de leur site (pour personnaliser) :
\"\"\"{contexte[:5000]}\"\"\"

Nos chiffres (n'utilise que ceux-là, 2 ou 3 maximum, les plus parlants pour eux) :
{C.CHIFFRES}

Contenu du mail :
1. Salutation : « Bonjour Madame Nom, » ou « Bonjour Monsieur Nom, » UNIQUEMENT si le contexte nomme clairement
   la personne derrière cette adresse ; sinon « Bonjour, ».
2. Une accroche qui montre qu'on les connaît : une actualité, un produit, un lieu ou un pan de leur histoire tiré de leur site.
3. Qui on est, en une phrase, avec les chiffres choisis.
4. Une proposition concrète et spécifique à eux : une histoire précise qu'on raconterait dans un carrousel
   (marque : carrousel sponsorisé ; musée, monument, institution, territoire : collaboration, tournage sur place
   ou campagne d'attractivité comme celle faite avec la Région Normandie).
5. « Je vous joins notre media kit. » Ne donne AUCUN prix.
6. Une question de fin simple pour obtenir un échange (un appel de 15 minutes cette semaine ou la suivante).
7. Termine par « Bien cordialement, » (la signature est ajoutée ensuite, ne l'écris pas).
Longueur : 120 à 190 mots. Objet : court (moins de 70 caractères), précis, sans majuscules partout, avec leur nom.
{REGLES_STYLE}
{('Consigne de Mathis pour cette version : ' + consigne) if consigne else ''}

Réponds en JSON : {{"objet": "...", "corps": "...", "nom_contact": "Madame/Monsieur Nom ou null"}}"""
    m = gemini.json_(prompt, temperature=0.7)
    corps = m["corps"].strip()
    corps = re.sub(r"\n*Mathis Pouchol.*$", "", corps, flags=re.S).rstrip()
    m["corps"] = (corps + "\n\n" + C.SIGNATURE +
                  "\n\nSi vous ne souhaitez plus recevoir de message de ma part, répondez simplement STOP.")
    return m


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
    heure = lire_date(c["prevu_le"]).strftime("%H:%M")
    dire(e, f"📤 Prévu à {heure} → {c['organisation']} ({c['email']})\nObjet : {c['objet']}\n\n{c['corps']}\n\n"
            "👉 Réponds « stop » à ce message pour l'annuler, ou « modif : ta consigne » pour le réécrire.",
         (c["id"], "apercu"))


def generer_du_jour(e):
    jour = maintenant().date()
    noms, domaines = deja_vus(e)
    retenus, essais = [], 0
    heures = list(C.HEURES_ENVOI)[: C.MAILS_PAR_JOUR]
    debut = time.time()
    while len(retenus) < C.MAILS_PAR_JOUR and essais < 3 and time.time() - debut < 15 * 60:
        essais += 1
        try:
            cibles = proposer_cibles(e)
        except Exception as ex:
            print(f"Propositions de cibles ratées : {ex}")
            continue
        for cible in cibles:
            if len(retenus) >= C.MAILS_PAR_JOUR or time.time() - debut > 15 * 60:
                break
            if not cible.get("site") or not cible.get("organisation"):
                continue
            d = domaine_base(urlparse(cible["site"] if "//" in cible["site"] else "https://" + cible["site"]).netloc)
            if d in domaines or cible["organisation"].lower() in noms:
                continue
            domaines.add(d)
            print(f"→ {cible['organisation']} ({cible['site']})")
            adresse, ctx, texte = trouver_adresse(cible)
            if not adresse:
                print("   pas d'adresse fiable, cible suivante")
                continue
            c = {"id": f"{jour.isoformat()}-{len(retenus) + 1}-{random.randint(100, 999)}",
                 "organisation": cible["organisation"], "type": cible.get("type", ""), "site": cible["site"],
                 "idee": cible.get("idee", ""), "email": adresse, "contexte_adresse": ctx, "contexte": texte[:3000],
                 "cree_le": iso(maintenant()), "statut": "planifié"}
            try:
                m = rediger(c, texte)
            except Exception as ex:
                print(f"   rédaction ratée : {ex}")
                continue
            hh, mm = map(int, heures[len(retenus)].split(":"))
            prevu = maintenant().replace(hour=hh, minute=mm, second=0, microsecond=0) + dt.timedelta(minutes=random.randint(0, 12))
            if prevu < maintenant() + dt.timedelta(minutes=30):
                prevu = maintenant() + dt.timedelta(minutes=30 + 40 * len(retenus))
            c.update({"objet": m["objet"], "corps": m["corps"], "nom_contact": m.get("nom_contact"),
                      "prevu_le": iso(prevu), "repondre_a": adresse})
            e["contacts"].append(c)
            retenus.append(c)
            envoyer_apercu(e, c)
            print(f"   ✓ {adresse}")
    e["jours_generes"].append(jour.isoformat())
    if len(retenus) < C.MAILS_PAR_JOUR:
        dire(e, f"ℹ️ Aujourd'hui je n'ai trouvé que {len(retenus)} adresse(s) fiable(s) sur {C.MAILS_PAR_JOUR}.")


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
def tick():
    manque = [n for n in ("MAIL_SERVEUR", "MAIL_UTILISATEUR", "MAIL_MOT_DE_PASSE") if not getattr(C, n)]
    if manque:
        print("Prospection pas encore configurée (secrets manquants : " + ", ".join(manque) + ").")
        return
    C.CLE_2_EN_PREMIER = True  # la prospection consomme d'abord la 2e clé Gemini
    e = charger()
    try:
        lire_telegram(e)
        m = maintenant()
        if (C.PROSPECTION_ACTIVE and e["active"] and m.weekday() < 5 and 8 <= m.hour < 14
                and m.date().isoformat() not in e["jours_generes"]):
            try:
                generer_du_jour(e)
            except Exception as ex:
                e["jours_generes"].append(m.date().isoformat())
                dire(e, f"⚠️ Préparation des mails du jour ratée : {ex}")
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
