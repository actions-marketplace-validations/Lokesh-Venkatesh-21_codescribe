import asyncio
import os

import httpx


async def main() -> None:
    base_url = os.getenv("CODESCRIBE_BASE_URL", "http://127.0.0.1:8000")
    payload = {
        "repo_full_name": "acme/widgets",
        "pr_number": 42,
        "head_sha": "abc123",
        "title": "Add widget pricing service",
        "author": "octocat",
        "files": [
            {
                "filename": "app/pricing.py",
                "status": "added",
                "patch": (
                    "@@\n"
                    "+class PricingService:\n"
                    "+    def quote(self, sku, quantity):\n"
                    "+        return quantity * 10\n"
                ),
                "additions": 3,
                "deletions": 0,
            }
        ],
    }
    async with httpx.AsyncClient(base_url=base_url, timeout=30) as client:
        response = await client.post("/api/v1/pull-requests/process", json=payload)
        response.raise_for_status()
        print(response.json())


if __name__ == "__main__":
    asyncio.run(main())
