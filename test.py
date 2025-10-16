import requests

HOMESERVER = "https://kore.host:4433"
USERNAME = "@arxiv:kore.host"
PASSWORD = "*b4k)V**Mz]F?Rc"

login_url = f"{HOMESERVER}/_matrix/client/r0/login"

data = {"type": "m.login.password", "user": USERNAME, "password": PASSWORD}

resp = requests.post(login_url, json=data)
resp.raise_for_status()

access_token = resp.json()["access_token"]
print("Access Token:", access_token)
