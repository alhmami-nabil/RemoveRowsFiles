#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
imos_import.py — Blueprint Flask : sortie des éléments d'un transfert
IMOS du dossier "Import".

Intégration dans app.py (3 lignes) :

    from imos_import import imos_bp
    app.register_blueprint(imos_bp)

Routes ajoutées :
  GET  /imos/            -> page web (combobox bases + upload)
  GET  /imos/bases       -> liste des bases du serveur (JSON)
  POST /imos/transfert   -> traitement du zip (form: file, db, apply)
  GET  /imos/health      -> état de la connexion

Dépend de : imos_import_out.py, database.py (+ .env IMOS_*)
"""

import io
import zipfile

from flask import Blueprint, jsonify, render_template, request

import database
from imos_import_out import Db, parse_transfer, process_element

imos_bp = Blueprint("imos", __name__, url_prefix="/imos")

MAX_ZIP_SIZE = 50 * 1024 * 1024  # 50 Mo


@imos_bp.route("/")
def page():
    return render_template("imos.html")


@imos_bp.route("/health")
def health():
    db = request.args.get("db") or None
    ok, err = database.check_connection(db)
    return jsonify({"api": "ok", "base": db or "(.env)",
                    "database": ok, "database_error": err})


@imos_bp.route("/bases")
def bases():
    try:
        return jsonify({"bases": database.list_databases()})
    except Exception as exc:            # noqa: BLE001
        return jsonify({"error": f"Impossible de lister les bases : "
                                 f"{exc}"}), 503


@imos_bp.route("/transfert", methods=["POST"])
def transfert():
    # --- paramètres -------------------------------------------------------
    file = request.files.get("file")
    if file is None or not file.filename:
        return jsonify({"error": "Aucun fichier uploadé"}), 400

    db_name = (request.form.get("db") or request.args.get("db") or "").strip()
    if not db_name:
        return jsonify({"error": "Paramètre 'db' (base de données) "
                                 "obligatoire"}), 400
    try:
        database.validate_db_name(db_name)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    apply_str = (request.form.get("apply")
                 or request.args.get("apply") or "false")
    apply = apply_str.strip().lower() in ("true", "1", "yes")

    # --- lecture du zip ---------------------------------------------------
    content = file.read()
    if len(content) > MAX_ZIP_SIZE:
        return jsonify({"error": "Fichier trop volumineux (max 50 Mo)"}), 413

    try:
        elements = parse_transfer(io.BytesIO(content))
    except zipfile.BadZipFile:
        return jsonify({"error": "Le fichier n'est pas un zip valide"}), 400
    except FileNotFoundError:
        return jsonify({"error": "DB/imostransfer.xml introuvable dans le "
                                 "zip — est-ce bien un fichier de transfert "
                                 "IMOS ?"}), 400
    except Exception as exc:            # noqa: BLE001
        return jsonify({"error": f"Lecture du transfert impossible : "
                                 f"{exc}"}), 400

    if not elements:
        return jsonify({"error": "Aucun élément final trouvé dans le "
                                 "fichier de transfert"}), 400

    # --- traitement -------------------------------------------------------
    try:
        conn = database.get_connection(db_name)
    except Exception as exc:            # noqa: BLE001
        return jsonify({"error": f"Connexion à la base '{db_name}' "
                                 f"impossible : {exc}"}), 503

    try:
        cursor = conn.cursor()
        worker = Db(cursor, apply_changes=apply)
        report = []
        moved_state = set()
        for table, name, typ in elements:
            process_element(worker, table, name, typ, report, moved_state)

        if apply:
            conn.commit()
        else:
            conn.rollback()
    except Exception as exc:            # noqa: BLE001
        conn.rollback()
        return jsonify({"error": f"Erreur pendant le traitement "
                                 f"(ROLLBACK effectué) : {exc}"}), 500
    finally:
        conn.close()

    # --- réponse ------------------------------------------------------------
    resume = {}
    for r in report:
        resume[r["status"]] = resume.get(r["status"], 0) + 1

    return jsonify({
        "fichier": file.filename,
        "base": db_name,
        "mode": "EXECUTION" if apply else "APERCU",
        "elements_lus": len(elements),
        "tables": sorted({t for (t, _n, _y) in elements}),
        "resume": resume,
        "rapport": report,
        "note": ("Modifications validées — fermer/rouvrir IMOS pour vider "
                 "le cache d'arborescence." if apply else
                 "Aperçu : aucune modification. Relancer avec apply=true "
                 "pour exécuter."),
    })