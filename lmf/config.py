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

# --- Contenu ----------------------------------------------------------------
SLIDES_PAR_CARROUSEL = 10        # couverture + 8 slides + slide finale (max API Instagram : 10)
MOTS_MAX_PAR_SLIDE = 22
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
LARGEUR_TEXTE_MAX = 860          # laisse la place à la flèche en bas à droite

# --- IA ---------------------------------------------------------------------
GEMINI_MODELE = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")            # rédaction + vérification
GEMINI_MODELE_LEGER = os.environ.get("GEMINI_MODEL_LITE", "gemini-2.5-flash-lite")  # choix des images

# --- Instagram --------------------------------------------------------------
GRAPH_VERSION = "v21.0"
GRAPH_URL = "https://graph.instagram.com"

# --- Hébergement des images -------------------------------------------------
# Les images sont mises en ligne sur imgbb (gratuit) : Instagram a besoin d'une adresse publique.
IMGBB_API_KEY = os.environ.get("IMGBB_API_KEY", "")

# --- Secrets (fournis par GitHub Actions) ----------------------------------
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
PEXELS_API_KEY = os.environ.get("PEXELS_API_KEY", "")
PIXABAY_API_KEY = os.environ.get("PIXABAY_API_KEY", "")
SHEET_URL = os.environ.get("SHEET_URL", "")        # URL de l'application Web Apps Script
SHEET_SECRET = os.environ.get("SHEET_SECRET", "")  # mot de passe partagé avec Apps Script
IG_USER_ID = os.environ.get("IG_USER_ID", "")
