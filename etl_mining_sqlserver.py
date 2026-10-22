"""
ETL pédagogique Mining : Excel -> validation -> SQL Server (staging, dimensions, faits, rejets).

Prérequis :
    pip install pandas openpyxl sqlalchemy pyodbc

Avant l'exécution :
1. Créer les schémas et tables SQL Server avec le DDL fourni.
2. Définir les variables d'environnement SQLSERVER_* (exemples ci-dessous).
3. Adapter les noms de colonnes si votre DDL diffère.

Exemple PowerShell :
    $env:SQLSERVER_SERVER="localhost"
    $env:SQLSERVER_DATABASE="DataWarehouseMining"
    $env:SQLSERVER_USERNAME="etl_user"
    $env:SQLSERVER_PASSWORD="VotreMotDePasse"
    $env:SQLSERVER_DRIVER="ODBC Driver 18 for SQL Server"
    $env:SQLSERVER_TRUST_CERT="yes"
    python etl_mining_sqlserver.py

Le script est idempotent pour les dimensions et les faits : il ne réinsère pas
les codes déjà présents. Les lignes rejetées sont conservées dans un CSV.
"""

import os
import sys
from pathlib import Path
from urllib.parse import quote_plus

import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL


# ------------------------- CONFIGURATION -------------------------
INPUT_FILE = Path(os.getenv(
    "MINING_INPUT_FILE",
    "TP_Master2_Entrepots_Donnees_Mining_Finance_Education_Commerce_1000_lignes.xlsx"
))
OUTPUT_DIR = Path(os.getenv("MINING_OUTPUT_DIR", "output_mining"))
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

SQL_SCHEMA_DW = os.getenv("SQLSERVER_DW_SCHEMA", "dw")
SQL_SCHEMA_STG = os.getenv("SQLSERVER_STG_SCHEMA", "stg")
# Le DDL présenté précédemment utilise statut_engin. Si votre table utilise
# plutôt "statut", modifiez ENGINE_STATUS_COLUMN.
ENGINE_STATUS_COLUMN = os.getenv("ENGINE_STATUS_COLUMN", "statut_engin")

SERVER = os.getenv("SQLSERVER_SERVER", "localhost")
DATABASE = os.getenv("SQLSERVER_DATABASE", "DataWarehouseMining")
USERNAME = os.getenv("SQLSERVER_USERNAME", "")
PASSWORD = os.getenv("SQLSERVER_PASSWORD", "")
DRIVER = os.getenv("SQLSERVER_DRIVER", "ODBC Driver 18 for SQL Server")
TRUST_CERT = os.getenv("SQLSERVER_TRUST_CERT", "yes")


def make_engine():
    """Crée une connexion SQL Server via ODBC."""
    if USERNAME:
        connection_url = URL.create(
            "mssql+pyodbc",
            username=USERNAME,
            password=PASSWORD,
            host=SERVER,
            database=DATABASE,
            query={
                "driver": DRIVER,
                "TrustServerCertificate": TRUST_CERT,
                "Encrypt": "yes",
            },
        )
    else:
        # Authentification Windows intégrée (à exécuter sur Windows avec ODBC).
        odbc = (
            f"DRIVER={{{DRIVER}}};SERVER={SERVER};DATABASE={DATABASE};"
            "Trusted_Connection=yes;TrustServerCertificate=yes;"
        )
        connection_url = URL.create(
            "mssql+pyodbc", query={"odbc_connect": odbc}
        )
    return create_engine(connection_url, fast_executemany=True, pool_pre_ping=True)


def require_columns(df, columns, sheet_name):
    missing = sorted(set(columns) - set(df.columns))
    if missing:
        raise ValueError(
            f"Colonnes manquantes dans la feuille '{sheet_name}': {missing}. "
            f"Colonnes trouvées: {list(df.columns)}"
        )


def add_reason(df, mask, reason):
    mask = mask.fillna(True) if hasattr(mask, "fillna") else mask
    df.loc[mask, "motifs_rejet"] = df.loc[mask, "motifs_rejet"].apply(
        lambda old: f"{old}; {reason}".strip("; ")
    )


