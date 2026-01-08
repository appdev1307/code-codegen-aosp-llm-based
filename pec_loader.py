import json

def load_spec(path="artifact_spec.json"):
    with open(path) as f:
        return json.load(f)
