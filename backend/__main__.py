"""Production entrypoint: `python -m backend`.

Reads PORT from the environment directly, so it works on platforms that start the
process without a shell (where "$PORT" would otherwise reach uvicorn literally).
"""

import os

import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "backend.app:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8000")),
        proxy_headers=True,
        forwarded_allow_ips=os.getenv("FORWARDED_ALLOW_IPS", "*"),
    )
