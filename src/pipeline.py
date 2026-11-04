"""Create/update the 'pl_nyc_taxi_medallion' pipeline: Bronze -> Silver -> Gold notebooks.

Usage: python src/pipeline.py deploy
"""
import base64, json, sys
import fabric

PIPELINE_NAME = "pl_nyc_taxi_medallion"


def nb_activity(name, nb_id, ws_id, depends_on=None, params=True):
    a = {
        "name": name, "type": "TridentNotebook",
        "dependsOn": [{"activity": depends_on, "dependencyConditions": ["Succeeded"]}] if depends_on else [],
        "policy": {"timeout": "0.02:00:00", "retry": 2, "retryIntervalInSeconds": 120, "secureOutput": False, "secureInput": False},
        "typeProperties": {"notebookId": nb_id, "workspaceId": ws_id},
    }
    if params:
        a["typeProperties"]["parameters"] = {
            "year": {"value": {"value": "@pipeline().parameters.year", "type": "Expression"}, "type": "int"},
            "months": {"value": {"value": "@pipeline().parameters.months", "type": "Expression"}, "type": "string"},
        }
    return a


def deploy():
    ws_id, _ = fabric.ids()
    nbs = {n["displayName"]: n["id"] for n in fabric.call("GET", f"/workspaces/{ws_id}/notebooks")["value"]}
    content = {"properties": {
        "description": "NYC taxi medallion: ingest TLC files to Bronze, clean to Silver, aggregate to Gold. "
                       "Each notebook retries twice with a 2-minute back-off; downstream steps only run if upstream succeeded, "
                       "so Gold keeps serving the previous data when an upstream step fails.",
        "parameters": {"year": {"type": "int", "defaultValue": 2024},
                       "months": {"type": "string", "defaultValue": "1,2,3,4,5,6,7,8,9,10,11,12"}},
        "activities": [
            nb_activity("Bronze ingest", nbs["01_bronze_ingest"], ws_id),
            nb_activity("Silver transform", nbs["02_silver_transform"], ws_id, "Bronze ingest"),
            nb_activity("Gold aggregate", nbs["03_gold_aggregate"], ws_id, "Silver transform", params=False),
        ]}}
    definition = {"parts": [{"path": "pipeline-content.json", "payloadType": "InlineBase64",
                             "payload": base64.b64encode(json.dumps(content).encode()).decode()}]}
    existing = [p for p in fabric.call("GET", f"/workspaces/{ws_id}/dataPipelines")["value"] if p["displayName"] == PIPELINE_NAME]
    if existing:
        fabric.call("POST", f"/workspaces/{ws_id}/items/{existing[0]['id']}/updateDefinition", {"definition": definition}, want_result=False)
        print(f"updated pipeline {existing[0]['id']}")
    else:
        r = fabric.call("POST", f"/workspaces/{ws_id}/items",
                        {"displayName": PIPELINE_NAME, "type": "DataPipeline", "definition": definition})
        print(f"created pipeline {r.get('id')}")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "deploy":
        deploy()
    else:
        print(__doc__)
