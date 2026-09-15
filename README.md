# Report Uploader Agent

Agent générique déployé sur chaque serveur de dépôt (là où tourne
TankMaster) : surveille `C:/Reports`, et transfère les nouveaux rapports
vers le serveur SFTP central (`192.168.1.9`), dans `/upload/Reports_<CODE>`
— exactement le point d'entrée que `ListSFTP` surveille côté NiFi.

## Principes de conception

- **Un seul script, une config par dépôt.** Le code (`report_uploader_agent.py`)
  est strictement identique sur les 4+ serveurs. Seul `config.yaml` change
  (le `code` du dépôt). Ajouter un dépôt ne demande aucune modification de
  code.

- **Pas de boucle infinie, un cycle par exécution.** Le script fait un scan
  + upload puis se termine (code retour 0 ou 1). La répétition toutes les
  2 min est déléguée au Planificateur de tâches Windows. Avantages : chaque
  exécution est isolée (un plantage n'affecte pas les cycles suivants),
  visible dans l'historique natif de Windows, et ne nécessite aucun service
  à superviser en plus.

- **Idempotence par déplacement de fichier, pas par base de données.**
  Un fichier n'est déplacé vers `C:/Reports/sent/` qu'après upload confirmé.
  S'il reste dans `C:/Reports`, il sera automatiquement repris au prochain
  cycle — le système de fichiers EST l'état, pas de state store séparé à
  maintenir ou à corrompre.

- **Upload atomique (.part + rename).** Chaque fichier est d'abord envoyé
  sous `nom.txt.part`, puis renommé côté serveur distant une fois complet.
  Ça garantit que `ListSFTP` (côté NiFi) ne verra jamais un fichier à moitié
  transféré, même sur un gros rapport ou une liaison lente.

- **Vérification de stabilité avant transfert.** Le script confirme que la
  taille du fichier est stable (TankMaster a fini d'écrire) avant de
  l'envoyer — même logique que le module Data Quality du pipeline principal.

- **Authentification par clé SSH, pas par mot de passe.** Adapté à une
  exécution non supervisée et permanente ; le mot de passe ne doit jamais
  apparaître en clair dans une config versionnée.

## Déploiement sur un nouveau dépôt

1. Ajouter une entrée dans `depots.yaml` (code + IP), puis :
   ```
   python generate_configs.py
   ```
   → produit `configs/config_<CODE>.yaml`

2. Sur le serveur du dépôt, créer `C:\ReportUploader\` et y copier :
   - `report_uploader_agent.py`
   - `configs/config_<CODE>.yaml` renommé en `config.yaml`
   - un `.env` (voir `.env.example`) avec les vrais identifiants
   - la clé privée SSH référencée dans `.env`

3. Chaque dépôt est ciblé vers le sous-dossier distant dédié :
   ```
   /upload/Reports_<CODE>
   ```
   Exemple : `/upload/Reports_DGP`, `/upload/Reports_MJG`, etc.
   Ces dossiers doivent être créés automatiquement par l'agent avant l'upload.

4. Installer les dépendances Python puis enregistrer la tâche planifiée :
   ```
   cd C:\ReportUploader
   python -m venv .venv
   .venv\Scripts\pip install -r requirements.txt
   ```

3. Créer l'environnement virtuel et installer les dépendances :
   ```
   cd C:\ReportUploader
   python -m venv .venv
   .venv\Scripts\pip install -r requirements.txt
   ```

4. Enregistrer la tâche planifiée (PowerShell, en administrateur) :
   ```
   powershell -ExecutionPolicy Bypass -File register_task.ps1
   ```

5. Vérifier : déposer un fichier de test dans `C:\Reports`, attendre le
   prochain cycle (≤ 2 min), confirmer son apparition dans
   `C:\Reports\sent\` et dans `/upload/Reports_<CODE>` côté serveur central
   (visible ensuite par `ListSFTP` dans NiFi).

## Où regarder en cas de problème

- `C:\ReportUploader\logs\uploader.log` (rotation automatique, 5 fichiers
  de 5 Mo) — chaque cycle y est journalisé, succès ou échec, par dépôt.
- Planificateur de tâches (`taskschd.msc`) → onglet Historique de la tâche
  `ReportUploaderAgent` — code retour de chaque exécution.
- Un fichier qui reste bloqué dans `C:\Reports` (jamais déplacé vers
  `sent\`) signale un échec de transfert persistant — regarder le log pour
  la cause (réseau, permissions, clé SSH expirée, etc.).