def load_source():
    if not INPUT_FILE.exists():
        raise FileNotFoundError(
            f"Fichier Excel introuvable : {INPUT_FILE.resolve()}\n"
            "Placez le fichier à côté du script ou définissez MINING_INPUT_FILE."
        )

    engins = pd.read_excel(INPUT_FILE, sheet_name="Mining_Engins")
    sites = pd.read_excel(INPUT_FILE, sheet_name="Mining_Sites")
    equipes = pd.read_excel(INPUT_FILE, sheet_name="Mining_Equipes")
    cycles = pd.read_excel(INPUT_FILE, sheet_name="Mining_Cycles_Bruts")

    require_columns(engins, ["code_engin", "modele", "fabricant",
                             "annee_mise_service", "statut"], "Mining_Engins")
    require_columns(sites, ["code_site", "nom_site", "zone"], "Mining_Sites")
    require_columns(equipes, ["code_equipe", "equipe", "heure_debut",
                              "heure_fin"], "Mining_Equipes")
    require_columns(cycles, ["code_cycle", "date_heure", "code_engin",
                              "site_chargement", "site_dechargement",
                              "code_equipe", "cycle_minutes", "payload_tonnes",
                              "nb_trajets", "statut"], "Mining_Cycles_Bruts")
    return engins, sites, equipes, cycles


