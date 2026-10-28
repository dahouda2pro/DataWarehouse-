
import os
import sys
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL

load_dotenv()

# --------------------------------------------------
# 1. CONFIGURATION
# --------------------------------------------------

INPUT_FILE = Path(os.getenv(
    "MINING_INPUT_FILE",
    "TP_Master2_Entrepots_Donnees_Mining_Finance_Education_Commerce_1000_lignes.xlsx"
))

OUTPUT_DIR = Path(os.getenv("MINING_OUTPUT_DIR", "output_mining"))
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

HOST = os.getenv("MYSQL_HOST", "")
PORT = int(os.getenv("MYSQL_PORT", "3306"))
DATABASE = os.getenv("MYSQL_DATABASE", "defaultdb")
USERNAME = os.getenv("MYSQL_USER", "")
PASSWORD = os.getenv("MYSQL_PASSWORD", "")
SSL_CA = os.getenv("MYSQL_SSL_CA", "")


# --------------------------------------------------
# 2. CONNECTION TO AIVEN MYSQL
# --------------------------------------------------

def make_engine():
    if not all([HOST, DATABASE, USERNAME, PASSWORD]):
        raise ValueError(
            "Configure MYSQL_HOST, MYSQL_DATABASE, "
            "MYSQL_USER and MYSQL_PASSWORD in .env"
        )

    connect_args = {"connect_timeout": 15}

    # Prefer certificate verification with Aiven's CA.
    if SSL_CA:
        if not Path(SSL_CA).is_file():
            raise FileNotFoundError(
                f"MySQL CA certificate not found: {SSL_CA}"
            )
        connect_args["ssl"] = {
            "ca": SSL_CA,
            "check_hostname": True,
        }
    else:
        # TLS encryption without explicit CA verification.
        # Configure MYSQL_SSL_CA for stronger verification.
        connect_args["ssl"] = {}

    url = URL.create(
        "mysql+pymysql",
        username=USERNAME,
        password=PASSWORD,
        host=HOST,
        port=PORT,
        database=DATABASE,
        query={"charset": "utf8mb4"},
    )

    return create_engine(
        url,
        connect_args=connect_args,
        pool_pre_ping=True,
        pool_recycle=1800,
    )


# --------------------------------------------------
# 3. VALIDATE SOURCE STRUCTURE
# --------------------------------------------------

def require_columns(df, columns, sheet_name):
    missing = sorted(set(columns) - set(df.columns))

    if missing:
        raise ValueError(
            f"Missing columns in {sheet_name}: {missing}. "
            f"Available columns: {list(df.columns)}"
        )


def add_reason(df, mask, reason):
    mask = mask.fillna(True) if hasattr(mask, "fillna") else mask

    df.loc[mask, "motifs_rejet"] = df.loc[
        mask, "motifs_rejet"
    ].apply(lambda old: f"{old}; {reason}".strip("; "))


def load_source():
    if not INPUT_FILE.exists():
        raise FileNotFoundError(
            f"Excel file not found: {INPUT_FILE.resolve()}"
        )

    engins = pd.read_excel(
        INPUT_FILE, sheet_name="Mining_Engins"
    )
    sites = pd.read_excel(
        INPUT_FILE, sheet_name="Mining_Sites"
    )
    equipes = pd.read_excel(
        INPUT_FILE, sheet_name="Mining_Equipes"
    )
    cycles = pd.read_excel(
        INPUT_FILE, sheet_name="Mining_Cycles_Bruts"
    )

    require_columns(
        engins,
        ["code_engin", "modele", "fabricant",
         "annee_mise_service", "statut"],
        "Mining_Engins",
    )

    require_columns(
        sites,
        ["code_site", "nom_site", "zone"],
        "Mining_Sites",
    )

    require_columns(
        equipes,
        ["code_equipe", "equipe", "heure_debut", "heure_fin"],
        "Mining_Equipes",
    )

    require_columns(
        cycles,
        ["code_cycle", "date_heure", "code_engin",
         "site_chargement", "site_dechargement",
         "code_equipe", "cycle_minutes", "payload_tonnes",
         "nb_trajets", "statut"],
        "Mining_Cycles_Bruts",
    )

    return engins, sites, equipes, cycles


