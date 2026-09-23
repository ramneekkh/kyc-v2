import os
import logging
try:
    from google.cloud import spanner
except ImportError:
    spanner = None

try:
    from google.api_core.exceptions import AlreadyExists, FailedPrecondition
except ImportError:
    AlreadyExists = Exception
    FailedPrecondition = Exception

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def get_spanner_instance():
    if spanner is None:
        return None
    project_id = os.getenv("GOOGLE_CLOUD_PROJECT")
    instance_id = os.getenv("SPANNER_INSTANCE")
    if not project_id or not instance_id:
        raise ValueError("Missing GOOGLE_CLOUD_PROJECT or SPANNER_INSTANCE env vars")
    
    spanner_client = spanner.Client(project=project_id)
    instance = spanner_client.instance(instance_id)
    return instance

def get_database():
    if spanner is None:
        return None
    database_id = os.getenv("SPANNER_DATABASE")
    if not database_id:
        raise ValueError("Missing SPANNER_DATABASE env var")
    
    instance = get_spanner_instance()
    if not instance:
        return None
    database = instance.database(database_id)
    return database

def apply_schema():
    database = get_database()
    
    # Reload database to get dialect
    database.reload()
    dialect = database.database_dialect
    logger.info(f"Detected Spanner Dialect: {dialect}")

    ddl_statements = []

    # Dialect 1 = GOOGLE_STANDARD_SQL
    # Dialect 2 = POSTGRESQL
    is_pg = False
    try:
        if isinstance(dialect, int):
             is_pg = (dialect == 2)
        else:
             is_pg = "POSTGRESQL" in str(dialect).upper() or dialect == spanner.DatabaseDialect.POSTGRESQL
    except:
        # Fallback if enum missing
        if hasattr(dialect, 'name'):
             is_pg = "POSTGRESQL" in dialect.name
        else:
             is_pg = (dialect == 2)

    if is_pg:
        logger.info("Using PostgreSQL DDL.")
        ddl_statements = [
            """CREATE TABLE Subjects (
                SubjectId varchar(36) NOT NULL PRIMARY KEY,
                UserId varchar(128) NOT NULL,
                Name text NOT NULL,
                ProfileData jsonb,
                Status varchar(20),
                CreatedAt timestamptz NOT NULL,
                UpdatedAt timestamptz
            )""",
            
            """CREATE TABLE Findings (
                FindingId varchar(36) NOT NULL PRIMARY KEY,
                SubjectId varchar(36) NOT NULL,
                Url text,
                Title text,
                Snippet text,
                RiskLevel varchar(20),
                Score bigint,
                RelevanceData jsonb,
                CreatedAt timestamptz NOT NULL,
                FOREIGN KEY (SubjectId) REFERENCES Subjects (SubjectId)
            )""",
            
            """CREATE TABLE GraphNodes (
                NodeId varchar(36) NOT NULL PRIMARY KEY,
                SubjectId varchar(36) NOT NULL,
                Label text,
                Type text,
                Properties jsonb,
                CreatedAt timestamptz NOT NULL,
                FOREIGN KEY (SubjectId) REFERENCES Subjects (SubjectId)
            )""",

            """CREATE TABLE GraphEdges (
                EdgeId varchar(36) NOT NULL PRIMARY KEY,
                SourceNodeId varchar(36) NOT NULL,
                TargetNodeId varchar(36) NOT NULL,
                Label text,
                Year bigint,
                Properties jsonb,
                CreatedAt timestamptz NOT NULL,
                FOREIGN KEY (SourceNodeId) REFERENCES GraphNodes (NodeId),
                FOREIGN KEY (TargetNodeId) REFERENCES GraphNodes (NodeId)
            )"""
        ]
    else:
        logger.info("Using GoogleSQL DDL.")
        ddl_statements = [
            """CREATE TABLE Subjects (
                SubjectId STRING(36) NOT NULL,
                UserId STRING(128) NOT NULL,
                Name STRING(MAX) NOT NULL,
                ProfileData JSON,
                Status STRING(20),
                CreatedAt TIMESTAMP NOT NULL,
                UpdatedAt TIMESTAMP
            ) PRIMARY KEY (SubjectId)""",

            """CREATE TABLE Findings (
                FindingId STRING(36) NOT NULL,
                SubjectId STRING(36) NOT NULL,
                Url STRING(MAX),
                Title STRING(MAX),
                Snippet STRING(MAX),
                RiskLevel STRING(20),
                Score INT64,
                RelevanceData JSON,
                CreatedAt TIMESTAMP NOT NULL,
                CONSTRAINT FK_Findings_Subjects FOREIGN KEY (SubjectId) REFERENCES Subjects (SubjectId)
            ) PRIMARY KEY (FindingId)""",

            """CREATE TABLE GraphNodes (
                NodeId STRING(36) NOT NULL,
                SubjectId STRING(36) NOT NULL,
                Label STRING(MAX),
                Type STRING(MAX),
                Properties JSON,
                CreatedAt TIMESTAMP NOT NULL,
                CONSTRAINT FK_Nodes_Subjects FOREIGN KEY (SubjectId) REFERENCES Subjects (SubjectId)
            ) PRIMARY KEY (NodeId)""",

            """CREATE TABLE GraphEdges (
                EdgeId STRING(36) NOT NULL,
                SourceNodeId STRING(36) NOT NULL,
                TargetNodeId STRING(36) NOT NULL,
                Label STRING(MAX),
                Year INT64,
                Properties JSON,
                CreatedAt TIMESTAMP NOT NULL,
                CONSTRAINT FK_Edges_Source FOREIGN KEY (SourceNodeId) REFERENCES GraphNodes (NodeId),
                CONSTRAINT FK_Edges_Target FOREIGN KEY (TargetNodeId) REFERENCES GraphNodes (NodeId)
            ) PRIMARY KEY (EdgeId)"""
        ]

    try:
        operation = database.update_ddl(ddl_statements)
        logger.info("Waiting for DDL update to complete...")
        operation.result(timeout=120)
        logger.info("Schema applied successfully.")
    except FailedPrecondition as e:
        logger.info(f"Schema update skipped/failed (Tables might already exist): {e}")
    except Exception as e:
        logger.error(f"Error applying schema: {e}")
        # Allow proceeding to migration steps
    
    # 2. Migration: additive columns
    logger.info("Running schema migration (additive columns)...")
    migration_statements = []
    if is_pg:
        migration_statements = [
            "ALTER TABLE Findings ADD COLUMN IF NOT EXISTS FullContent text",
            "ALTER TABLE Findings ADD COLUMN IF NOT EXISTS UrlHash text",
            "ALTER TABLE Findings ADD COLUMN IF NOT EXISTS ContentHash text",
            "ALTER TABLE Findings ADD COLUMN IF NOT EXISTS IsSyndicated boolean DEFAULT false",
            "ALTER TABLE Findings ADD COLUMN IF NOT EXISTS ParentFindingId text",
            "ALTER TABLE Findings ADD COLUMN IF NOT EXISTS GcsContentUri text",
            "ALTER TABLE Findings ADD COLUMN IF NOT EXISTS GcsJsonUri text",
            # Two-dimensional materiality (see backend/search/utils.py)
            "ALTER TABLE Findings ADD COLUMN IF NOT EXISTS IdentityConfidence bigint",
            "ALTER TABLE Findings ADD COLUMN IF NOT EXISTS RiskSeverity bigint",
            "ALTER TABLE Findings ADD COLUMN IF NOT EXISTS MatchStatus text",
            "ALTER TABLE Findings ADD COLUMN IF NOT EXISTS AnalysisStatus text",
            "ALTER TABLE Findings ADD COLUMN IF NOT EXISTS ContentAvailable boolean",
            # Detects an article edited in place under an unchanged URL
            "ALTER TABLE Findings ADD COLUMN IF NOT EXISTS ContentFingerprint text",
            "ALTER TABLE Findings ADD COLUMN IF NOT EXISTS ContentChangedAt timestamptz",
            "ALTER TABLE Subjects ADD COLUMN IF NOT EXISTS Summary text",
            "ALTER TABLE Subjects ADD COLUMN IF NOT EXISTS UserId varchar(128)",
            # Identity + audit
            "ALTER TABLE Subjects ADD COLUMN IF NOT EXISTS CustomerId varchar(128)",
            "ALTER TABLE Subjects ADD COLUMN IF NOT EXISTS TenantId varchar(128)",
            "ALTER TABLE Subjects ADD COLUMN IF NOT EXISTS KeyStrategy varchar(64)",
            # Summary regeneration debounce
            "ALTER TABLE Subjects ADD COLUMN IF NOT EXISTS SummaryStatus varchar(32)",
            "ALTER TABLE Subjects ADD COLUMN IF NOT EXISTS SummaryAttempts bigint DEFAULT 0",
            "ALTER TABLE Subjects ADD COLUMN IF NOT EXISTS LastSummaryAttemptAt timestamptz",
            # Retention: when this subject's findings become eligible for purge
            "ALTER TABLE Subjects ADD COLUMN IF NOT EXISTS RetainUntil timestamptz",
        ]
    else:
        # GoogleSQL does not support IF NOT EXISTS in ALTER TABLE usually, but we tolerate errors
        migration_statements = [
            "ALTER TABLE Findings ADD COLUMN FullContent STRING(MAX)",
            "ALTER TABLE Findings ADD COLUMN UrlHash STRING(MAX)",
            "ALTER TABLE Findings ADD COLUMN ContentHash STRING(MAX)",
            "ALTER TABLE Findings ADD COLUMN IsSyndicated BOOL",
            "ALTER TABLE Findings ADD COLUMN ParentFindingId STRING(36)",
            "ALTER TABLE Findings ADD COLUMN GcsContentUri STRING(MAX)",
            "ALTER TABLE Findings ADD COLUMN GcsJsonUri STRING(MAX)",
            "ALTER TABLE Findings ADD COLUMN IdentityConfidence INT64",
            "ALTER TABLE Findings ADD COLUMN RiskSeverity INT64",
            "ALTER TABLE Findings ADD COLUMN MatchStatus STRING(32)",
            "ALTER TABLE Findings ADD COLUMN AnalysisStatus STRING(32)",
            "ALTER TABLE Findings ADD COLUMN ContentAvailable BOOL",
            "ALTER TABLE Findings ADD COLUMN ContentFingerprint STRING(64)",
            "ALTER TABLE Findings ADD COLUMN ContentChangedAt TIMESTAMP",
            "ALTER TABLE Subjects ADD COLUMN Summary STRING(MAX)",
            "ALTER TABLE Subjects ADD COLUMN UserId STRING(128)",
            "ALTER TABLE Subjects ADD COLUMN CustomerId STRING(128)",
            "ALTER TABLE Subjects ADD COLUMN TenantId STRING(128)",
            "ALTER TABLE Subjects ADD COLUMN KeyStrategy STRING(64)",
            "ALTER TABLE Subjects ADD COLUMN SummaryStatus STRING(32)",
            "ALTER TABLE Subjects ADD COLUMN SummaryAttempts INT64",
            "ALTER TABLE Subjects ADD COLUMN LastSummaryAttemptAt TIMESTAMP",
            "ALTER TABLE Subjects ADD COLUMN RetainUntil TIMESTAMP",
        ]

    for stmt in migration_statements:
        try:
            op = database.update_ddl([stmt])
            op.result(timeout=120)
            logger.info(f"Executed: {stmt}")
        except Exception as e:
            if "Duplicate column" in str(e) or "already exists" in str(e):
                logger.info(f"Column already exists: {stmt}")
            else:
                logger.warning(f"Migration failed for {stmt}: {e}")

    # 3. Secondary indexes.
    #
    # Every query in spanner_client.py filters on SubjectId, yet SubjectId was not
    # the primary key of Findings, GraphNodes or GraphEdges and had no index. Each
    # "show me this subject's findings" therefore scanned the entire table, so read
    # latency grew with the size of the whole corpus rather than with the subject.
    # Subjects.Name is indexed because get_subject_by_name sits on the hot path.
    index_statements = [
        "CREATE INDEX IDX_Findings_SubjectId ON Findings (SubjectId)",
        "CREATE INDEX IDX_Findings_Subject_Url ON Findings (SubjectId, UrlHash)",
        "CREATE INDEX IDX_Findings_Subject_Created ON Findings (SubjectId, CreatedAt DESC)",
        "CREATE INDEX IDX_GraphNodes_SubjectId ON GraphNodes (SubjectId)",
        # GraphEdges has no SubjectId of its own -- an edge is scoped to a
        # subject transitively, via
        #   SourceNodeId IN (SELECT NodeId FROM GraphNodes WHERE SubjectId = ?)
        # so SourceNodeId is the column that subquery probes, and indexing a
        # SubjectId here simply fails: the column does not exist.
        "CREATE INDEX IDX_GraphEdges_SourceNodeId ON GraphEdges (SourceNodeId)",
        "CREATE INDEX IDX_Subjects_Name ON Subjects (Name)",
        "CREATE INDEX IDX_Subjects_CustomerId ON Subjects (CustomerId)",
    ]

    logger.info("Creating secondary indexes...")
    for stmt in index_statements:
        try:
            op = database.update_ddl([stmt])
            op.result(timeout=300)
            logger.info(f"Executed: {stmt}")
        except Exception as e:
            msg = str(e)
            if "already exists" in msg or "Duplicate name" in msg:
                logger.info(f"Index already exists: {stmt}")
            else:
                logger.warning(f"Index creation failed for {stmt}: {e}")

if __name__ == "__main__":
    # Load env vars manually if running as script (or assume environment is set)
    # For local dev, we might need python-dotenv, but user has sourced .env
    try:
        from dotenv import load_dotenv
        load_dotenv()
        apply_schema()
    except Exception as e:
        logger.error(f"Failed to initialize database: {e}")
