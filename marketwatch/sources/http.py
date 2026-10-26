import httpx

USER_AGENT = "marketwatch/0.1 (personal market brief)"


def client(timeout: float = 20.0) -> httpx.Client:
    return httpx.Client(timeout=timeout, headers={"User-Agent": USER_AGENT}, follow_redirects=True)


def get_json(url: str, params: dict | None = None, timeout: float = 20.0):
    with client(timeout) as c:
        r = c.get(url, params=params)
        r.raise_for_status()
        return r.json()