# --------------------------------------------------
# 4. DATA QUALITY VALIDATION
# --------------------------------------------------

def validate_cycles(engins, sites, equipes, cycles):
    df = cycles.copy()

    text_columns = [
        "code_cycle", "code_engin", "site_chargement",
        "site_dechargement", "code_equipe", "statut",
    ]

    for col in text_columns:
        df[col] = df[col].astype("string").str.strip()

    df["date_parsee"] = pd.to_datetime(
        df["date_heure"], errors="coerce"
    )

    for col in [
        "cycle_minutes", "payload_tonnes", "nb_trajets"
    ]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df["motifs_rejet"] = ""

    add_reason(
        df,
        df["code_cycle"].isna() | df["code_cycle"].eq(""),
        "Code cycle manquant",
    )

    add_reason(
        df,
        df["code_cycle"].duplicated(keep="first"),
        "Doublon code_cycle",
    )

    add_reason(
        df, df["date_parsee"].isna(), "Date invalide"
    )

    add_reason(
        df,
        ~df["code_engin"].isin(
            set(engins["code_engin"].astype(str))
        ),
        "Engin inconnu",
    )

    add_reason(
        df,
        ~df["site_chargement"].isin(
            set(sites["code_site"].astype(str))
        ),
        "Site de chargement inconnu",
    )

    add_reason(
        df,
        ~df["site_dechargement"].isin(
            set(sites["code_site"].astype(str))
        ),
        "Site de déchargement inconnu",
    )

    add_reason(
        df,
        ~df["code_equipe"].isin(
            set(equipes["code_equipe"].astype(str))
        ),
        "Équipe inconnue",
    )

    add_reason(
        df,
        df["cycle_minutes"].isna()
        | (
            (df["statut"] == "Terminé")
            & (df["cycle_minutes"] <= 0)
        ),
        "Durée invalide",
    )

    add_reason(
        df,
        df["payload_tonnes"].isna()
        | (df["payload_tonnes"] <= 0),
        "Payload invalide",
    )

    add_reason(
        df,
        df["nb_trajets"].isna()
        | (df["nb_trajets"] < 1)
        | (df["nb_trajets"] % 1 != 0),
        "Nombre de trajets invalide",
    )

    rejects = df[df["motifs_rejet"].ne("")].copy()
    valid = df[df["motifs_rejet"].eq("")].copy()

    return valid, rejects


# --------------------------------------------------
# 5. MYSQL TABLES
# --------------------------------------------------

