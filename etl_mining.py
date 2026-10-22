# ETL pédagogique Mining: Excel -> validation -> CSV
import pandas as pd
from pathlib import Path

INPUT = Path("TP_Master2_Entrepots_Donnees_Mining_Finance_Education_Commerce_1000_lignes.xlsx")
OUT = Path("output_mining"); OUT.mkdir(exist_ok=True)
engins = pd.read_excel(INPUT, sheet_name="Mining_Engins")
sites = pd.read_excel(INPUT, sheet_name="Mining_Sites")
equipes = pd.read_excel(INPUT, sheet_name="Mining_Equipes")
df = pd.read_excel(INPUT, sheet_name="Mining_Cycles_Bruts")
df["date_parsee"] = pd.to_datetime(df["date_heure"], errors="coerce")
for c in ["cycle_minutes", "payload_tonnes", "nb_trajets"]:
    df[c] = pd.to_numeric(df[c], errors="coerce")
df["motifs_rejet"] = ""

def add_reason(mask, reason):
    df.loc[mask, "motifs_rejet"] = df.loc[mask, "motifs_rejet"].apply(
        lambda old: f"{old}; {reason}".strip("; ")
    )

add_reason(df.duplicated("code_cycle", keep="first"), "Doublon code_cycle")
add_reason(df["date_parsee"].isna(), "Date invalide")
add_reason(~df["code_engin"].isin(set(engins.code_engin)), "Engin inconnu")
add_reason(~df["site_chargement"].isin(set(sites.code_site)), "Site chargement inconnu")
add_reason(~df["site_dechargement"].isin(set(sites.code_site)), "Site déchargement inconnu")
add_reason(~df["code_equipe"].isin(set(equipes.code_equipe)), "Equipe inconnue")
add_reason(df["cycle_minutes"].isna() | ((df["statut"]=="Terminé") & (df["cycle_minutes"]<=0)), "Durée invalide")
add_reason(df["payload_tonnes"].isna() | (df["payload_tonnes"]<=0), "Payload invalide")
add_reason(df["nb_trajets"].isna() | (df["nb_trajets"]<1), "Nombre trajets invalide")

rejects = df[df["motifs_rejet"]!=""].copy()
valid = df[df["motifs_rejet"]==""].copy()
rejects.to_csv(OUT/"mining_rejets.csv", index=False)
valid.to_csv(OUT/"mining_cycles_valides.csv", index=False)
engins.to_csv(OUT/"dim_engin.csv", index=False)
sites.to_csv(OUT/"dim_site.csv", index=False)
equipes.to_csv(OUT/"dim_equipe.csv", index=False)
print("Source:", len(df), "Valides:", len(valid), "Rejets:", len(rejects))
# Étape SQL Server: charger d'abord les dimensions, construire DimDate,
# résoudre les clés substituts puis charger FactCycleTransport.
