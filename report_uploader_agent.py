"""
report_uploader_agent.py

Agent générique de transfert des rapports TankMaster vers le serveur SFTP
central (NiFi). Ce SCRIPT est identique sur chaque serveur de dépôt ; seule
la configuration (config.yaml + .env) change d'un dépôt à l'autre.

Conçu pour être exécuté périodiquement (toutes les 2 min) via le
Planificateur de tâches Windows — un lancement = un cycle complet
(scan, upload, sortie). Pas de boucle infinie : la répétition est déléguée
à l'ordonnanceur, ce qui rend chaque exécution supervisable et redémarrable
indépendamment (voir README.md).

Idempotence : un fichier n'est déplacé vers le sous-dossier "sent" qu'après
upload confirmé. Un cycle qui échoue en cours de route laisse simplement les
fichiers non traités en place — ils seront repris automatiquement au
prochain cycle, sans état supplémentaire à maintenir.
"""

from __future__ import annotations

import argparse
import logging
import logging.handlers
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import paramiko
import yaml
from dotenv import load_dotenv


# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Config:
    depot_code: str
    source_dir: Path
    file_pattern: str
    stability_wait_seconds: int
    scan_interval_seconds: int
    sftp_host: str
    sftp_port: int
    sftp_user: str
    sftp_password: str
    remote_base_path: str
    log_file: Path
    log_level: str


