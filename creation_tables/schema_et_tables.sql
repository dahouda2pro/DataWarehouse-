
/* =========================================================
   ENTREPOT DE DONNEES MINING
   SGBD : Microsoft SQL Server
   ========================================================= */

-- 1. Création du schéma
IF NOT EXISTS (
    SELECT 1 FROM sys.schemas WHERE name = 'dw'
)
    EXEC('CREATE SCHEMA dw');
GO

IF NOT EXISTS (
    SELECT 1 FROM sys.schemas WHERE name = 'stg'
)
    EXEC('CREATE SCHEMA stg');
GO

/* =========================================================
   2. DIMENSION DATE
   ========================================================= */
CREATE TABLE dw.DimDate (
    date_key       INT           NOT NULL PRIMARY KEY,
    date_complete  DATE          NOT NULL UNIQUE,
    jour           TINYINT       NOT NULL,
    mois           TINYINT       NOT NULL,
    nom_mois       NVARCHAR(20)  NOT NULL,
    trimestre      TINYINT       NOT NULL,
    annee          SMALLINT      NOT NULL,
    jour_semaine   NVARCHAR(20)  NOT NULL,

    CONSTRAINT CK_DimDate_Jour
        CHECK (jour BETWEEN 1 AND 31),
    CONSTRAINT CK_DimDate_Mois
        CHECK (mois BETWEEN 1 AND 12),
    CONSTRAINT CK_DimDate_Trimestre
        CHECK (trimestre BETWEEN 1 AND 4)
);
GO

/* =========================================================
   3. DIMENSION ENGIN
   ========================================================= */
CREATE TABLE dw.DimEngin (
    engin_key           INT IDENTITY(1,1) PRIMARY KEY,
    code_engin          NVARCHAR(20) NOT NULL UNIQUE,
    modele              NVARCHAR(50),
    fabricant           NVARCHAR(50),
    annee_mise_service  SMALLINT,
    statut_engin        NVARCHAR(30)
);
GO

/* =========================================================
   4. DIMENSION SITE
   Utilisee pour le chargement et le dechargement
   ========================================================= */
CREATE TABLE dw.DimSite (
    site_key  INT IDENTITY(1,1) PRIMARY KEY,
    code_site NVARCHAR(20) NOT NULL UNIQUE,
    nom_site  NVARCHAR(100) NOT NULL,
    zone      NVARCHAR(50)
);
GO

/* =========================================================
   5. DIMENSION EQUIPE
   ========================================================= */
CREATE TABLE dw.DimEquipe (
    equipe_key  INT IDENTITY(1,1) PRIMARY KEY,
    code_equipe NVARCHAR(20) NOT NULL UNIQUE,
    equipe      NVARCHAR(30),
    heure_debut TIME,
    heure_fin   TIME
);
GO

/* =========================================================
   6. TABLE DE FAITS : CYCLES DE TRANSPORT
   Grain : une ligne par cycle de transport
   ========================================================= */
CREATE TABLE dw.FactCycleTransport (
    cycle_id               BIGINT IDENTITY(1,1) PRIMARY KEY,
    code_cycle             NVARCHAR(30) NOT NULL UNIQUE,

    date_key               INT NOT NULL,
    engin_key              INT NOT NULL,
    site_chargement_key    INT NOT NULL,
    site_dechargement_key  INT NOT NULL,
    equipe_key             INT NOT NULL,

    cycle_minutes          DECIMAL(10,2) NOT NULL,
    cycle_hours            DECIMAL(10,4) NOT NULL,
    payload_tonnes         DECIMAL(12,2) NOT NULL,
    nb_trajets             INT NOT NULL,
    tonnes_transportees    DECIMAL(14,2) NOT NULL,

    statut                 NVARCHAR(20) NOT NULL,

    -- Relations avec les dimensions
    CONSTRAINT FK_Fact_Date
        FOREIGN KEY (date_key)
        REFERENCES dw.DimDate(date_key),

    CONSTRAINT FK_Fact_Engin
        FOREIGN KEY (engin_key)
        REFERENCES dw.DimEngin(engin_key),

    CONSTRAINT FK_Fact_SiteChargement
        FOREIGN KEY (site_chargement_key)
        REFERENCES dw.DimSite(site_key),

    CONSTRAINT FK_Fact_SiteDechargement
        FOREIGN KEY (site_dechargement_key)
        REFERENCES dw.DimSite(site_key),

    CONSTRAINT FK_Fact_Equipe
        FOREIGN KEY (equipe_key)
        REFERENCES dw.DimEquipe(equipe_key),

    -- Regles de validation
    CONSTRAINT CK_Fact_CycleMinutes
        CHECK (cycle_minutes > 0),

    CONSTRAINT CK_Fact_CycleHours
        CHECK (cycle_hours > 0),

    CONSTRAINT CK_Fact_Payload
        CHECK (payload_tonnes > 0),

    CONSTRAINT CK_Fact_NbTrajets
        CHECK (nb_trajets >= 1),

    CONSTRAINT CK_Fact_Statut
        CHECK (statut IN (N'Terminé', N'Annulé', N'En cours'))
);
GO

/* =========================================================
   7. INDEX POUR LES REQUETES ANALYTIQUES
   ========================================================= */
CREATE INDEX IX_FactCycle_Date
    ON dw.FactCycleTransport(date_key);

CREATE INDEX IX_FactCycle_Engin
    ON dw.FactCycleTransport(engin_key);

CREATE INDEX IX_FactCycle_Equipe
    ON dw.FactCycleTransport(equipe_key);

CREATE INDEX IX_FactCycle_SiteChargement
    ON dw.FactCycleTransport(site_chargement_key);

CREATE INDEX IX_FactCycle_Statut
    ON dw.FactCycleTransport(statut);
GO
