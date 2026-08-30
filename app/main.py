from fastapi import FastAPI

app = FastAPI(title="Identity Verification Engine (PoC)")


@app.get("/health")
def health():
    return {"status": "ok"}
