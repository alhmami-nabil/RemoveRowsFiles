#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
database.py — Connexion SQL Server pour l'API IMOS Import Out.

Configuration par variables d'environnement (fichier .env supporté
si python-dotenv est installé) :

  IMOS_SERVER      (obligatoire)   ex: BXL-SQL-IMD
  IMOS_PORT        (défaut: 1433)
  IMOS_DATABASE    (obligatoire)   ex: 2124
  IMOS_USER        vide -> authentification Windows (Trusted_Connection)
  IMOS_PASSWORD    requis si IMOS_USER est renseigné
  IMOS_DRIVER      (défaut: ODBC Driver 17 for SQL Server)
  IMOS_TRUST_CERT  yes/no (défaut: yes) -> TrustServerCertificate
"""

import os
import re
import re

try:                                    # optionnel : support des fichiers .env
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


DB_NAME_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_\-\.]{0,127}$")


def validate_db_name(name):
    """Valide un nom de base fourni par l'utilisateur (anti-injection)."""
    if not name or not DB_NAME_RE.match(name):
        raise ValueError(f"Nom de base invalide : {name!r}")
    return name


def get_settings(database=None):
    """Lit et valide la configuration ; lève RuntimeError si incomplète.

    `database` : si fourni (ex. par l'utilisateur de l'API), remplace
    IMOS_DATABASE du .env.
    """
    server = os.environ.get("IMOS_SERVER", "").strip()
    database = (database or os.environ.get("IMOS_DATABASE", "")).strip()
    if not server or not database:
        raise RuntimeError(
            "IMOS_SERVER non défini ou aucune base précisée "
            "(paramètre ou IMOS_DATABASE)")
    database = validate_db_name(database)

    user = os.environ.get("IMOS_USER", "").strip()
    password = os.environ.get("IMOS_PASSWORD", "").strip()
    if user and not password:
        raise RuntimeError("IMOS_PASSWORD requis quand IMOS_USER est défini")

    return {
        "server": server,
        "port": os.environ.get("IMOS_PORT", "1433").strip(),
        "database": database,
        "user": user,          # vide -> authentification Windows
        "password": password,
        "driver": os.environ.get(
            "IMOS_DRIVER", "ODBC Driver 17 for SQL Server").strip(),
        "trust_cert": os.environ.get(
            "IMOS_TRUST_CERT", "yes").strip().lower() in ("yes", "1", "true"),
    }


def build_connection_string(s):
    parts = [
        f"DRIVER={{{s['driver']}}}",
        f"SERVER={s['server']},{s['port']}",
        f"DATABASE={s['database']}",
    ]
    if s["user"]:
        parts.append(f"UID={s['user']}")
        parts.append(f"PWD={s['password']}")
    else:
        parts.append("Trusted_Connection=yes")
    if s["trust_cert"]:
        parts.append("TrustServerCertificate=yes")
    return ";".join(parts)


VALID_DB_NAME = re.compile(r"^[A-Za-z0-9_\-]{1,128}$")


def get_connection(database=None):
    """Retourne une connexion pyodbc (autocommit désactivé).

    `database` permet de cibler une autre base que celle du .env
    (nom validé pour éviter toute injection dans la chaîne).
    L'appelant est responsable du commit/rollback et du close.
    """
    import pyodbc

    settings = get_settings()
    if database:
        if not VALID_DB_NAME.match(database):
            raise ValueError(f"Nom de base invalide : {database!r}")
        settings["database"] = database
    return pyodbc.connect(build_connection_string(settings),
                          autocommit=False)


def list_databases():
    """Liste les bases utilisateur disponibles sur le serveur."""
    conn = get_connection(database="master")
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT name FROM sys.databases "
            "WHERE database_id > 4 ORDER BY name")   # > 4 : hors bases système
        return [r[0] for r in cur.fetchall()]
    finally:
        conn.close()


def list_databases():
    """Liste les bases utilisateur du serveur (hors bases système)."""
    conn = get_connection("master")
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT name FROM sys.databases "
            "WHERE name NOT IN ('master','tempdb','model','msdb') "
            "AND state = 0 "            # ONLINE uniquement
            "ORDER BY name")
        return [r[0] for r in cur.fetchall()]
    finally:
        conn.close()


def check_connection(database=None):
    """Teste la connexion ; retourne (ok: bool, erreur: str | None)."""
    try:
        conn = get_connection(database)
        conn.close()
        return True, None
    except Exception as exc:            # noqa: BLE001
        return False, str(exc)