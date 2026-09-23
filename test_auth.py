from google.cloud import spanner
import os

project_id = "turing-striker-452905-u3"
instance_id = "kyc123"
database_id = "kycdb"

print(f"Testing Spanner Auth for {project_id}...")

try:
    client = spanner.Client(project=project_id)
    instance = client.instance(instance_id)
    database = instance.database(database_id)
    
    with database.snapshot() as snapshot:
        results = snapshot.execute_sql("SELECT 1")
        for row in results:
            print("SUCCESS: Spanner Connection OK. Result:", row)
except Exception as e:
    print("FAILURE: Auth Test Failed.")
    print(e)
