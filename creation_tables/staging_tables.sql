
CREATE TABLE stg.Mining_Cycles_Bruts (
    code_cycle           NVARCHAR(30),
    date_heure           NVARCHAR(50),
    code_engin           NVARCHAR(20),
    site_chargement      NVARCHAR(20),
    site_dechargement    NVARCHAR(20),
    code_equipe          NVARCHAR(20),
    cycle_minutes        DECIMAL(10,2),
    payload_tonnes       DECIMAL(12,2),
    nb_trajets           INT,
    statut               NVARCHAR(20),
    date_chargement      DATETIME2 NOT NULL
        DEFAULT SYSDATETIME()
);
GO

CREATE TABLE stg.Mining_Cycles_Rejets (
    rejet_id             BIGINT IDENTITY(1,1) PRIMARY KEY,
    code_cycle           NVARCHAR(30),
    date_heure           NVARCHAR(50),
    code_engin           NVARCHAR(20),
    site_chargement      NVARCHAR(20),
    site_dechargement    NVARCHAR(20),
    code_equipe          NVARCHAR(20),
    cycle_minutes        DECIMAL(10,2),
    payload_tonnes       DECIMAL(12,2),
    nb_trajets           INT,
    statut               NVARCHAR(20),
    motifs_rejet         NVARCHAR(1000),
    date_rejet           DATETIME2 NOT NULL
        DEFAULT SYSDATETIME()
);
GO
