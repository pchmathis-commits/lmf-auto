"""Réglages du Média Français – automatisation.

Tout ce qui se règle sans toucher au reste du code est ici.
Les secrets (clés, URL du Sheet) viennent des « Secrets » GitHub,
jamais de ce fichier.
"""
import os

# --- Rythme -----------------------------------------------------------------
CARROUSELS_PAR_JOUR = 3          # créneaux définis dans .github/workflows/carrousel.yml
ACTUS_PAR_JOUR = 2               # créneaux définis dans .github/workflows/actu.yml
DELAI_AVANT_PUBLICATION_MIN = 45   # temps laissé pour supprimer un post après le mail
VERIFICATION_AUTO = False        # True = relecture par Gemini + Google (coûte beaucoup de quota)

# --- Contenu ----------------------------------------------------------------
SLIDES_PAR_CARROUSEL = 10        # couverture + 8 slides + slide finale (max API Instagram : 10)
MOTS_MAX_PAR_SLIDE = 16
ABONNES_AFFICHES = "210K"        # affiché sur la slide finale
COMPTE = "@lemediafrancais"

# Sujets interdits en automatique (garde-fou)
SUJETS_INTERDITS = [
    "politique actuelle", "élections", "partis politiques", "religion (débat)",
    "faits divers violents", "crimes", "terrorisme", "guerre en cours",
    "immigration (débat)", "santé (conseils médicaux)",
]

# Requêtes d'actualité (Google Actualités, en français, dernières 24 h)
REQUETES_ACTU = [
    "patrimoine France",
    "monument historique restauration",
    "découverte archéologique France",
    "village France insolite",
    "château France",
    "tradition régionale France",
    "musée France exposition",
]

# --- Direction artistique --------------------------------------------------
LARGEUR, HAUTEUR = 1080, 1350
BLEU_MARINE = (6, 30, 97)        # #061E61
ROUGE = (252, 7, 0)              # #FC0700
BLANC = (255, 255, 255)
POLICE_URL = "https://github.com/google/fonts/raw/main/ofl/montserrat/Montserrat%5Bwght%5D.ttf"
POLICE_FICHIER = os.path.join(os.path.dirname(__file__), "..", "assets", "Montserrat.ttf")
POIDS_POLICE = 800               # ExtraBold
TAILLE_TEXTE_COUVERTURE = 68
TAILLE_TEXTE_SLIDE = 58
MARGE_GAUCHE = 48
ESPACE_ENTRE_BANDEAUX = 10      # espace vide entre deux bandeaux (0 = un seul bloc)
LARGEUR_TEXTE_MAX = 860
# Post actu (template Orion)
TAILLE_TEXTE_ACTU = 62
FOND_ACTU = (14, 22, 48)        # bleu nuit du fondu en bas
# Slide de fin (si assets/slide_finale_fond.png existe)
TAILLE_TEXTE_FIN = 60
ANGLE_BANDEAUX_FIN = 3           # inclinaison en degrés
CENTRE_BANDEAUX_FIN = 300        # hauteur du centre des bandeaux (sur 1350)          # laisse la place à la flèche en bas à droite

# --- IA ---------------------------------------------------------------------
GEMINI_MODELE = os.environ.get("GEMINI_MODEL", "gemini-3.6-flash")            # rédaction + vérification
GEMINI_MODELE_LEGER = os.environ.get("GEMINI_MODEL_LITE", "gemini-3.5-flash-lite")  # choix des images

# --- Instagram --------------------------------------------------------------
GRAPH_VERSION = "v21.0"
GRAPH_URL = "https://graph.instagram.com"

# --- Hébergement des images -------------------------------------------------
# Les images sont mises en ligne sur imgbb (gratuit) : Instagram a besoin d'une adresse publique.
IMGBB_API_KEY = os.environ.get("IMGBB_API_KEY", "")

# --- Secrets (fournis par GitHub Actions) ----------------------------------
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_API_KEY_2 = os.environ.get("GEMINI_API_KEY_2", "")   # clé d'un autre projet Google (ex. Orion) = quota en plus
CLE_2_EN_PREMIER = False                                    # la prospection passe ce réglage à True


def cles_gemini():
    cles = [k for k in (GEMINI_API_KEY, GEMINI_API_KEY_2) if k]
    return cles[::-1] if CLE_2_EN_PREMIER else cles