def validate_cycles(engins, sites, equipes, cycles):
    """Valide les faits et sépare les lignes acceptées/rejetées."""
    df = cycles.copy()
    for col in ["code_cycle", "code_engin", "site_chargement",
                "site_dechargement", "code_equipe", "statut"]:
        df[col] = df[col].astype("string").str.strip()

    df["date_parsee"] = pd.to_datetime(df["date_heure"], errors="coerce")
    for col in ["cycle_minutes", "payload_tonnes", "nb_trajets"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df["motifs_rejet"] = ""
    add_reason(df, df["code_cycle"].isna() | df["code_cycle"].eq(""),
               "Code cycle manquant")
    add_reason(df, df["code_cycle"].duplicated(keep="first"),
               "Doublon code_cycle")
    add_reason(df, df["date_parsee"].isna(), "Date invalide")
    add_reason(df, ~df["code_engin"].isin(set(engins["code_engin"].astype(str))),
               "Engin inconnu")
    add_reason(df, ~df["site_chargement"].isin(set(sites["code_site"].astype(str))),
               "Site de chargement inconnu")
    add_reason(df, ~df["site_dechargement"].isin(set(sites["code_site"].astype(str))),
               "Site de déchargement inconnu")
    add_reason(df, ~df["code_equipe"].isin(set(equipes["code_equipe"].astype(str))),
               "Équipe inconnue")
    add_reason(df, df["cycle_minutes"].isna() |
               ((df["statut"] == "Terminé") & (df["cycle_minutes"] <= 0)),
               "Durée invalide")
    add_reason(df, df["payload_tonnes"].isna() | (df["payload_tonnes"] <= 0),
               "Payload invalide")
    add_reason(df, df["nb_trajets"].isna() | (df["nb_trajets"] < 1) |
               (df["nb_trajets"] % 1 != 0), "Nombre de trajets invalide")

    rejects = df[df["motifs_rejet"].ne("")].copy()
    valid = df[df["motifs_rejet"].eq("")].copy()
    return valid, rejects


def execute_many(conn, sql, rows):
    if rows:
        conn.execute(text(sql), rows)


def upsert_dimensions(engine, engins, sites, equipes, valid):
    """
    Insère les membres de dimensions qui n'existent pas encore.
    Suppose les colonnes de dimension conformes au DDL présenté.
    """
    with engine.begin() as conn:
        for _, r in engins.drop_duplicates("code_engin").iterrows():
            conn.execute(text(f"""
                IF NOT EXISTS (SELECT 1 FROM [{SQL_SCHEMA_DW}].[DimEngin]
                               WHERE code_engin = :code_engin)
                INSERT INTO [{SQL_SCHEMA_DW}].[DimEngin]
                    (code_engin, modele, fabricant, annee_mise_service, [{ENGINE_STATUS_COLUMN}])
                VALUES (:code_engin, :modele, :fabricant, :annee_mise_service, :statut)
            """), {
                "code_engin": str(r["code_engin"]),
                "modele": None if pd.isna(r["modele"]) else str(r["modele"]),
                "fabricant": None if pd.isna(r["fabricant"]) else str(r["fabricant"]),
                "annee_mise_service": None if pd.isna(r["annee_mise_service"]) else int(r["annee_mise_service"]),
                "statut": None if pd.isna(r["statut"]) else str(r["statut"]),
            })

        for _, r in sites.drop_duplicates("code_site").iterrows():
            conn.execute(text(f"""
                IF NOT EXISTS (SELECT 1 FROM [{SQL_SCHEMA_DW}].[DimSite]
                               WHERE code_site = :code_site)
                INSERT INTO [{SQL_SCHEMA_DW}].[DimSite] (code_site, nom_site, zone)
                VALUES (:code_site, :nom_site, :zone)
            """), {
                "code_site": str(r["code_site"]),
                "nom_site": None if pd.isna(r["nom_site"]) else str(r["nom_site"]),
                "zone": None if pd.isna(r["zone"]) else str(r["zone"]),
            })

        for _, r in equipes.drop_duplicates("code_equipe").iterrows():
            conn.execute(text(f"""
                IF NOT EXISTS (SELECT 1 FROM [{SQL_SCHEMA_DW}].[DimEquipe]
                               WHERE code_equipe = :code_equipe)
                INSERT INTO [{SQL_SCHEMA_DW}].[DimEquipe]
                    (code_equipe, equipe, heure_debut, heure_fin)
                VALUES (:code_equipe, :equipe, :heure_debut, :heure_fin)
            """), {
                "code_equipe": str(r["code_equipe"]),
                "equipe": None if pd.isna(r["equipe"]) else str(r["equipe"]),
                "heure_debut": None if pd.isna(r["heure_debut"]) else str(r["heure_debut"]),
                "heure_fin": None if pd.isna(r["heure_fin"]) else str(r["heure_fin"]),
            })

        # DimDate : une ligne par date présente dans les faits valides.
        dates = sorted(pd.Timestamp(x).normalize() for x in valid["date_parsee"].dropna().unique())
        for d in dates:
            date_value = d.date()
            date_key = int(d.strftime("%Y%m%d"))
            conn.execute(text(f"""
                IF NOT EXISTS (SELECT 1 FROM [{SQL_SCHEMA_DW}].[DimDate]
                               WHERE date_key = :date_key)
                INSERT INTO [{SQL_SCHEMA_DW}].[DimDate]
                    (date_key, date_complete, jour, mois, trimestre, annee, jour_semaine)
                VALUES (:date_key, :date_complete, :jour, :mois, :trimestre, :annee, :jour_semaine)
            """), {
                "date_key": date_key,
                "date_complete": date_value,
                "jour": int(d.day),
                "mois": int(d.month),
                "trimestre": int((d.month - 1) // 3 + 1),
                "annee": int(d.year),
                "jour_semaine": d.day_name(),
            })


def load_facts(engine, valid):
    """Résout les clés substituts puis insère les faits non encore chargés."""
    if valid.empty:
        return 0

    with engine.begin() as conn:
        dim_engin = pd.read_sql(text(
            f"SELECT engin_key, code_engin FROM [{SQL_SCHEMA_DW}].[DimEngin]"
        ), conn)
        dim_site = pd.read_sql(text(
            f"SELECT site_key, code_site FROM [{SQL_SCHEMA_DW}].[DimSite]"
        ), conn)
        dim_equipe = pd.read_sql(text(
            f"SELECT equipe_key, code_equipe FROM [{SQL_SCHEMA_DW}].[DimEquipe]"
        ), conn)
        dim_date = pd.read_sql(text(
            f"SELECT date_key, date_complete FROM [{SQL_SCHEMA_DW}].[DimDate]"
        ), conn)

        e_map = dict(zip(dim_engin["code_engin"].astype(str), dim_engin["engin_key"]))
        s_map = dict(zip(dim_site["code_site"].astype(str), dim_site["site_key"]))
        q_map = dict(zip(dim_equipe["code_equipe"].astype(str), dim_equipe["equipe_key"]))
        d_map = {
            pd.Timestamp(row.date_complete).date(): int(row.date_key)
            for row in dim_date.itertuples(index=False)
        }

        rows = []
        for _, r in valid.iterrows():
            dt = pd.Timestamp(r["date_parsee"])
            cycle_minutes = float(r["cycle_minutes"])
            payload = float(r["payload_tonnes"])
            nb_trajets = int(r["nb_trajets"])
            rows.append({
                "code_cycle": str(r["code_cycle"]),
                "date_key": d_map[dt.date()],
                "engin_key": int(e_map[str(r["code_engin"])]),
                "site_chargement_key": int(s_map[str(r["site_chargement"])]),
                "site_dechargement_key": int(s_map[str(r["site_dechargement"])]),
                "equipe_key": int(q_map[str(r["code_equipe"])]),
                "cycle_minutes": cycle_minutes,
                "cycle_hours": cycle_minutes / 60.0,
                "payload_tonnes": payload,
                "nb_trajets": nb_trajets,
                "tonnes_transportees": payload * nb_trajets,
                "statut": str(r["statut"]),
            })

        # Insert un par un avec NOT EXISTS pour éviter de recharger les mêmes cycles.
        insert_sql = text(f"""
            IF NOT EXISTS (SELECT 1 FROM [{SQL_SCHEMA_DW}].[FactCycleTransport]
                           WHERE code_cycle = :code_cycle)
            INSERT INTO [{SQL_SCHEMA_DW}].[FactCycleTransport]
                (code_cycle, date_key, engin_key, site_chargement_key,
                 site_dechargement_key, equipe_key, cycle_minutes, cycle_hours,
                 payload_tonnes, nb_trajets, tonnes_transportees, statut)
            VALUES (:code_cycle, :date_key, :engin_key, :site_chargement_key,
                    :site_dechargement_key, :equipe_key, :cycle_minutes, :cycle_hours,
                    :payload_tonnes, :nb_trajets, :tonnes_transportees, :statut)
        """)
        conn.execute(insert_sql, rows)

        count = conn.execute(text(
            f"SELECT COUNT(*) FROM [{SQL_SCHEMA_DW}].[FactCycleTransport]"
        )).scalar_one()
    return int(count)


def load_staging_and_rejects(engine, source, rejects):
    """Charge une copie source en staging et archive les rejets dans la table stg."""
    # to_sql remplace uniquement la table staging source ; elle est une zone de travail.
    raw = source.copy()
    raw["date_heure"] = raw["date_heure"].astype("string")
    raw.to_sql(
        "Mining_Cycles_Bruts", con=engine, schema=SQL_SCHEMA_STG,
        if_exists="replace", index=False, chunksize=500, method=None
    )

    if not rejects.empty:
        rej = rejects.copy()
        # Les colonnes date_parsee et motifs_rejet permettent l'audit.
        rej["date_parsee"] = rej["date_parsee"].astype("string")
        rej.to_sql(
            "Mining_Cycles_Rejets", con=engine, schema=SQL_SCHEMA_STG,
            if_exists="append", index=False, chunksize=500, method=None
        )


def main():
    print("1/5 - Lecture du fichier Excel...")
    engins, sites, equipes, source = load_source()
    print(f"Source : {len(source)} cycles")

    print("2/5 - Validation des données...")
    valid, rejects = validate_cycles(engins, sites, equipes, source)
    valid.to_csv(OUTPUT_DIR / "mining_cycles_valides.csv", index=False, encoding="utf-8-sig")
    rejects.to_csv(OUTPUT_DIR / "mining_rejets.csv", index=False, encoding="utf-8-sig")
    print(f"Valides : {len(valid)} | Rejetés : {len(rejects)}")

    print("3/5 - Connexion à SQL Server...")
    engine = make_engine()
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    print(f"Connexion réussie : {SERVER} / {DATABASE}")

    print("4/5 - Chargement staging et dimensions...")
    load_staging_and_rejects(engine, source, rejects)
    upsert_dimensions(engine, engins, sites, equipes, valid)

    print("5/5 - Chargement de la table de faits...")
    total_fact = load_facts(engine, valid)

    # Contrôle qualité final
    with engine.connect() as conn:
        source_count = len(source)
        fact_count = conn.execute(text(
            f"SELECT COUNT(*) FROM [{SQL_SCHEMA_DW}].[FactCycleTransport]"
        )).scalar_one()
        dim_dates = conn.execute(text(
            f"SELECT COUNT(*) FROM [{SQL_SCHEMA_DW}].[DimDate]"
        )).scalar_one()
    print("\nETL terminé.")
    print(f"Cycles dans le fichier : {source_count}")
    print(f"Lignes valides de ce lot : {len(valid)}")
    print(f"Lignes rejetées de ce lot : {len(rejects)}")
    print(f"Lignes dans FactCycleTransport (total) : {fact_count}")
    print(f"Dates dans DimDate (total) : {dim_dates}")
    print(f"Fichiers d'audit : {OUTPUT_DIR.resolve()}")

    engine.dispose()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"\nERREUR ETL : {exc}", file=sys.stderr)
        print("Vérifiez la connexion SQL Server, les tables et les noms de colonnes.", file=sys.stderr)
        raise