def create_tables(engine):
    ddl = [
        """
        CREATE TABLE IF NOT EXISTS dw_DimEngin (
            engin_key BIGINT AUTO_INCREMENT PRIMARY KEY,
            code_engin VARCHAR(100) NOT NULL UNIQUE,
            modele VARCHAR(150),
            fabricant VARCHAR(150),
            annee_mise_service INT,
            statut_engin VARCHAR(100)
        ) ENGINE=InnoDB
        """,
        """
        CREATE TABLE IF NOT EXISTS dw_DimSite (
            site_key BIGINT AUTO_INCREMENT PRIMARY KEY,
            code_site VARCHAR(100) NOT NULL UNIQUE,
            nom_site VARCHAR(200),
            zone VARCHAR(100)
        ) ENGINE=InnoDB
        """,
        """
        CREATE TABLE IF NOT EXISTS dw_DimEquipe (
            equipe_key BIGINT AUTO_INCREMENT PRIMARY KEY,
            code_equipe VARCHAR(100) NOT NULL UNIQUE,
            equipe VARCHAR(150),
            heure_debut VARCHAR(30),
            heure_fin VARCHAR(30)
        ) ENGINE=InnoDB
        """,
        """
        CREATE TABLE IF NOT EXISTS dw_DimDate (
            date_key INT PRIMARY KEY,
            date_complete DATE NOT NULL UNIQUE,
            jour INT,
            mois INT,
            trimestre INT,
            annee INT,
            jour_semaine VARCHAR(20)
        ) ENGINE=InnoDB
        """,
        """
        CREATE TABLE IF NOT EXISTS dw_FactCycleTransport (
            fact_key BIGINT AUTO_INCREMENT PRIMARY KEY,
            code_cycle VARCHAR(100) NOT NULL UNIQUE,
            date_key INT NOT NULL,
            engin_key BIGINT NOT NULL,
            site_chargement_key BIGINT NOT NULL,
            site_dechargement_key BIGINT NOT NULL,
            equipe_key BIGINT NOT NULL,
            cycle_minutes DOUBLE NOT NULL,
            cycle_hours DOUBLE NOT NULL,
            payload_tonnes DOUBLE NOT NULL,
            nb_trajets INT NOT NULL,
            tonnes_transportees DOUBLE NOT NULL,
            statut VARCHAR(100),
            FOREIGN KEY (date_key)
                REFERENCES dw_DimDate(date_key),
            FOREIGN KEY (engin_key)
                REFERENCES dw_DimEngin(engin_key),
            FOREIGN KEY (site_chargement_key)
                REFERENCES dw_DimSite(site_key),
            FOREIGN KEY (site_dechargement_key)
                REFERENCES dw_DimSite(site_key),
            FOREIGN KEY (equipe_key)
                REFERENCES dw_DimEquipe(equipe_key)
        ) ENGINE=InnoDB
        """,
        """
        CREATE TABLE IF NOT EXISTS stg_Mining_Cycles_Rejets (
            reject_id BIGINT AUTO_INCREMENT PRIMARY KEY,
            code_cycle VARCHAR(100),
            date_heure VARCHAR(100),
            code_engin VARCHAR(100),
            site_chargement VARCHAR(100),
            site_dechargement VARCHAR(100),
            code_equipe VARCHAR(100),
            cycle_minutes DOUBLE,
            payload_tonnes DOUBLE,
            nb_trajets DOUBLE,
            statut VARCHAR(100),
            date_parsee VARCHAR(100),
            motifs_rejet TEXT,
            loaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        ) ENGINE=InnoDB
        """,
    ]

    with engine.begin() as conn:
        for statement in ddl:
            conn.execute(text(statement))


# --------------------------------------------------
# 6. LOAD DIMENSIONS
# --------------------------------------------------