PEXELS_API_KEY = os.environ.get("PEXELS_API_KEY", "")
PIXABAY_API_KEY = os.environ.get("PIXABAY_API_KEY", "")
SHEET_URL = os.environ.get("SHEET_URL", "")        # URL de l'application Web Apps Script
SHEET_SECRET = os.environ.get("SHEET_SECRET", "")  # mot de passe partagé avec Apps Script
IG_USER_ID = os.environ.get("IG_USER_ID", "")

# --- Prospection (mails de partenariat) -----------------------------------
PROSPECTION_ACTIVE = True
MAILS_PAR_JOUR = 3                        # du lundi au vendredi → 15 par semaine
HEURES_ENVOI = ["09:45", "10:45", "13:45"]  # prêts à ces heures, partent au passage suivant de l'horloge (≈ 10 h, 11 h, 14 h)
RELANCE_APRES_JOURS = 7                   # une seule relance si pas de réponse
EXPEDITEUR_NOM = "Mathis Pouchol – Le Média Français"
SIGNATURE = """Mathis Pouchol
Fondateur – Le Média Français
lemediafrancais.fr · instagram.com/lemediafrancais"""
MEDIA_KIT = os.path.join(os.path.dirname(__file__), "..", "assets", "media_kit.pdf")
# Chiffres du media kit (octobre 2026) utilisés dans les mails
CHIFFRES = """- 210 000 abonnés Instagram, 82 000 sur TikTok et Threads
- 24 millions de vues par mois, record : 800 000 vues sur un seul carrousel
- Audience : 80 % ont entre 18 et 34 ans, 75 % d'hommes, 83 % en France
- Exemples de posts : origine française de Cadillac (plus de 90 000 vues), OTAN (plus de 95 000 vues)
- Étude de cas : Région Normandie, Millenium 2027 (1000 ans de Guillaume le Conquérant) : 65 000 vues, 3,5 % d'engagement, 25 000 vues de la story avec lien
- Offres : carrousel sponsorisé 2 000 €, story dédiée 800 €, lien exclusif un mois 2 200 € ; diffusion TikTok et Threads offerte"""
# Types de cibles, en rotation pour varier
CATEGORIES_CIBLES = [
    "marque française grand public (mode, accessoires, art de vivre) qui met en avant son histoire",
    "marque française automobile, moto, vélo ou mobilité",
    "marque française alimentaire ou boisson à forte histoire (biscuiterie, fromagerie, spiritueux, eaux…)",
    "musée français (national ou régional) avec une programmation active",
    "château, abbaye ou monument français géré par une structure qui communique",
    "office de tourisme, département ou ville française qui fait des campagnes d'attractivité",
    "entreprise industrielle française historique (aéronautique, verrerie, coutellerie, porcelaine, horlogerie…)",
    "maison d'édition, festival ou événement culturel ou sportif français",
    "marque française outdoor, sport ou équipement",
    "institution ou établissement public culturel français",
]
# Déjà contactés ou à ne jamais contacter (ajoute une ligne quand tu veux)
EXCLUS = [
    "Élysée", "Ministère de la Culture", "Opinel", "Alpine", "Monnaie de Paris", "Château de Versailles",
    "Marine nationale", "Armée de l'Air et de l'Espace", "Le Coq Sportif", "Musée de l'Armée",
    "Musée de l'Air et de l'Espace", "Centre des monuments nationaux", "Le Slip Français", "Saint James",
    "Région Normandie", "Saint-Malo Cryptors",
]

# --- Secrets de la prospection ---------------------------------------------
MAIL_SERVEUR = os.environ.get("MAIL_SERVEUR", "")          # serveur Zimbra (SMTP et IMAP)
MAIL_UTILISATEUR = os.environ.get("MAIL_UTILISATEUR", "")  # mathis@lemediafrancais.fr
MAIL_MOT_DE_PASSE = os.environ.get("MAIL_MOT_DE_PASSE", "")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")

# --- Veille hebdo (samedi matin) -------------------------------------------
APIFY_TOKEN = os.environ.get("APIFY_TOKEN", "")
POSTS_PAR_COMPTE = 30      # 5 comptes × 30 posts max ≈ 150 résultats/semaine (crédit gratuit Apify : ~1 850/mois)
JOURS_VEILLE = 7
COMPTES_SURVEILLES = [
    "sellmeparis", "le_coq_savant", "cultination.fr", "gardedupatrimoine", "villages_et_patrimoine",
]
