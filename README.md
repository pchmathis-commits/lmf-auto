# Le Média Français – automatisation Instagram

Crée chaque jour 3 carrousels de 10 slides et 2 posts d'actualité dans la DA du Média Français,
envoie un mail de validation pour chacun, puis publie sur Instagram.

- `lmf/` : le programme (rédaction, vérification, images, rendu des slides, publication)
- `apps_script/Code.gs` : le script du Google Sheet de pilotage (mails, validation, sujets)
- `data/sujets_initiaux.csv` : la banque de sujets vérifiés, à importer dans l'onglet Sujets
- `.github/workflows/` : les horaires (créneaux en heure UTC)
- `assets/` : logo et flèche

Tout se règle dans `lmf/config.py`. L'installation est décrite dans le mode opératoire.

Tester le rendu sans rien publier : onglet Actions > « Carrousel de démonstration ».
