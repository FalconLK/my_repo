import orjson
import requests

BASE_URL = "http://localhost:9400"

def run_one_instance(instance_id: str, model_patch: str):
    response = requests.get(f"{BASE_URL}/run_one_instance", json={"instance_id": instance_id, "model_patch": model_patch})
    return response

def run_many_instances(instances: list[dict]):
    response = requests.get(f"{BASE_URL}/run_many_instances", json=instances)
    return response

if __name__ == "__main__":
    with open('tests/test_data/five_instances.jsonl', 'r') as f:
        dataset = {}
        for line in f:
            data = orjson.loads(line)
            data = {
                "instance_id": data['instance_id'],
                "model_patch": data['patch']
            }
            print(run_one_instance(data['instance_id'], data['model_patch']))
            dataset[data['instance_id']] = data['model_patch']
            
        result = run_many_instances(dataset)
        print(result.json())
    