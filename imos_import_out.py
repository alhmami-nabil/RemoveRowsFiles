#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
imos_import_out.py — Sort les éléments d'un transfert IMOS du dossier
"Import" après import (option 1 : coupure au niveau d'Import).

Principe :
  1) Lit DB/imostransfer.xml dans le zip de transfert
  2) Balise <Folder> : ne garde que les éléments finaux (TYP < 1000000)
     -> DB = nom de la table, VAL = NAME, TYP = TYPE
  3) Pour chaque élément, dans la base de destination :
       - s'il est sous "Import" : le dossier directement sous Import
         est déplacé vers le parent d'Import (la racine)
       - si un dossier homonyme existe déjà à destination : FUSION,
         seuls les éléments absents sont déplacés
       - sinon : rien à faire
  4) Les dossiers vidés sous Import sont supprimés (uniquement
     s'ils sont entièrement vides ; Import lui-même est conservé)

Usage :
  python imos_import_out.py transfert.zip --server SRV --database DB --trusted
  python imos_import_out.py transfert.zip --server SRV --database DB \
         --user sa --password xxx --apply

Sans --apply : mode aperçu (aucune écriture).
"""

import argparse
import zipfile
import xml.etree.ElementTree as ET
from urllib.parse import unquote

IMPORT_FOLDER_NAME = "Import"


def _is_folder_type(t):
    """Les types de dossiers IMOS sont >= 1000000 (1000001, 100002x...)."""
    try:
        return int(t) >= 1000000
    except (TypeError, ValueError):
        return False


# ----------------------------------------------------------------------
# 1) Lecture du fichier de transfert : éléments finaux uniquement
# ----------------------------------------------------------------------

def parse_transfer(zip_path):
    """Retourne [(table, name, typ), ...] des éléments finaux du transfert."""
    with zipfile.ZipFile(zip_path) as z:
        candidates = [n for n in z.namelist()
                      if n.lower().endswith("imostransfer.xml")]
        if not candidates:
            raise FileNotFoundError("imostransfer.xml introuvable dans le zip")
        xml_data = z.read(candidates[0])

    root = ET.fromstring(xml_data)
    folder = root.find("Folder")
    if folder is None:
        raise ValueError("Balise <Folder> absente du fichier de transfert")

    elements = []
    seen = set()
    for fld in folder.findall("FLD"):
        typ = fld.get("TYP")
        if _is_folder_type(typ):
            continue                       # dossier / racine : ignoré
        table = fld.get("DB")
        name = unquote(fld.get("VAL"))     # décodage %20 etc.
        key = (table, name, typ)
        if key not in seen:
            seen.add(key)
            elements.append(key)
    return elements


# ----------------------------------------------------------------------
# 2) Accès base : primitives paramétrées
# ----------------------------------------------------------------------

class Db:
    def __init__(self, cursor, apply_changes):
        self.cur = cursor
        self.apply = apply_changes

    def _q(self, table):
        return "[" + table.replace("]", "]]") + "]"

    def table_ok(self, table):
        try:
            self.cur.execute(
                f"SELECT DIR_ID, NAME, TYPE, PARENT_ID FROM {self._q(table)} "
                f"WHERE 1 = 0")
            self.cur.fetchall()
            return True
        except Exception:
            return False

    def find_element(self, table, name, typ):
        """Occurrences de l'élément (NAME + TYPE) : [(dir_id, parent_id)]."""
        self.cur.execute(
            f"SELECT DIR_ID, PARENT_ID FROM {self._q(table)} "
            f"WHERE NAME = ? AND TYPE = ?", (name, typ))
        return self.cur.fetchall()

    def node(self, table, dir_id):
        self.cur.execute(
            f"SELECT NAME, PARENT_ID FROM {self._q(table)} WHERE DIR_ID = ?",
            (dir_id,))
        return self.cur.fetchone()

    def ancestors(self, table, dir_id):
        """Chaîne [(dir_id, name), ...] du nœud vers la racine (nœud inclus)."""
        chain = []
        cur = dir_id
        seen = set()
        while True:
            row = self.node(table, cur)
            if row is None or cur in seen:
                break
            seen.add(cur)
            chain.append((cur, row[0]))
            if row[1] in (0, None):
                break
            cur = row[1]
        return chain

    def children(self, table, parent_id):
        """Tous les enfants : [(dir_id, name, type)]."""
        self.cur.execute(
            f"SELECT DIR_ID, NAME, TYPE FROM {self._q(table)} "
            f"WHERE PARENT_ID = ?", (parent_id,))
        return self.cur.fetchall()

    def children_named(self, table, parent_id, name):
        self.cur.execute(
            f"SELECT DIR_ID, TYPE FROM {self._q(table)} "
            f"WHERE PARENT_ID = ? AND NAME = ?", (parent_id, name))
        return self.cur.fetchall()

    def child_count(self, table, parent_id):
        self.cur.execute(
            f"SELECT COUNT(*) FROM {self._q(table)} WHERE PARENT_ID = ?",
            (parent_id,))
        return self.cur.fetchone()[0]

    def move(self, table, dir_id, new_parent):
        if not self.apply:
            return
        self.cur.execute(
            f"UPDATE {self._q(table)} SET PARENT_ID = ? WHERE DIR_ID = ?",
            (new_parent, dir_id))

    def delete_if_empty(self, table, dir_id):
        if not self.apply:
            return True
        if self.child_count(table, dir_id) == 0:
            self.cur.execute(
                f"DELETE FROM {self._q(table)} WHERE DIR_ID = ?", (dir_id,))
            return True
        return False


# ----------------------------------------------------------------------
# 3) Traitement d'un élément : coupure au niveau d'Import + fusion
# ----------------------------------------------------------------------

def process_element(db, table, name, typ, report, moved_state):
    def log(status, detail=""):
        report.append({"table": table, "element": name,
                       "status": status, "detail": detail})

    if not db.table_ok(table):
        log("ERREUR", "table absente ou colonnes manquantes")
        return

    occurrences = db.find_element(table, name, typ)
    if not occurrences:
        log("INTROUVABLE", "élément absent de la base (import non fait ?)")
        return

    under_import, outside = [], []
    for dir_id, _parent in occurrences:
        chain = db.ancestors(table, dir_id)
        anc_names = [n for (_i, n) in chain[1:]]
        (under_import if IMPORT_FOLDER_NAME in anc_names
         else outside).append((dir_id, chain))

    if not under_import:
        if outside:
            p = " > ".join(n for (_i, n) in reversed(outside[0][1]))
            log("RIEN A FAIRE", f"pas sous Import ; emplacement : {p}")
        return

    if outside:
        p = " > ".join(n for (_i, n) in reversed(outside[0][1]))
        log("CONFLIT", f"le nom existe déjà hors d'Import : {p}")
        return

    if len(under_import) > 1:
        log("CONFLIT", f"{len(under_import)} occurrences sous Import, ambigu")
        return

    elem_id, chain = under_import[0]

    # Import et sa position dans la chaîne
    import_pos = next(i for i, (_id, n) in enumerate(chain)
                      if n == IMPORT_FOLDER_NAME)
    import_id = chain[import_pos][0]
    import_parent = db.node(table, import_id)[1]   # destination

    # point de coupure : le nœud directement sous Import
    # (l'élément lui-même s'il est posé directement dans Import)
    cut_id, cut_name = chain[import_pos - 1]

    # homonyme (dossier) déjà présent à destination ?
    cands = [(i, t) for (i, t) in db.children_named(table, import_parent,
                                                    cut_name)
             if i != cut_id]
    if len(cands) > 1:
        log("CONFLIT", f"plusieurs homonymes '{cut_name}' à destination")
        return

    if not cands:
        # ---------- CAS A : déplacement simple ----------
        db.move(table, cut_id, import_parent)
        moved_state.add((table, cut_id))
        dest = " > ".join(n for (_i, n) in reversed(chain[import_pos + 1:]))
        log("DEPLACE", f"'{cut_name}' déplacé sous {dest or 'la racine'}")
        return

    # ---------- CAS B : fusion niveau par niveau ----------
    target_id = cands[0][0]
    pairs = [(cut_id, target_id)]
    all_sources = []
    moved, kept = [], []

    while pairs:
        src, dst = pairs.pop(0)
        all_sources.append(src)
        for child_id, child_name, child_typ in db.children(table, src):
            same = [(i, t) for (i, t) in db.children_named(table, dst,
                                                           child_name)
                    if str(t) == str(child_typ)]
            if same:
                if _is_folder_type(child_typ):
                    pairs.append((child_id, same[0][0]))   # fusion récursive
                else:
                    kept.append(child_name)   # élément déjà présent : on garde
            else:
                db.move(table, child_id, dst)
                moved.append(child_name)

    # nettoyage : suppression des dossiers sources vidés, du bas vers le haut
    cleaned = []
    for src in reversed(all_sources):
        row = db.node(table, src)
        if row and db.delete_if_empty(table, src):
            cleaned.append(row[0])

    detail = f"fusion dans '{cut_name}' existant"
    if moved:
        detail += f" | déplacés : {', '.join(moved)}"
    if kept:
        detail += f" | déjà présents (conservés) : {', '.join(kept)}"
    if cleaned:
        detail += f" | dossiers nettoyés : {', '.join(cleaned)}"
    log("FUSIONNE", detail)


# ----------------------------------------------------------------------
# 4) Programme principal
# ----------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(
        description="Sort les éléments d'un transfert IMOS du dossier "
                    "Import (coupure au niveau d'Import + fusion).")
    ap.add_argument("zip", help="fichier de transfert IMOS (.zip)")
    ap.add_argument("--server", required=True)
    ap.add_argument("--database", required=True)
    ap.add_argument("--user")
    ap.add_argument("--password")
    ap.add_argument("--trusted", action="store_true",
                    help="authentification Windows")
    ap.add_argument("--driver", default="ODBC Driver 17 for SQL Server")
    ap.add_argument("--apply", action="store_true",
                    help="exécuter réellement (sinon : aperçu)")
    args = ap.parse_args()

    elements = parse_transfer(args.zip)
    print(f"Transfert lu : {len(elements)} élément(s) final(aux) dans "
          f"{len({t for (t, _n, _y) in elements})} table(s)\n")

    import pyodbc
    if args.trusted:
        cs = (f"DRIVER={{{args.driver}}};SERVER={args.server};"
              f"DATABASE={args.database};Trusted_Connection=yes")
    else:
        cs = (f"DRIVER={{{args.driver}}};SERVER={args.server};"
              f"DATABASE={args.database};UID={args.user};PWD={args.password}")
    conn = pyodbc.connect(cs, autocommit=False)
    cursor = conn.cursor()

    db = Db(cursor, apply_changes=args.apply)
    report = []
    moved_state = set()
    for table, name, typ in elements:
        process_element(db, table, name, typ, report, moved_state)

    width = max((len(r["element"]) for r in report), default=10)
    print(f"{'TABLE':<25} {'ELEMENT':<{width}}  STATUT — détail")
    print("-" * (35 + width + 40))
    for r in report:
        print(f"{r['table']:<25} {r['element']:<{width}}  "
              f"{r['status']} — {r['detail']}")

    if args.apply:
        conn.commit()
        print("\n[OK] Modifications validées (COMMIT). "
              "Fermer/rouvrir IMOS pour vider le cache.")
    else:
        conn.rollback()
        print("\n[APERÇU] Aucune modification. Relancer avec --apply "
              "pour exécuter.")


if __name__ == "__main__":
    main()