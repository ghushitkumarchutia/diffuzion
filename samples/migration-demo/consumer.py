import requests

def load_user(id, api_url):
    requests.post("https://api.diffuzion.example/v1/users", json={"name": "Ada", "status": "paused"})
    requests.get(f"https://api.diffuzion.example/v1/users/{id}")
    requests.get("/legacy")
    requests.get(api_url)
