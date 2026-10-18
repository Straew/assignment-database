-- =====================================================================
-- ReefWatch reporting schema - Azure SQL Database
-- Run once (Azure portal Query editor / SSMS / Azure Data Studio), or let
-- the app run it automatically:  python reefwatch_app.py setup
-- Safe to re-run: every object is created only if it does not exist.
-- =====================================================================
IF OBJECT_ID(N'dbo.DailySummary', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.DailySummary (
        summary_id            INT IDENTITY(1,1) NOT NULL
                              CONSTRAINT PK_DailySummary PRIMARY KEY,
        -- UNIQUE => the database itself prevents duplicate summaries for a date
        summary_date          DATE NOT NULL
                              CONSTRAINT UQ_DailySummary_Date UNIQUE,
        total_surveys         INT NOT NULL
                              CONSTRAINT CK_DailySummary_Surveys CHECK (total_surveys >= 0),
        total_survey_cost     DECIMAL(12,2) NOT NULL
                              CONSTRAINT CK_DailySummary_Cost CHECK (total_survey_cost >= 0),
        top_species           NVARCHAR(100) NULL,   -- most frequently observed species
        top_species_sightings INT NULL,             -- number of surveys it appeared in
        low_equipment_alerts  NVARCHAR(MAX) NULL,   -- "; "-separated alert text
        generated_at          DATETIME2(0) NOT NULL
                              CONSTRAINT DF_DailySummary_GeneratedAt DEFAULT SYSUTCDATETIME()
    );

    -- Head office question: "which species keep topping the daily summaries?"
    CREATE INDEX IX_DailySummary_TopSpecies
        ON dbo.DailySummary (top_species) WHERE top_species IS NOT NULL;
    -- (summary_date lookups are already served by the UNIQUE constraint's index)
END