def upsert_dimensions(engine, engins, sites, equipes, valid):
    with engine.begin() as conn:

        for _, r in engins.drop_duplicates(
            "code_engin"
        ).iterrows():
            conn.execute(text("""
                INSERT IGNORE INTO dw_DimEngin
                    (code_engin, modele, fabricant,
                     annee_mise_service, statut_engin)
                VALUES
                    (:code, :modele, :fabricant, :annee, :statut)
            """), {
                "code": str(r["code_engin"]),
                "modele": None if pd.isna(r["modele"])
                else str(r["modele"]),
                "fabricant": None if pd.isna(r["fabricant"])
                else str(r["fabricant"]),
                "annee": None
                if pd.isna(r["annee_mise_service"])
                else int(r["annee_mise_service"]),
                "statut": None if pd.isna(r["statut"])
                else str(r["statut"]),
            })

        for _, r in sites.drop_duplicates(
            "code_site"
        ).iterrows():
            conn.execute(text("""
                INSERT IGNORE INTO dw_DimSite
                    (code_site, nom_site, zone)
                VALUES (:code, :nom, :zone)
            """), {
                "code": str(r["code_site"]),
                "nom": None if pd.isna(r["nom_site"])
                else str(r["nom_site"]),
                "zone": None if pd.isna(r["zone"])
                else str(r["zone"]),
            })

        for _, r in equipes.drop_duplicates(
            "code_equipe"
        ).iterrows():
            conn.execute(text("""
                INSERT IGNORE INTO dw_DimEquipe
                    (code_equipe, equipe, heure_debut, heure_fin)
                VALUES (:code, :equipe, :debut, :fin)
            """), {
                "code": str(r["code_equipe"]),
                "equipe": None if pd.isna(r["equipe"])
                else str(r["equipe"]),
                "debut": None if pd.isna(r["heure_debut"])
                else str(r["heure_debut"]),
                "fin": None if pd.isna(r["heure_fin"])
                else str(r["heure_fin"]),
            })

        dates = sorted(
            pd.Timestamp(x).normalize()
            for x in valid["date_parsee"].dropna().unique()
        )

        for d in dates:
            conn.execute(text("""
                INSERT IGNORE INTO dw_DimDate
                    (date_key, date_complete, jour, mois,
                     trimestre, annee, jour_semaine)
                VALUES
                    (:key, :date, :jour, :mois,
                     :trimestre, :annee, :semaine)
            """), {
                "key": int(d.strftime("%Y%m%d")),
                "date": d.date(),
                "jour": int(d.day),
                "mois": int(d.month),
                "trimestre": int((d.month - 1) // 3 + 1),
                "annee": int(d.year),
                "semaine": d.day_name(),
            })


# --------------------------------------------------
# 7. LOAD FACT TABLE
# --------------------------------------------------

def load_facts(engine, valid):
    if valid.empty:
        return 0

    with engine.begin() as conn:
        dim_engin = pd.read_sql(
            text("SELECT engin_key, code_engin FROM dw_DimEngin"),
            conn,
        )
        dim_site = pd.read_sql(
            text("SELECT site_key, code_site FROM dw_DimSite"),
            conn,
        )
        dim_equipe = pd.read_sql(
            text("SELECT equipe_key, code_equipe FROM dw_DimEquipe"),
            conn,
        )
        dim_date = pd.read_sql(
            text("SELECT date_key, date_complete FROM dw_DimDate"),
            conn,
        )

        e_map = dict(zip(
            dim_engin["code_engin"].astype(str),
            dim_engin["engin_key"],
        ))
        s_map = dict(zip(
            dim_site["code_site"].astype(str),
            dim_site["site_key"],
        ))
        q_map = dict(zip(
            dim_equipe["code_equipe"].astype(str),
            dim_equipe["equipe_key"],
        ))
        d_map = {
            pd.Timestamp(row.date_complete).date(): int(row.date_key)
            for row in dim_date.itertuples(index=False)
        }

        rows = []

        for _, r in valid.iterrows():
            dt = pd.Timestamp(r["date_parsee"])
            minutes = float(r["cycle_minutes"])
            payload = float(r["payload_tonnes"])
            trips = int(r["nb_trajets"])

            rows.append({
                "code_cycle": str(r["code_cycle"]),
                "date_key": d_map[dt.date()],
                "engin_key": int(e_map[str(r["code_engin"])]),
                "chargement_key": int(
                    s_map[str(r["site_chargement"])]
                ),
                "dechargement_key": int(
                    s_map[str(r["site_dechargement"])]
                ),
                "equipe_key": int(q_map[str(r["code_equipe"])]),
                "minutes": minutes,
                "hours": minutes / 60,
                "payload": payload,
                "trips": trips,
                "tonnes": payload * trips,
                "statut": str(r["statut"]),
            })

        conn.execute(text("""
            INSERT IGNORE INTO dw_FactCycleTransport
                (code_cycle, date_key, engin_key,
                 site_chargement_key, site_dechargement_key,
                 equipe_key, cycle_minutes, cycle_hours,
                 payload_tonnes, nb_trajets,
                 tonnes_transportees, statut)
            VALUES
                (:code_cycle, :date_key, :engin_key,
                 :chargement_key, :dechargement_key,
                 :equipe_key, :minutes, :hours,
                 :payload, :trips, :tonnes, :statut)
        """), rows)

        return conn.execute(text(
            "SELECT COUNT(*) FROM dw_FactCycleTransport"
        )).scalar_one()


# --------------------------------------------------
# 8. STAGING AND REJECTED RECORDS
# --------------------------------------------------


def load_staging_and_rejects(engine, source, rejects):
    """Load staging data and archive rejected records in Aiven MySQL."""

    with engine.begin() as conn:
        # Create staging table with a primary key
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS stg_Mining_Cycles_Bruts (
                staging_id BIGINT AUTO_INCREMENT PRIMARY KEY,
                code_cycle VARCHAR(100),
                date_heure VARCHAR(100),
                code_engin VARCHAR(100),
                site_chargement VARCHAR(100),
                site_dechargement VARCHAR(100),
                code_equipe VARCHAR(100),
                cycle_minutes DOUBLE,
                payload_tonnes DOUBLE,
                nb_trajets BIGINT,
                statut VARCHAR(100)
            ) ENGINE=InnoDB
        """))

        # Refresh staging data without dropping the table
        conn.execute(text("DELETE FROM stg_Mining_Cycles_Bruts"))

    # Insert rows into the existing table.
    # Pandas does not recreate the table when if_exists="append".
    raw = source.copy()
    raw["date_heure"] = raw["date_heure"].astype("string")
    raw = raw.where(pd.notna(raw), None)

    raw.to_sql(
        "stg_Mining_Cycles_Bruts",
        con=engine,
        if_exists="append",
        index=False,
        chunksize=500,
    )

    # Archive rejected rows; the rejection table already has a primary key.
    if not rejects.empty:
        rej = rejects.copy()
        rej["date_parsee"] = rej["date_parsee"].astype("string")

        columns = [
            "code_cycle", "date_heure", "code_engin",
            "site_chargement", "site_dechargement",
            "code_equipe", "cycle_minutes", "payload_tonnes",
            "nb_trajets", "statut", "date_parsee", "motifs_rejet",
        ]

        rej = rej[columns].copy()
        rej = rej.astype(object).where(pd.notna(rej), None)

        rej.to_sql(
            "stg_Mining_Cycles_Rejets",
            con=engine,
            if_exists="append",
            index=False,
            chunksize=500,
        )



# --------------------------------------------------
# 9. MAIN ETL
# --------------------------------------------------

def main():
    print("1/5 - Reading Excel...")
    engins, sites, equipes, source = load_source()
    print(f"Source cycles: {len(source)}")

    print("2/5 - Validating data...")
    valid, rejects = validate_cycles(
        engins, sites, equipes, source
    )

    valid.to_csv(
        OUTPUT_DIR / "mining_cycles_valides.csv",
        index=False,
        encoding="utf-8-sig",
    )
    rejects.to_csv(
        OUTPUT_DIR / "mining_rejets.csv",
        index=False,
        encoding="utf-8-sig",
    )

    print(f"Valid: {len(valid)} | Rejected: {len(rejects)}")

    print("3/5 - Connecting to Aiven MySQL...")
    engine = make_engine()

    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))

        print(f"Connected to {HOST}:{PORT}/{DATABASE}")

        print("4/5 - Creating tables and loading staging...")
        create_tables(engine)
        load_staging_and_rejects(engine, source, rejects)
        upsert_dimensions(
            engine, engins, sites, equipes, valid
        )

        print("5/5 - Loading fact table...")
        fact_count = load_facts(engine, valid)

        with engine.connect() as conn:
            staging_count = conn.execute(text(
                "SELECT COUNT(*) FROM stg_Mining_Cycles_Bruts"
            )).scalar_one()

            reject_count = conn.execute(text(
                "SELECT COUNT(*) FROM stg_Mining_Cycles_Rejets"
            )).scalar_one()

            date_count = conn.execute(text(
                "SELECT COUNT(*) FROM dw_DimDate"
            )).scalar_one()

        print("\nETL completed successfully.")
        print(f"Source rows: {len(source)}")
        print(f"Valid rows in this batch: {len(valid)}")
        print(f"Rejected rows in this batch: {len(rejects)}")
        print(f"Staging rows: {staging_count}")
        print(f"Fact rows (total): {fact_count}")
        print(f"Date dimension rows: {date_count}")
        print(f"Rejected records archived (total): {reject_count}")
        print(f"Audit files: {OUTPUT_DIR.resolve()}")

    finally:
        engine.dispose()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"\nETL ERROR: {exc}", file=sys.stderr)
        print(
            "Check Aiven credentials, TLS certificate, "
            "Excel sheet names and column names.",
            file=sys.stderr,
        )
        raise
