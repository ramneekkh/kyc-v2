from dotenv import load_dotenv
load_dotenv()

import logging
import uuid
import os
import json
from datetime import datetime

try:
    from google.cloud import spanner
except ImportError:
    spanner = None

from ..storage.gcs_client import gcs_client

logger = logging.getLogger(__name__)

class SpannerClient:
    def __init__(self):
        self.project_id = os.getenv("GOOGLE_CLOUD_PROJECT")
        self.instance_id = os.getenv("SPANNER_INSTANCE")
        self.database_id = os.getenv("SPANNER_DATABASE")
        
        # Debug logging to identify missing config
        missing = []
        if not self.project_id: missing.append("GOOGLE_CLOUD_PROJECT")
        if not self.instance_id: missing.append("SPANNER_INSTANCE")
        if not self.database_id: missing.append("SPANNER_DATABASE")
        
        if missing:
            print(f"DEBUG: SpannerClient Init: Missing environment variables: {', '.join(missing)}")
            logger.warning(f"SpannerClient Init: Missing environment variables: {', '.join(missing)}")
        else:
            print(f"DEBUG: SpannerClient Init: Configured for {self.project_id}/{self.instance_id}/{self.database_id}")
            logger.info(f"SpannerClient Init: Configured for {self.project_id}/{self.instance_id}/{self.database_id}")

        self.client = None

        self.instance = None
        self.database = None
    
    def connect(self):
        """Lazy connection to ensure fork safety with Gunicorn."""
        if self.database:
            return

        if not (self.project_id and self.instance_id and self.database_id) or spanner is None:
            if spanner is None:
                pass # Already logged at top level or will log once
            else:
                 logger.warning("Spanner configuration missing. Persistence disabled.")
            return

        try:
            if not self.client:
                self.client = spanner.Client(project=self.project_id)
                self.instance = self.client.instance(self.instance_id)
                self.database = self.instance.database(self.database_id)
                logger.info("SpannerClient connected successfully.")
        except Exception as e:
            logger.error(f"Failed to connect SpannerClient: {e}")
            self.client = None

    def _get_timestamp(self):
        return datetime.utcnow()

    def save_subject(self, subject_id, name, profile_data):
        self.connect()
        if not self.database: return
        
        def trans_op(transaction):
            # Check if exists using Read API (Dialect Agnostic)
            keyset = spanner.KeySet(keys=[(subject_id,)])
            results = transaction.read(
                table="Subjects",
                columns=["SubjectId"],
                keyset=keyset
            )
            exists = False
            for _ in results: exists = True
            
            now = datetime.utcnow()
            
            if not exists:
                transaction.insert(
                    "Subjects",
                    columns=["SubjectId", "Name", "ProfileData", "Status", "CreatedAt", "UpdatedAt"],
                    values=[
                        (subject_id, name, json.dumps(profile_data), "ACTIVE", now, now)
                    ]
                )
            else:
                transaction.update(
                    "Subjects",
                    columns=["SubjectId", "ProfileData", "UpdatedAt"],
                    values=[(subject_id, json.dumps(profile_data), now)]
                )

        try:
            self.database.run_in_transaction(trans_op)
            logger.info(f"Subject {subject_id} saved.")
        except Exception as e:
            logger.error(f"Error saving subject: {e}")

    def update_subject_summary(self, subject_id, summary):
        self.connect()
        if not self.database: return
        
        try:
             def trans_op(transaction):
                 now = datetime.utcnow()
                 # We assume subject exists if we are updating summary
                 transaction.update(
                     "Subjects",
                     columns=["SubjectId", "Summary", "UpdatedAt"],
                     values=[(subject_id, summary, now)]
                 )
             self.database.run_in_transaction(trans_op)
             logger.info(f"Updated summary for subject {subject_id}.")
        except Exception as e:
            logger.error(f"Error updating subject summary: {e}")

    def save_findings_batch(self, subject_id, findings):
        self.connect()
        if not self.database: return

        # findings is a list of dicts
        rows = []
        now = datetime.utcnow()
        for f in findings:
            f_id = f.get("finding_id") or str(uuid.uuid4())
            
            # --- GCS Offloading ---
            gcs_content_uri = None
            gcs_json_uri = None
            
            try:
                # 1. Upload Full Content (Text)
                if f.get("full_content"):
                     blob_name_txt = f"findings/{subject_id}/{f_id}_content.txt"
                     gcs_content_uri = gcs_client.upload_string(f.get("full_content"), blob_name_txt, "text/plain")
                
                # 2. Upload Full JSON Data
                # Ensure we include everything
                blob_name_json = f"findings/{subject_id}/{f_id}.json"
                gcs_json_uri = gcs_client.upload_string(json.dumps(f, indent=2), blob_name_json, "application/json")
                
            except Exception as e:
                logger.error(f"Failed to offload data to GCS for finding {f_id}: {e}")
                # We continue to save to Spanner, but maybe with missing URIs

            rows.append((
                f_id,
                subject_id,
                f.get("link"),
                f.get("title"),
                f.get("snippet"),
                f.get("risk_level"),
                int(f.get("relevance_score", 0)),
                json.dumps(f), # Still keep JSON in Spanner, but it might be truncated by DB if huge? 
                               # Spanner JSON/JSONB limit is high (10MB/doc usu), so maybe OK. 
                               # But Findings table definition for RelevanceData is JSONB.
                f.get("full_content")[:2400000] if f.get("full_content") else None, # Truncate to ~2.4MB
                f.get("url_hash"),
                f.get("content_hash"),
                f.get("is_syndicated", False),
                f.get("parent_finding_id"),
                gcs_content_uri,
                gcs_json_uri,
                now
            ))
        
        try:
            with self.database.batch() as batch:
                batch.insert(
                    "Findings",
                    columns=[
                        "FindingId", "SubjectId", "Url", "Title", "Snippet", 
                        "RiskLevel", "Score", "RelevanceData", "FullContent", 
                        "UrlHash", "ContentHash", "IsSyndicated", "ParentFindingId", 
                        "GcsContentUri", "GcsJsonUri",
                        "CreatedAt"
                    ],
                    values=rows
                )
            logger.info(f"Saved {len(rows)} findings for subject {subject_id}.")
        except Exception as e:
            logger.error(f"Error saving findings batch: {e}")

    def get_existing_hashes_and_ids(self, subject_id):
        """Retrieves FindingId, UrlHash, ContentHash for deduplication."""
        self.connect()
        if not self.database: return {}, {}
        
        try:
            with self.database.snapshot() as snapshot:
                # Check dialect
                self.database.reload()
                dialect = self.database.database_dialect
                is_pg = False
                try:
                    is_pg = (dialect == 2) or "POSTGRESQL" in getattr(dialect, 'name', str(dialect)).upper()
                except:
                    is_pg = (dialect == 2)
                
                if is_pg:
                    query = "SELECT FindingId, UrlHash, ContentHash FROM Findings WHERE SubjectId = $1"
                    params = {"p1": subject_id}
                    p_types = {"p1": spanner.param_types.STRING}
                else:
                    query = "SELECT FindingId, UrlHash, ContentHash FROM Findings WHERE SubjectId = @subject_id"
                    params = {"subject_id": subject_id}
                    p_types = {"subject_id": spanner.param_types.STRING}

                results = snapshot.execute_sql(query, params=params, param_types=p_types)
                
                url_hashes = {}
                content_hashes = {}
                
                for row in results:
                    fid = row[0]
                    u_hash = row[1]
                    c_hash = row[2]
                    
                    if u_hash: url_hashes[u_hash] = fid
                    if c_hash: content_hashes[fid] = c_hash # Store content hash by FID for hydration
                    
                return url_hashes, content_hashes

        except Exception as e:
            logger.error(f"Error fetching existing hashes for {subject_id}: {e}")
            return {}, {}

    def save_graph_data(self, subject_id, nodes, edges):
        self.connect()
        if not self.database: return

        # Convert VisJS format to Database Schema
        
        node_rows = []
        now = datetime.utcnow()
        for n in nodes:
            # Sanitizing ID
            raw_id = str(n.get("id"))
            # Make a composite ID or Hash -> Must fit in 36 chars (UUID)
            # We use uuid5 to create a deterministic UUID from the composite string
            node_pk = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{subject_id}_{raw_id}"))
            
            node_rows.append((
                node_pk,
                subject_id,
                n.get("label"),
                n.get("group"), # Type
                json.dumps(n),
                now
            ))

        edge_rows = []
        for e in edges:
            # Edges source/target must match the hashed Node IDs
            from_pk = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{subject_id}_{str(e.get('from'))}"))
            to_pk = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{subject_id}_{str(e.get('to'))}"))
            edge_pk = str(uuid.uuid4()) # Edges are unique events usually
            
            edge_rows.append((
                edge_pk,
                from_pk,
                to_pk,
                e.get("label"),
                int(e.get("parsedYear")) if e.get("parsedYear") else None,
                json.dumps(e),
                now
            ))

        try:
            with self.database.batch() as batch:
                batch.insert_or_update(
                    "GraphNodes",
                    columns=["NodeId", "SubjectId", "Label", "Type", "Properties", "CreatedAt"],
                    values=node_rows
                )
                batch.insert_or_update(
                    "GraphEdges",
                    columns=["EdgeId", "SourceNodeId", "TargetNodeId", "Label", "Year", "Properties", "CreatedAt"],
                    values=edge_rows
                )
            logger.info(f"Saved graph data: {len(node_rows)} nodes, {len(edge_rows)} edges.")
        except Exception as e:
            logger.error(f"Error saving graph data: {e}")

    def get_all_subjects(self):
        self.connect()
        if not self.database: return []
        
        try:
            with self.database.snapshot() as snapshot:
                # Use Read API for dialect neutrality
                # Retrieve SubjectId, Name, CreatedAt, Status from Subjects
                results = snapshot.read(
                    table="Subjects",
                    columns=["SubjectId", "Name", "Status", "CreatedAt"],
                    keyset=spanner.KeySet(all_=True)
                )
                
                subjects = []
                for row in results:
                    subjects.append({
                        "subject_id": row[0],
                        "name": row[1],
                        "status": row[2],
                        "created_at": row[3].isoformat() if row[3] else None
                    })
                return subjects
        except Exception as e:
            logger.error(f"Error fetching subjects: {e}")
            return []

    def get_subject(self, subject_id):
        self.connect()
        if not self.database: return None
        
        try:
            with self.database.snapshot() as snapshot:
                results = snapshot.read(
                    table="Subjects",
                    columns=["SubjectId", "Name", "ProfileData", "Status", "Summary", "CreatedAt"],
                    keyset=spanner.KeySet(keys=[(subject_id,)])
                )
                for row in results:
                    profile_data = row[2] if row[2] else {}
                    if isinstance(profile_data, str):
                        try:
                            profile_data = json.loads(profile_data)
                        except json.JSONDecodeError:
                            logger.warning(f"Failed to decode ProfileData for {subject_id}")
                            profile_data = {}

                    return {
                        "subject_id": row[0],
                        "name": row[1],
                        "profile_data": profile_data,
                        "status": row[3],
                        "summary": row[4],
                        "created_at": row[5].isoformat() if row[5] else None
                    }
        except Exception as e:
            logger.error(f"Error fetching subject {subject_id}: {e}")
            # Re-raise to let the caller handle it or see the 500
            # For now, let's return None but log heavily. 
            # Actually, let's verify if it was a "Column not found" error.
            return None

    def get_findings(self, subject_id):
        self.connect()
        if not self.database: return []
        
        try:
            with self.database.snapshot() as snapshot:
                # We can't use Read API easily for "WHERE SubjectId=" without an index on SubjectId (if not part of PK)
                # But typically reading by partial key works if SubjectId is prefix of PK?
                # Finding PK is FindingId. SubjectId is secondary.
                # So we must use SQL.
                
                # Check dialect to form query
                self.database.reload()
                dialect = self.database.database_dialect
                is_pg = False
                try:
                    if isinstance(dialect, int): is_pg = (dialect == 2)
                    else: is_pg = "POSTGRESQL" in str(dialect).upper() or dialect == spanner.DatabaseDialect.POSTGRESQL
                except:
                    if hasattr(dialect, 'name'): is_pg = "POSTGRESQL" in dialect.name
                    else: is_pg = (dialect == 2)

                if is_pg:
                    query = "SELECT FindingId, RelevanceData, FullContent, CreatedAt FROM Findings WHERE SubjectId = $1"
                    params = {"p1": subject_id}
                    param_types = {"p1": spanner.param_types.STRING}
                else:
                    query = "SELECT FindingId, RelevanceData, FullContent, CreatedAt FROM Findings WHERE SubjectId = @subject_id"
                    params = {"subject_id": subject_id}
                    param_types = {"subject_id": spanner.param_types.STRING}

                results = snapshot.execute_sql(query, params=params, param_types=param_types)
                
                findings = []
                for row in results:
                    # RelevanceData contains the original full finding dictionary
                    relevance_data = row[1] if row[1] else {}
                    if isinstance(relevance_data, str):
                         try:
                             relevance_data = json.loads(relevance_data)
                         except:
                             relevance_data = {}
                    
                    finding_data = relevance_data
                    
                    # Merge with other columns if strictly needed, but finding_data has most.
                    # Ensure full_content is there (it might be in JSON or separate column)
                    # output of save_findings_batch puts json.dumps(f) in RelevanceData
                    # AND full_content in FullContent column.
                    # We prefer the column for full_content as it might be updated separately? 
                    # Or just add it.
                    finding_data['full_content'] = row[2]
                    finding_data['finding_id'] = row[0]
                    finding_data['created_at'] = row[3].isoformat() if row[3] else None
                    
                    findings.append(finding_data)
                return findings
        except Exception as e:
            logger.error(f"Error fetching findings for {subject_id}: {e}")
            return []

    def get_graph_data(self, subject_id):
        self.connect()
        if not self.database: return {"nodes": [], "edges": []}
        
        try:
            with self.database.snapshot(multi_use=True) as snapshot:
                # Retrieve Nodes
                # Manual SQL for dialect safety if needed, or Read
                # Using SQL for safer complex type handling if needed, but Read is fine for simple dumps
                
                # Check dialect again? Or assume standard scan. 
                # Let's use SQL for consistency with get_findings
                self.database.reload()
                dialect = self.database.database_dialect
                is_pg = False
                try:
                    if isinstance(dialect, int): is_pg = (dialect == 2)
                    else: is_pg = "POSTGRESQL" in str(dialect).upper() or dialect == spanner.DatabaseDialect.POSTGRESQL
                except:
                    if hasattr(dialect, 'name'): is_pg = "POSTGRESQL" in dialect.name
                    else: is_pg = (dialect == 2)
                
                if is_pg:
                    n_query = "SELECT NodeId, Label, Type, Properties, CreatedAt FROM GraphNodes WHERE SubjectId = $1"
                    e_query = "SELECT EdgeId, SourceNodeId, TargetNodeId, Label, Year, Properties, CreatedAt FROM GraphEdges WHERE SourceNodeId IN (SELECT NodeId FROM GraphNodes WHERE SubjectId = $1)"
                    # Note: Edge fetching strategy. We store edges with Source/Target IDs.
                    # Currently, our schema assumes Edge Source/Target logic is valid.
                    # But if we fetch edges, we must fetch all edges RELEVANT to the subject. The graph is centered on subject?
                    # Our current logic finds all edges originating from nodes belonging to the subject?
                    # Actually, our schema might share nodes? 
                    # "NodeId STRING(MAX), SubjectId STRING(MAX)..." -> Nodes are scoped to SubjectId in this simple design. OK.
                    params = {"p1": subject_id}
                    param_types = {"p1": spanner.param_types.STRING}
                else:
                    n_query = "SELECT NodeId, Label, Type, Properties, CreatedAt FROM GraphNodes WHERE SubjectId = @subject_id"
                    e_query = "SELECT EdgeId, SourceNodeId, TargetNodeId, Label, Year, Properties, CreatedAt FROM GraphEdges WHERE SourceNodeId IN (SELECT NodeId FROM GraphNodes WHERE SubjectId = @subject_id)"
                    params = {"subject_id": subject_id}
                    param_types = {"subject_id": spanner.param_types.STRING}

                n_res = snapshot.execute_sql(n_query, params=params, param_types=param_types)
                nodes = []
                for row in n_res:
                    n_props_raw = row[3] if row[3] else {}
                    if isinstance(n_props_raw, str):
                        try:
                             n_props = json.loads(n_props_raw)
                        except:
                             n_props = {}
                    else:
                        n_props = n_props_raw

                    # Reconstruct VisJS node
                    node = {
                        "id": n_props.get("id", row[1]), # Prefer original stored ID if available, else Label?
                        # Actually we constructed 'node_pk' in save_graph_data but stored json dump. 
                        # Use the JSON dump for full fidelity.
                        **n_props
                    }
                    # Ensure Label/Group are correct from columns just in case
                    node['label'] = row[1]
                    node['group'] = row[2]
                    nodes.append(node)

                # For edges, we need to be careful. In save_graph_data we saved them.
                # Query logic above tries to find edges where Source is in our nodes. 
                # Ideally we want edges connected to ANY of our nodes (Source OR Target).
                # But simple fetch is fine for now.
                
                # Let's just fetch Edges where SourceNodeId matches our nodes.
                # Or better: "WHERE SourceNodeId LIKE subject_id% OR TargetNodeId LIKE ..." if we used prefix keys.
                # We used f"{subject_id}_{raw_id}". 
                # So filtering by that prefix is safer? Spanner LIKE is expensive?
                # Let's stick to the subquery approach if supported.
                
                e_res = snapshot.execute_sql(e_query, params=params, param_types=param_types)
                edges = []
                for row in e_res:
                    e_props_raw = row[5] if row[5] else {}
                    if isinstance(e_props_raw, str):
                        try:
                            e_props = json.loads(e_props_raw)
                        except:
                            e_props = {}
                    else:
                        e_props = e_props_raw

                    edge = {
                         **e_props
                    }
                    edges.append(edge)
                    
                return {"nodes": nodes, "edges": edges}

        except Exception as e:
            logger.error(f"Error fetching graph for {subject_id}: {e}")
            return {"nodes": [], "edges": []}

    def get_subject_by_name(self, name):
        self.connect()
        if not self.database: return None
        
        try:
            with self.database.snapshot() as snapshot:
                # Naive implementation: Scan all subjects and filter by name. 
                # Ideally Name should be indexed or we use a Search index.
                # For small scale, a scan or "SELECT * FROM Subjects WHERE Name = @name" is okay.
                
                # Check dialect
                self.database.reload()
                dialect = self.database.database_dialect
                is_pg = False
                try:
                    is_pg = (dialect == 2) or "POSTGRESQL" in getattr(dialect, 'name', str(dialect)).upper()
                except:
                    is_pg = (dialect == 2)

                if is_pg:
                    query = "SELECT SubjectId, Name, ProfileData, Status, UpdatedAt, CreatedAt FROM Subjects WHERE Name = $1"
                    params = {"p1": name}
                    param_types = {"p1": spanner.param_types.STRING}
                else:
                    query = "SELECT SubjectId, Name, ProfileData, Status, UpdatedAt, CreatedAt FROM Subjects WHERE Name = @name"
                    params = {"name": name}
                    param_types = {"name": spanner.param_types.STRING}

                results = snapshot.execute_sql(query, params=params, param_types=param_types)
                
                for row in results:
                     # Return the first match
                     return {
                        "subject_id": row[0],
                        "name": row[1],
                        "status": row[3],
                        "updated_at": row[4].isoformat() if row[4] else (row[5].isoformat() if row[5] else None),
                        "created_at": row[5].isoformat() if row[5] else None
                     }
                return None
        except Exception as e:
            logger.error(f"Error fetching subject by name {name}: {e}")
            return None

    def get_latest_update_time(self, subject_id):
        """Returns the latest 'CreatedAt' from Findings or 'UpdatedAt' from Subject."""
        self.connect()
        if not self.database: return None
        
        try:
            # We need multi_use=True because we run two queries
            with self.database.snapshot(multi_use=True) as snapshot:
                # 1. Get Subject UpdatedAt
                # 2. Get Max Finding CreatedAt
                # Return max of both.
                
                # Dialect check skipped for brevity (assuming params generic or reused)
                # Just use simple SQL
                
                 # Check dialect
                self.database.reload()
                dialect = self.database.database_dialect
                is_pg = False
                try:
                    is_pg = (dialect == 2) or "POSTGRESQL" in getattr(dialect, 'name', str(dialect)).upper()
                except:
                    is_pg = (dialect == 2)
                    
                if is_pg:
                    q_sub = "SELECT UpdatedAt FROM Subjects WHERE SubjectId = $1"
                    q_find = "SELECT MAX(CreatedAt) FROM Findings WHERE SubjectId = $1"
                else:
                    q_sub = "SELECT UpdatedAt FROM Subjects WHERE SubjectId = @subject_id"
                    q_find = "SELECT MAX(CreatedAt) FROM Findings WHERE SubjectId = @subject_id"

                params = {"p1" if is_pg else "subject_id": subject_id}
                p_types = {"p1" if is_pg else "subject_id": spanner.param_types.STRING}
                
                updated_at = None
                
                # Subject time
                res_sub = snapshot.execute_sql(q_sub, params=params, param_types=p_types)
                for row in res_sub:
                    if row[0]: updated_at = row[0]
                    
                # Findings time
                res_find = snapshot.execute_sql(q_find, params=params, param_types=p_types)
                for row in res_find:
                    if row[0]: 
                        if updated_at is None or row[0] > updated_at:
                            updated_at = row[0]
                            
                return updated_at
        except Exception as e:
             logger.error(f"Error getting latest update time for {subject_id}: {e}")
             return None

    def get_all_finding_urls(self, subject_id):
        self.connect()
        if not self.database: return set()
        
        try:
            with self.database.snapshot() as snapshot:
                # Dialect check
                self.database.reload()
                dialect = self.database.database_dialect
                is_pg = False
                try:
                    is_pg = (dialect == 2) or "POSTGRESQL" in getattr(dialect, 'name', str(dialect)).upper()
                except:
                    is_pg = (dialect == 2)

                if is_pg:
                     query = "SELECT Url FROM Findings WHERE SubjectId = $1"
                     params = {"p1": subject_id}
                else:
                     query = "SELECT Url FROM Findings WHERE SubjectId = @subject_id"
                     params = {"subject_id": subject_id}
                
                p_types = {"p1" if is_pg else "subject_id": spanner.param_types.STRING}
                
                results = snapshot.execute_sql(query, params=params, param_types=p_types)
                urls = set()
                for row in results:
                    if row[0]:
                        urls.add(row[0])
                return urls
        except Exception as e:
            logger.error(f"Error fetching finding URLs for {subject_id}: {e}")
            return set()

# Global instance
spanner_client = SpannerClient()
