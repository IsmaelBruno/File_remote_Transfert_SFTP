"""
generate_configs.py

Génère un config.yaml prêt à déployer pour chaque dépôt listé dans
depots.yaml. Usage :

    python generate_configs.py

Produit configs/config_<CODE>.yaml pour chaque dépôt du registre.
Il ne reste qu'à copier le fichier correspondant en "config.yaml" sur le
serveur du dépôt concerné (avec le script agent + un .env contenant les
secrets — voir README.md).

Ajouter un dépôt = ajouter une entrée dans depots.yaml puis relancer ce
script. Aucune modification du script agent lui-même n'est nécessaire.
"""

from pathlib import Path

import yaml

REGISTRY_PATH = Path(__file__).parent / "depots.yaml"
OUTPUT_DIR = Path(__file__).parent / "configs"


def main() -> None:
    with open(REGISTRY_PATH, "r", encoding="utf-8") as f:
        registry = yaml.safe_load(f)

    OUTPUT_DIR.mkdir(exist_ok=True)

    for depot in registry["depots"]:
        code = depot["code"]
        config = {
            "depot": {"code": code},
            "source": registry["source"],
            "sftp": {
                "host": registry["sftp_central"]["host"],
                "port": registry["sftp_central"]["port"],
                "remote_base_path": registry["sftp_central"]["remote_base_path"],
            },
            "logging": {
                "file": f"{registry['logging']['directory']}/uploader.log",
                "level": registry["logging"]["level"],
            },
        }

        out_path = OUTPUT_DIR / f"config_{code}.yaml"
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(f"# Généré automatiquement depuis depots.yaml pour le dépôt {code}.\n")
            f.write(f"# Copier ce fichier en config.yaml sur le serveur {depot['ip_address']}.\n\n")
            yaml.safe_dump(config, f, sort_keys=False, allow_unicode=True)

        print(f"Généré : {out_path}  (-> serveur {depot['ip_address']})")


if __name__ == "__main__":
    main()