def load_config(config_path: Path) -> Config:
    """Charge la config YAML (non sensible) + les secrets depuis un .env
    situé à côté du fichier de config."""
    env_path = config_path.parent / ".env"
    load_dotenv(dotenv_path=env_path)

    with open(config_path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    try:
        sftp_user = os.environ["SFTP_USER"]
        sftp_password = os.environ["SFTP_PASSWORD"]
    except KeyError as exc:
        raise RuntimeError(
            f"Variable d'environnement manquante : {exc}. "
            f"Vérifie le fichier .env à côté de {config_path}"
        ) from exc

    return Config(
        depot_code=raw["depot"]["code"],
        source_dir=Path(raw["source"]["directory"]),
        file_pattern=raw["source"].get("file_pattern", "*.txt"),
        stability_wait_seconds=raw["source"].get("stability_wait_seconds", 5),
        scan_interval_seconds=raw["source"].get("scan_interval_seconds", 120),
        sftp_host=raw["sftp"]["host"],
        sftp_port=raw["sftp"].get("port", 22),
        sftp_user=sftp_user,
        sftp_password=sftp_password,
        remote_base_path=raw["sftp"]["remote_base_path"],
        log_file=Path(raw["logging"]["file"]),
        log_level=raw["logging"].get("level", "INFO"),
    )


# --------------------------------------------------------------------------
# Logging
# --------------------------------------------------------------------------

def setup_logging(log_file: Path, level: str) -> logging.Logger:
    log_file.parent.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("uploader")
    logger.setLevel(level)

    if not logger.handlers:  # évite les doublons si jamais réimporté
        file_handler = logging.handlers.RotatingFileHandler(
            log_file, maxBytes=5_000_000, backupCount=5, encoding="utf-8"
        )
        console_handler = logging.StreamHandler(sys.stdout)
        formatter = logging.Formatter(
            "%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
        )
        for handler in (file_handler, console_handler):
            handler.setFormatter(formatter)
            logger.addHandler(handler)

    return logger


# --------------------------------------------------------------------------
# Détection des fichiers prêts à être transférés
# --------------------------------------------------------------------------

def is_file_stable(path: Path, wait_seconds: int) -> bool:
    """Considère le fichier comme stable s'il n'a pas été modifié
    depuis au moins wait_seconds."""
    try:
        stat = path.stat()

        if stat.st_size <= 0:
            return False

        file_age = time.time() - stat.st_mtime
        return file_age >= wait_seconds

    except FileNotFoundError:
        return False


def find_candidate_files(source_dir: Path, pattern: str) -> list[Path]:
    if not source_dir.exists():
        raise FileNotFoundError(f"Dossier source introuvable : {source_dir}")
    return sorted(f for f in source_dir.glob(pattern) if f.is_file())


# --------------------------------------------------------------------------
# Transfert SFTP
# --------------------------------------------------------------------------


def ensure_remote_dir(sftp: paramiko.SFTPClient, remote_dir: str) -> None:
    """Crée le sous-dossier distant de dépôt s'il n'existe pas.
    NiFi surveille /upload/Reports_<CODE> ; il faut que ce répertoire soit
    présent avant l'envoi du premier rapport."""
    parts = [p for p in remote_dir.strip("/").split("/") if p]
    current = ""
    for part in parts:
        current = f"{current}/{part}" if current else f"/{part}"
        try:
            sftp.stat(current)
        except OSError:
            sftp.mkdir(current)


def upload_file(
    sftp: paramiko.SFTPClient,
    local_path: Path,
    remote_dir: str,
    logger: logging.Logger,
) -> None:
    remote_final = f"{remote_dir}/{local_path.name}"
    remote_tmp = f"{remote_final}.part"

    # Nettoye un ancien .part éventuellement laissé par un transfert précédent
    try:
        sftp.remove(remote_tmp)
    except OSError:
        pass

    # Supprime l'ancien fichier final s'il existe
    try:
        sftp.remove(remote_final)
    except OSError:
        pass

    sftp.put(str(local_path), remote_tmp)
    sftp.rename(remote_tmp, remote_final)

    logger.info(
        "Transféré : %s -> %s",
        local_path.name,
        remote_final,
    )




# --------------------------------------------------------------------------
# Cycle principal
# --------------------------------------------------------------------------

def run_cycle(config: Config, logger: logging.Logger, dry_run: bool = False) -> int:
    config.source_dir.mkdir(parents=True, exist_ok=True)
    sent_dir = config.source_dir / "sent"
    sent_dir.mkdir(parents=True, exist_ok=True)

    candidates = find_candidate_files(config.source_dir, config.file_pattern)
    if not candidates:
        logger.info("[%s] Aucun nouveau fichier.", config.depot_code)
        return 0

    stable_files = [f for f in candidates if is_file_stable(f, config.stability_wait_seconds)]
    if not stable_files:
        logger.info(
            "[%s] %d fichier(s) détecté(s) mais encore en écriture ; repris au prochain cycle.",
            config.depot_code, len(candidates),
        )
        return 0

    logger.info("[%s] %d fichier(s) à transférer.", config.depot_code, len(stable_files))

    if dry_run:
        logger.info(
            "[%s] Mode dry-run activé : aucun transfert réel ne sera effectué. "
            "Fichiers prévus : %s",
            config.depot_code,
            ", ".join(f.name for f in stable_files),
        )
        return 0

    try:
        transport = paramiko.Transport((config.sftp_host, config.sftp_port))
        transport.connect(
            username=config.sftp_user,
            password=config.sftp_password,
        )

    except Exception:
        logger.exception(
            "[%s] Connexion SFTP impossible vers %s:%s — nouvelle tentative au prochain cycle.",
            config.depot_code, config.sftp_host, config.sftp_port,
        )
        return 1

    remote_dir = f"{config.remote_base_path.rstrip('/')}/Reports_{config.depot_code}"
    failures = 0

    try:
        sftp = paramiko.SFTPClient.from_transport(transport)
        ensure_remote_dir(sftp, remote_dir)
        for file in stable_files:
            try:
                upload_file(sftp, file, remote_dir, logger)
                file.rename(sent_dir / file.name)
            except Exception:
                failures += 1
                logger.exception(
                    "[%s] Échec transfert de %s — fichier laissé en place, "
                    "nouvelle tentative au prochain cycle.",
                    config.depot_code, file.name,
                )
        sftp.close()
    finally:
        transport.close()

    if failures:
        logger.warning("[%s] Cycle terminé avec %d échec(s).", config.depot_code, failures)
        return 1

    logger.info("[%s] Cycle terminé avec succès.", config.depot_code)
    return 0


# --------------------------------------------------------------------------
# Entrée programme
# --------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Agent permanent d'upload des rapports TankMaster vers le SFTP central."
    )

    parser.add_argument(
        "--config",
        required=True,
        type=Path,
        help="Chemin vers config.yaml",
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simule les cycles sans transférer ni déplacer les fichiers.",
    )

    args = parser.parse_args()

    try:
        config = load_config(args.config)
    except Exception as exc:
        print(f"Erreur de configuration : {exc}", file=sys.stderr)
        return 2

    logger = setup_logging(config.log_file, config.log_level)

    logger.info(
        "[%s] Agent démarré. Surveillance continue de %s toutes les %s secondes.",
        config.depot_code,
        config.source_dir,
        config.scan_interval_seconds,
    )

    try:
        while True:
            try:
                run_cycle(
                    config,
                    logger,
                    dry_run=args.dry_run,
                )

                # Tant qu'il y a des fichiers à traiter,
                # on recommence immédiatement.
                candidates = find_candidate_files(
                    config.source_dir,
                    config.file_pattern,
                )

                if not candidates:
                    time.sleep(config.scan_interval_seconds)

            except Exception:
                logger.exception(
                    "[%s] Erreur non gérée durant le cycle.",
                    config.depot_code,
                )

                # Évite une boucle d'erreur trop agressive
                time.sleep(10)

    except KeyboardInterrupt:
        logger.info(
            "[%s] Agent arrêté manuellement.",
            config.depot_code,
        )
        return 0

if __name__ == "__main__":
    sys.exit(main())
